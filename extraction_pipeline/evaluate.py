"""
Cell-by-cell evaluation: compare LLM extraction vs Callum's ground truth.

Usage: python3 evaluate.py [paper_name] [--c3 | --no-c3]
       python3 evaluate.py "Cimino 2019"
       python3 evaluate.py                  # all papers with extracted JSON files
       python3 evaluate.py --ablation       # C3 ablation (two independent factors)
       python3 evaluate.py --protocol-audit # effect of each protocol fix on the scores
       python3 evaluate.py --legacy         # reproduce the pre-audit (v1) protocol

--c3 enables composition-aware catalyst normalisation (Contribution C3);
without it, catalyst names are compared as strings (the V1 baseline).

═══════════════════════════════════════════════════════════════════════════════
EVALUATION PROTOCOL — v2 (post-audit)
═══════════════════════════════════════════════════════════════════════════════
The v1 protocol scored 0.83 corpus F1. An audit found four methodological
defects that made that number optimistic, plus two reporting errors. Each fix is
implemented behind its own switch in `Protocol` so that `--protocol-audit` can
report how much of the score change each one accounts for. `--legacy` restores
all six defects and reproduces the v1 numbers exactly, as a regression check.

  FIX 1  Dimension-aware tolerances (was: flat 5% relative on every column).
         A 5% relative band on a Kelvin temperature is ±31 K at 623 K, three
         times looser than the ±10 °C the supervisor specified, so 623 K and
         653 K scored as equal. Relative bands are also unusable on percentage
         columns, where they collapse to zero width as the value approaches
         zero. See TOLERANCES below for the per-column table and its rationale.

  FIX 2  Unit normalisation before comparison (was: none — see units.py).
         "10 Mpa" == "10 MPa" held only because both strings were lower-cased;
         "1 bar" != "101 kPa" although they are the same pressure. Pressures are
         now canonicalised to kPa and temperatures to K before comparison. The
         unit layer lives in units.py so the extraction pipeline can reuse it;
         it is the second half of Contribution C3 (normalise.py resolves
         material identity, units.py resolves numeric meaning).

  FIX 3  Interval semantics (was: none).
         A predicted "29-46" against a GT of "46" scored FP *and* FN — the same
         cell penalised twice. A point falling inside a predicted interval now
         counts as a hit, but is tallied separately (the `rng` column) because
         it is weaker evidence than having produced the point outright.

  FIX 4a Row-alignment floor (was: best_score initialised to -1).
         Any unused prediction row could be matched to a GT row even with zero
         cells in common, because -1 is below every achievable score. A match
         now requires at least MIN_ROW_OVERLAP cells of informative agreement.

  FIX 4b Unmatched prediction rows are penalised (was: silently discarded).
         The corpus predicts 34 rows against 21 GT rows; the 13 surplus rows
         carried no cost, so the protocol had no row-level precision term at
         all. Every non-empty cell of an unmatched prediction row now counts as
         a false positive, tallied separately as `fp_un` so the two sources of
         imprecision stay distinguishable in the write-up.

  FIX 5  The `paper` column is no longer scored.
         It is the join key, not an extracted quantity. Scoring it donated one
         guaranteed TP per matched row (21 free TPs on 215 cells) and inflated
         the micro-average.

  FIX 6  Reporting: the --ablation column headed "R_text" printed F1, not
         recall. Both are now printed, under correct headers.
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import csv
import sys
from pathlib import Path

import units
from normalise import catalyst_match as c3_catalyst_match

PIPELINE_DIR = Path(__file__).parent
# PIPELINE_GT overrides the reference table (August 2026: patent_ground_truth.csv
# for the patent sub-corpus). Default unchanged: journal ground_truth.csv.
import os as _os
GT_CSV = Path(_os.environ.get("PIPELINE_GT", str(PIPELINE_DIR / "ground_truth.csv")))

# Where the predictions to be scored live. Predictions are no longer kept beside
# this script: reproducible extractor output is written per run by pipeline.py
# into runs/<run_id>/, and the earlier hand-written JSONs are preserved under
# manual_ceiling/ as an informed-reader reference line. Set with --pred-dir=PATH
# so the same protocol can score any of them.
PRED_DIR = PIPELINE_DIR
# Optional filter for run directories, whose files are named
# <slug>_<version>_r<rep>_extracted.json rather than <slug>_extracted.json.
PRED_VARIANT = None

# Columns as they appear in the CSV / JSON. `paper` is carried for joining and
# reporting but excluded from scoring (FIX 5).
ALL_COLS = [
    "paper", "catalyst", "loading_g", "temperature_K", "gas_mix",
    "flow_rate_mL_min", "LHSV_h", "WHSV_h", "GHSV_h",
    "pressure", "conversion_pct", "selectivity_pct",
]
SCORE_COLS = [c for c in ALL_COLS if c != "paper"]

# ── FIX 1: per-column tolerance table ───────────────────────────────────────
# ("abs", x) = tolerance of x in the column's canonical unit.
# ("rel", f) = tolerance of f as a fraction of the ground-truth value.
# Columns absent from this table are compared as strings (catalyst, gas_mix).
TOLERANCES = {
    # ±10 K absolute, matching the supervisor's ±10 °C guidance. Must be
    # absolute: a relative band on an absolute temperature scale is arbitrary,
    # and at 623 K a 5% band was ±31 K.
    "temperature_K":    ("abs", 10.0),
    # Space velocities and flows: reported to ~2 significant figures across the
    # corpus and often derived from feed/mass ratios, so a proportional band is
    # the right shape. 10% covers rounding in the source without merging
    # genuinely different operating points.
    "flow_rate_mL_min": ("rel", 0.10),
    "LHSV_h":           ("rel", 0.10),
    "WHSV_h":           ("rel", 0.10),
    "GHSV_h":           ("rel", 0.10),
    # Pressure: tight relative band, meaningful only after unit normalisation
    # to kPa (FIX 2) — without it this tolerance compares numerals, not
    # pressures. 5% accommodates 1 atm vs 101 kPa vs 0.1 MPa.
    "pressure":         ("rel", 0.05),
    # Percentages: ±2 percentage points, absolute. A relative band is unusable
    # here — at a GT of 0.2% selectivity, 5% relative is ±0.01 pp, so any
    # plausible reading fails; at 80% conversion it is ±4 pp, which is looser
    # than the reading error it is meant to absorb.
    "conversion_pct":   ("abs", 2.0),
    "selectivity_pct":  ("abs", 2.0),
    # Catalyst mass: reported to 1-2 s.f.; proportional band.
    "loading_g":        ("rel", 0.05),
}

# ── FIX 2: which columns carry a physical dimension needing conversion ──────
COL_DIMENSION = {
    "pressure": "pressure",
    "temperature_K": "temperature",
}

# ── FIX 4a: minimum informative cell agreement for a row to be a match ──────
MIN_ROW_OVERLAP = 2

_NULL_TOKENS = {"", "none", "null", "nan", "n/a"}


class Protocol:
    """Evaluation-protocol configuration.

    Every audit fix is a separate switch so `--protocol-audit` can attribute the
    score change to individual fixes rather than reporting one opaque delta.
    `Protocol.legacy()` restores the pre-audit v1 behaviour.
    """

    def __init__(self, dim_tolerance=True, unit_norm=True, interval=True,
                 min_overlap=MIN_ROW_OVERLAP, penalise_unmatched=True,
                 score_paper=False, c3_match=False, c3_gate=False):
        self.dim_tolerance = dim_tolerance          # FIX 1
        self.unit_norm = unit_norm                  # FIX 2
        self.interval = interval                    # FIX 3
        self.min_overlap = min_overlap              # FIX 4a
        self.penalise_unmatched = penalise_unmatched  # FIX 4b
        self.score_paper = score_paper              # FIX 5 (True = v1 defect)
        # C3 is now two independent factors, not one switch (see print_ablation).
        self.c3_match = c3_match    # composition-aware catalyst CELL comparison
        self.c3_gate = c3_gate      # composition gate on ROW alignment

    @classmethod
    def legacy(cls, c3_match=False, c3_gate=False):
        """The v1 protocol, defects included. Used as the regression baseline."""
        return cls(dim_tolerance=False, unit_norm=False, interval=False,
                   min_overlap=0, penalise_unmatched=False, score_paper=True,
                   c3_match=c3_match, c3_gate=c3_gate)

    def cols(self):
        return ALL_COLS if self.score_paper else SCORE_COLS

    def copy(self, **kw):
        p = Protocol(self.dim_tolerance, self.unit_norm, self.interval,
                     self.min_overlap, self.penalise_unmatched, self.score_paper,
                     self.c3_match, self.c3_gate)
        for k, v in kw.items():
            setattr(p, k, v)
        return p


def load_gt(paper_filter=None) -> list[dict]:
    rows = []
    with open(GT_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("paper") and (paper_filter is None or r["paper"] == paper_filter):
                rows.append({k: (r[k] if r[k] not in ("", "None") else None) for k in ALL_COLS})
    return rows


def pred_files() -> list[Path]:
    """Prediction JSONs in PRED_DIR, optionally filtered to one variant."""
    files = sorted(PRED_DIR.glob("*_extracted.json"))
    if PRED_VARIANT:
        files = [f for f in files if f"_{PRED_VARIANT}_extracted.json" in f.name]
    return files


def find_pred_file(paper_slug: str):
    """Locate the prediction file for a paper slug.

    Accepts both layouts: `<slug>_extracted.json` (the flat layout) and
    `<slug>_<version>_r<rep>_extracted.json` (pipeline.py run directories).
    """
    direct = PRED_DIR / f"{paper_slug}_extracted.json"
    if direct.exists() and (not PRED_VARIANT):
        return direct
    cands = [f for f in pred_files() if f.name.startswith(f"{paper_slug}_")]
    if not cands:
        return None
    if len(cands) > 1:
        print(f"[!] {len(cands)} prediction files for '{paper_slug}'; using "
              f"{cands[0].name}. Use --variant=<version>_r<rep> to choose.")
    return cands[0]


def load_extracted(paper_slug: str) -> list[dict]:
    """Load extracted JSON for a paper.

    Keeps the _text_recoverable flag map alongside the schema columns: a field
    flagged False means the paper states that value only in a figure, so a miss
    against it is a limitation of text extraction rather than of the extractor.
    """
    fname = find_pred_file(paper_slug)
    if fname is None:
        return []
    with open(fname) as f:
        rows = json.load(f)
    clean = []
    for row in rows:
        r = {k: row.get(k) for k in ALL_COLS}
        r["_text_recoverable"] = row.get("_text_recoverable") or {}
        clean.append(r)
    return clean


def is_text_recoverable(pred_row: dict, col: str) -> bool:
    """A field counts as text-recoverable unless explicitly flagged otherwise."""
    if not pred_row:
        return True
    return bool(pred_row.get("_text_recoverable", {}).get(col, True))


def normalize(v):
    """String normalisation for non-numeric comparison (catalyst, gas_mix).

    This is deliberately NOT where units are handled — see units.py. Prior to
    the audit this function was the entire comparison layer, which is how
    "10 Mpa" == "10 MPa" ended up being a lower-casing artefact.
    """
    if v is None or str(v).lower() in ("null", "none", ""):
        return None
    s = str(v).strip().lower()
    s = s.replace(" ", "").replace(",", ".")
    # normalise catalyst abbreviations: AC → C, gamma → γ etc.
    s = s.replace("/ac", "/c").replace("mgo/ac", "mgo/c")
    return s


def is_null(v) -> bool:
    return v is None or str(v).strip().lower() in _NULL_TOKENS


def _legacy_numeric(p: str, t: str, tol_mode: str, tol: float):
    """The v1 numeric comparison: float() on the space-squashed string.

    Returns None when either side will not parse, so the caller falls back to
    string equality — exactly as v1 did. Kept so --legacy and --protocol-audit
    can reproduce v1 rather than approximate it.
    """
    try:
        pf, tf = float(p.rstrip("%")), float(t.rstrip("%"))
    except ValueError:
        return None
    if tol_mode == "abs":
        return abs(pf - tf) <= tol
    return abs(pf - tf) / max(abs(tf), 1e-9) <= tol


def cell_match_kind(pred, truth, col, P: Protocol) -> str:
    """Compare one cell. Returns units.MATCH_EXACT / MATCH_RANGE / MATCH_MISS."""
    if col == "catalyst" and P.c3_match:
        return units.MATCH_EXACT if c3_catalyst_match(pred, truth) else units.MATCH_MISS

    p, t = normalize(pred), normalize(truth)
    if p is None and t is None:
        return units.MATCH_EXACT
    if p is None or t is None:
        return units.MATCH_MISS

    spec = TOLERANCES.get(col)
    if spec is not None:
        tol_mode, tol = spec if P.dim_tolerance else ("rel", 0.05)   # FIX 1
        if P.unit_norm:                                              # FIX 2
            dim = COL_DIMENSION.get(col)
            kind = units.values_match(pred, truth, dimension=dim,
                                      tol_mode=tol_mode, tol=tol)
            if kind == units.MATCH_RANGE and not P.interval:         # FIX 3
                # v1 had no interval parser: a range on one side fell through to
                # string equality, and an unequal string was scored FP *and* FN.
                return units.MATCH_EXACT if p == t else units.MATCH_MISS
            if kind != units.MATCH_NO_PARSE:
                return kind
        else:
            got = _legacy_numeric(p, t, tol_mode, tol)
            if got is not None:
                return units.MATCH_EXACT if got else units.MATCH_MISS

    return units.MATCH_EXACT if p == t else units.MATCH_MISS


def cell_match(pred, truth, col, P: Protocol) -> bool:
    return cell_match_kind(pred, truth, col, P) != units.MATCH_MISS


def catalyst_compatible(pred_row: dict, gt_row: dict, P: Protocol) -> bool:
    """Can these two rows describe the same experiment?

    Two rows over different materials are never the same experiment, however
    well their numbers happen to line up. Without C3 catalyst identity is not
    decidable across naming conventions (Cu/MgAlOx vs Cu/Mg-Al), so this gate
    only applies once composition-aware parsing is available. Since the audit
    this is a factor of its own (P.c3_gate), independent of whether the catalyst
    CELL is scored with C3 (P.c3_match) — the two were previously fused into a
    single --ablation switch, which made the ablation non-single-factor.
    """
    if not P.c3_gate:
        return True
    p, g = pred_row.get("catalyst"), gt_row.get("catalyst")
    if p is None or g is None:
        return True
    return c3_catalyst_match(p, g)


def _overlap_score(pred: dict, gt: dict, P: Protocol) -> int:
    """How much evidence is there that these two rows are the same experiment?

    With the alignment floor active (FIX 4a) only *informative* agreement
    counts: both sides non-null and matching. Two rows agreeing that a column is
    empty is not evidence of anything, so counting it — as v1 did — would make
    the threshold meaningless. With the floor off, the v1 score is reproduced.
    """
    # Catalyst cell comparison is a scoring factor, not an alignment factor.
    # Using it here would let C3 change the row pairing and then claim the gain
    # came only from final cell equality. The row gate remains separate through
    # catalyst_compatible().
    A = P.copy(c3_match=False) if P.c3_match else P
    if P.min_overlap <= 0:
        return sum(1 for col in P.cols() if cell_match(pred.get(col), gt.get(col), col, A))
    return sum(1 for col in SCORE_COLS
               if not is_null(pred.get(col)) and not is_null(gt.get(col))
               and cell_match(pred.get(col), gt.get(col), col, A))


def best_match(gt_row: dict, pred_rows: list[dict], used: set, P: Protocol):
    """Highest-scoring unused prediction row for this GT row, or None.

    Greedy: each prediction is consumed by at most one GT row.

    FIX 4a: the running best used to start at -1, which is below every
    achievable score, so the first unused row was always accepted regardless of
    whether a single cell agreed. Starting at (min_overlap - 1) imposes a floor
    of min_overlap agreeing cells; min_overlap=0 restores the v1 behaviour.
    """
    floor = max(P.min_overlap, 0) - 1
    best_idx, best_score, best_pred = None, floor, None
    for i, pred in enumerate(pred_rows):
        if i in used or not catalyst_compatible(pred, gt_row, P):
            continue
        score = _overlap_score(pred, gt_row, P)
        if score > best_score:
            best_score, best_idx, best_pred = score, i, pred
    return (best_idx, best_pred) if best_pred is not None else None


def new_stats(P: Protocol) -> dict:
    return {col: {"tp": 0, "tp_rng": 0, "fp": 0, "fp_un": 0, "fn": 0, "fn_fig": 0}
            for col in P.cols()}


def evaluate(gt_rows: list[dict], pred_rows: list[dict], P: Protocol):
    """Cell-by-cell TP/FP/FN per column. Returns (stats, rowinfo).

    fn_fig  counts false negatives on fields the paper only reports in a figure.
            Absolute recall counts these as misses; text-recoverable recall
            excludes them.
    tp_rng  counts true positives won by point-in-interval containment (FIX 3)
            rather than by exact agreement — a weaker form of hit.
    fp_un   counts false positives contributed by prediction rows that matched
            no GT row at all (FIX 4b), so row-level and cell-level imprecision
            can be reported separately.
    """
    stats = new_stats(P)
    cols = P.cols()
    used = set()
    n_matched = 0

    for gt in gt_rows:
        match = best_match(gt, pred_rows, used, P)
        if match is None:
            for col in cols:
                if not is_null(gt.get(col)):
                    stats[col]["fn"] += 1
            continue

        idx, pred = match
        used.add(idx)
        n_matched += 1
        for col in cols:
            g_null, p_null = is_null(gt.get(col)), is_null(pred.get(col))
            if g_null and p_null:
                continue
            if g_null:
                stats[col]["fp"] += 1
                continue
            if p_null:
                stats[col]["fn"] += 1
                if not is_text_recoverable(pred, col):
                    stats[col]["fn_fig"] += 1
                continue
            kind = cell_match_kind(pred.get(col), gt.get(col), col, P)
            if kind == units.MATCH_EXACT:
                stats[col]["tp"] += 1
            elif kind == units.MATCH_RANGE:
                stats[col]["tp"] += 1
                stats[col]["tp_rng"] += 1
            else:
                stats[col]["fn"] += 1
                stats[col]["fp"] += 1

    # FIX 4b: surplus prediction rows are extractor output that corresponds to
    # nothing in the reference. Every non-empty cell of such a row is a false
    # positive; discarding them (as v1 did) removes the row-level precision
    # penalty entirely.
    n_unmatched = 0
    if P.penalise_unmatched:
        for i, pred in enumerate(pred_rows):
            if i in used:
                continue
            n_unmatched += 1
            for col in cols:
                if not is_null(pred.get(col)):
                    stats[col]["fp"] += 1
                    stats[col]["fp_un"] += 1
    else:
        n_unmatched = len(pred_rows) - len(used)

    rowinfo = {"n_gt": len(gt_rows), "n_pred": len(pred_rows),
               "n_matched": n_matched, "n_unmatched_pred": n_unmatched}
    return stats, rowinfo


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) > 0 else None
    r = tp / (tp + fn) if (tp + fn) > 0 else None
    if p is None or r is None:
        f1 = None
    elif (p + r) == 0:
        # A defined precision and recall that are both zero imply F1=0.  Treating
        # this as missing silently drops the worst repeats/papers from means.
        f1 = 0.0
    else:
        f1 = 2 * p * r / (p + r)
    return p, r, f1


def totals(stats: dict):
    keys = ("tp", "fp", "fn", "fn_fig", "tp_rng", "fp_un")
    return tuple(sum(stats[c][k] for c in stats) for k in keys)


def print_report(stats: dict, title: str, P: Protocol):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")
    print(f"{'Column':<22} {'P':>6} {'R':>6} {'F1':>6}  TP  FP  FN  fig rng fp_un")
    print("-" * 80)
    for col in P.cols():
        s = stats[col]
        tp, fp, fn, ff, rg, fu = (s["tp"], s["fp"], s["fn"], s["fn_fig"],
                                  s["tp_rng"], s["fp_un"])
        p, r, f1 = prf(tp, fp, fn)
        ps = f"{p:.2f}" if p is not None else "  N/A"
        rs = f"{r:.2f}" if r is not None else "  N/A"
        fs = f"{f1:.2f}" if f1 is not None else "  N/A"
        print(f"{col:<22} {ps:>6} {rs:>6} {fs:>6}  {tp:2}  {fp:2}  {fn:2}  {ff:2}  {rg:2}  {fu:3}")
    print("-" * 80)
    atp, afp, afn, aff, arg, afu = totals(stats)
    p, r, f1 = prf(atp, afp, afn)
    print(f"{'OVERALL (absolute)':<22} {p:>6.2f} {r:>6.2f} {f1:>6.2f}  "
          f"{atp:2}  {afp:2}  {afn:2}  {aff:2}  {arg:2}  {afu:3}")
    print_text_recoverable(atp, afp, afn, aff, arg, afu)


def print_text_recoverable(tp, fp, fn, fn_fig, tp_rng=0, fp_un=0):
    """Dual recall (Contribution C1): absolute vs text-recoverable.

    Text-recoverable recall discounts GT cells the paper only reports in a
    figure, isolating extractor error from the limits of text-only input.
    """
    tr_fn = fn - fn_fig
    p, r, f1 = prf(tp, fp, tr_fn)
    if r is None:
        return
    label = "OVERALL (text-recov.)"
    print(f"{label:<22} {p:>6.2f} {r:>6.2f} {f1:>6.2f}  {tp:2}  {fp:2}  {tr_fn:2}   -  {tp_rng:2}  {fp_un:3}")
    if fn_fig:
        print(f"  └─ {fn_fig} figure-only GT cell(s) excluded from text-recoverable recall")
    if tp_rng:
        print(f"  └─ {tp_rng} of {tp} TP(s) won by interval containment, not exact agreement")
    if fp_un:
        print(f"  └─ {fp_un} of {fp} FP(s) from prediction rows that matched no GT row")


def print_side_by_side(gt_rows: list[dict], pred_rows: list[dict], P: Protocol):
    print(f"\n── Row-by-row comparison ──")
    used = set()
    for gi, gt in enumerate(gt_rows):
        match = best_match(gt, pred_rows, used, P)
        if match:
            idx, pred = match
            used.add(idx)
        else:
            pred = {}

        print(f"\n  GT row {gi+1} vs best extracted match:")
        print(f"  {'Column':<22} {'GT':>24} {'Extracted':>24} {'OK':>4}")
        print(f"  {'-'*78}")
        for col in P.cols():
            gv = str(gt.get(col) or "null")
            pv = str(pred.get(col) or "null") if pred else "—"
            kind = cell_match_kind(pred.get(col) if pred else None, gt.get(col), col, P)
            ok = {units.MATCH_EXACT: "✓", units.MATCH_RANGE: "≈"}.get(kind, "✗")
            print(f"  {col:<22} {gv:>24} {pv:>24} {ok:>4}")

    unmatched = [i for i in range(len(pred_rows)) if i not in used]
    if unmatched:
        note = ("scored as false positives" if P.penalise_unmatched
                else "DISCARDED — no precision penalty (v1 behaviour)")
        print(f"\n  {len(unmatched)} prediction row(s) matched no GT row — {note}:")
        for i in unmatched:
            r = pred_rows[i]
            nonempty = sum(1 for c in SCORE_COLS if not is_null(r.get(c)))
            print(f"    · row {i+1}: {r.get('catalyst')!r} "
                  f"T={r.get('temperature_K')!r}  ({nonempty} non-empty cells)")


def discover_papers() -> list[str]:
    """Paper names, read from the extracted JSONs so they match the GT spelling."""
    seen = []
    for json_path in pred_files():
        with open(json_path) as f:
            rows = json.load(f)
        if rows and rows[0].get("paper") and rows[0]["paper"] not in seen:
            seen.append(rows[0]["paper"])
    if not seen:
        print(f"[!] No *_extracted.json under {PRED_DIR}. "
              f"Point --pred-dir at a run directory or at manual_ceiling/.")
    return seen


def run_corpus(papers: list[str], P: Protocol, verbose=True):
    """Evaluate each paper; return (pooled stats, [(paper, n_gt, stats, rowinfo)])."""
    corpus = new_stats(P)
    per_paper = []

    for paper in papers:
        slug = paper.lower().replace(" ", "").replace("/", "")
        gt_rows = load_gt(paper_filter=paper)
        pred_rows = load_extracted(slug)

        if not gt_rows:
            if verbose:
                print(f"\n[!] No GT rows found for '{paper}'")
            continue
        if not pred_rows:
            if verbose:
                print(f"\n[!] No extracted JSON for '{paper}' "
                      f"(looked for {slug}_extracted.json)")
            continue

        stats, rowinfo = evaluate(gt_rows, pred_rows, P)
        if verbose:
            print(f"\nGT rows: {len(gt_rows)}  |  Extracted rows: {len(pred_rows)}"
                  f"  |  matched: {rowinfo['n_matched']}"
                  f"  |  unmatched predictions: {rowinfo['n_unmatched_pred']}")
            print_report(stats, f"{paper} — Extraction Evaluation", P)
            print_side_by_side(gt_rows, pred_rows, P)

        for col in P.cols():
            for k in corpus[col]:
                corpus[col][k] += stats[col][k]
        per_paper.append((paper, len(gt_rows), stats, rowinfo))

    return corpus, per_paper


def print_corpus_aggregate(corpus, per_paper, P: Protocol):
    n_pred = sum(ri["n_pred"] for _, _, _, ri in per_paper)
    n_unm = sum(ri["n_unmatched_pred"] for _, _, _, ri in per_paper)
    mode = []
    mode.append("C3-match on" if P.c3_match else "C3-match off")
    mode.append("C3-gate on" if P.c3_gate else "C3-gate off")
    print(f"\n\n{'#'*80}")
    print(f"  CORPUS AGGREGATE — {len(per_paper)} papers, "
          f"{sum(n for _, n, _, _ in per_paper)} GT rows, "
          f"{n_pred} predicted rows ({n_unm} unmatched)   [{', '.join(mode)}]")
    print(f"{'#'*80}")
    print(f"\n{'Paper':<24} {'GT':>4} {'Pred':>5} {'P':>6} {'R':>6} {'F1':>6} {'R_text':>8} {'F1_text':>8}")
    print("-" * 80)
    for paper, n_gt, stats, ri in per_paper:
        tp, fp, fn, ff, _, _ = totals(stats)
        p, r, f1 = prf(tp, fp, fn)
        _, r_txt, f1_txt = prf(tp, fp, fn - ff)
        rt = f"{r_txt:.2f}" if r_txt is not None else "   N/A"
        ft = f"{f1_txt:.2f}" if f1_txt is not None else "   N/A"
        print(f"{paper:<24} {n_gt:>4} {ri['n_pred']:>5} {p:>6.2f} {r:>6.2f} {f1:>6.2f} {rt:>8} {ft:>8}")
    print("-" * 80)
    print_report(corpus, "CORPUS MICRO-AVERAGE (all cells pooled)", P)


# ── Ablations ───────────────────────────────────────────────────────────────

def _corpus_row(corpus):
    """(catalyst F1, P, R, F1, R_text, F1_text) for one corpus stats dict."""
    c = corpus["catalyst"]
    _, _, cat_f1 = prf(c["tp"], c["fp"], c["fn"])
    tp, fp, fn, ff, _, _ = totals(corpus)
    p, r, f1 = prf(tp, fp, fn)
    _, r_txt, f1_txt = prf(tp, fp, fn - ff)
    return cat_f1, p, r, f1, r_txt, f1_txt


def print_ablation(papers, base: Protocol):
    """C3 ablation, split into two independent factors.

    Before the audit, --ablation flipped the catalyst comparison function AND
    the row-alignment gate together, so the reported "C3 effect" confounded two
    mechanisms: recovering catalyst cells that string matching missed, and
    preventing rows over different materials from being aligned. They are
    reported separately here; the joint row is the full C3 configuration.
    """
    variants = [
        ("V1 (string match)",        False, False),
        ("+C3 cell match only",      True,  False),
        ("+C3 row-align gate only",  False, True),
        ("V1 + C3 (both)",           True,  True),
    ]
    results = {}
    for label, m, g in variants:
        P = base.copy(c3_match=m, c3_gate=g)
        corpus, per_paper = run_corpus(papers, P, verbose=False)
        results[label] = (corpus, per_paper)

    print(f"\n{'='*80}")
    print("  ABLATION — Contribution C3: composition-aware normalisation")
    print("  (two independent factors: catalyst cell comparison, row-alignment gate)")
    print(f"{'='*80}")
    # FIX 6: the final column used to be headed "R_text" while printing F1.
    print(f"\n{'Variant':<26} {'catalyst F1':>12} {'corpus P':>9} {'corpus R':>9} "
          f"{'corpus F1':>10} {'R_text':>8} {'F1_text':>8}")
    print("-" * 80)
    for label, _, _ in variants:
        cat_f1, p, r, f1, r_txt, f1_txt = _corpus_row(results[label][0])
        cf = f"{cat_f1:.2f}" if cat_f1 is not None else "N/A"
        print(f"{label:<26} {cf:>12} {p:>9.2f} {r:>9.2f} {f1:>10.2f} "
              f"{r_txt:>8.2f} {f1_txt:>8.2f}")
    print("-" * 80)

    base_cat = results["V1 (string match)"][0]["catalyst"]
    for label in ("+C3 cell match only", "+C3 row-align gate only", "V1 + C3 (both)"):
        cat = results[label][0]["catalyst"]
        _, _, f1_b = prf(base_cat["tp"], base_cat["fp"], base_cat["fn"])
        _, _, f1_c = prf(cat["tp"], cat["fp"], cat["fn"])
        if f1_b is not None and f1_c is not None:
            print(f"  catalyst F1  V1 → {label:<24} {f1_b:.2f} → {f1_c:.2f}  "
                  f"({f1_c - f1_b:+.2f}, {cat['tp'] - base_cat['tp']:+d} cells)")

    print("\n  Per-paper corpus F1:")
    print(f"  {'Paper':<24} {'V1':>6} {'+cell':>7} {'+gate':>7} {'both':>7} {'Δ both':>8}")
    print(f"  {'-'*64}")
    pp = {label: {p: s for p, _, s, _ in results[label][1]} for label, _, _ in variants}
    for paper in pp["V1 (string match)"]:
        vals = []
        for label, _, _ in variants:
            tp, fp, fn, _, _, _ = totals(pp[label][paper])
            _, _, f1 = prf(tp, fp, fn)
            vals.append(f1 if f1 is not None else 0.0)
        delta = vals[3] - vals[0]
        flag = "  ←" if abs(delta) > 0.005 else ""
        print(f"  {paper:<24} {vals[0]:>6.2f} {vals[1]:>7.2f} {vals[2]:>7.2f} "
              f"{vals[3]:>7.2f} {delta:>+8.2f}{flag}")


def print_protocol_audit(papers):
    """Attribute the v1 → v2 score change to each individual protocol fix.

    Fixes are switched on cumulatively in the order they appear in the audit, so
    each row's delta is that fix's marginal contribution given the ones above
    it. Reported for both C3 settings because the C3 factors interact with the
    row-alignment fixes.
    """
    steps = [
        ("v1 (pre-audit protocol)",        {}),
        ("+ FIX 5  drop `paper` column",   {"score_paper": False}),
        ("+ FIX 1  dimension tolerances",  {"dim_tolerance": True}),
        ("+ FIX 2  unit normalisation",    {"unit_norm": True}),
        ("+ FIX 3  interval semantics",    {"interval": True}),
        ("+ FIX 4a row-align floor (N=2)", {"min_overlap": MIN_ROW_OVERLAP}),
        ("+ FIX 4b penalise unmatched",    {"penalise_unmatched": True}),
    ]

    for c3_label, c3 in (("C3 OFF (V1 baseline)", dict(c3_match=False, c3_gate=False)),
                         ("C3 ON  (full system)", dict(c3_match=True, c3_gate=True))):
        print(f"\n{'='*94}")
        print(f"  PROTOCOL AUDIT — cumulative effect of each fix   [{c3_label}]")
        print(f"{'='*94}")
        print(f"\n{'Protocol step':<34} {'cat F1':>7} {'P':>6} {'R':>6} {'F1':>6} "
              f"{'ΔF1':>7} {'R_text':>8} {'F1_text':>8} {'ΔF1_t':>7}")
        print("-" * 94)
        P = Protocol.legacy(**c3)
        prev_f1 = prev_f1t = None
        for label, kw in steps:
            P = P.copy(**kw)
            corpus, _ = run_corpus(papers, P, verbose=False)
            cat_f1, p, r, f1, r_txt, f1_txt = _corpus_row(corpus)
            cf = f"{cat_f1:.2f}" if cat_f1 is not None else "  N/A"
            d1 = f"{f1 - prev_f1:+.3f}" if prev_f1 is not None else "     —"
            d2 = f"{f1_txt - prev_f1t:+.3f}" if prev_f1t is not None else "     —"
            print(f"{label:<34} {cf:>7} {p:>6.2f} {r:>6.2f} {f1:>6.2f} {d1:>7} "
                  f"{r_txt:>8.2f} {f1_txt:>8.2f} {d2:>7}")
            prev_f1, prev_f1t = f1, f1_txt
        print("-" * 94)


CIMINO_NOTE = """
── NOTE: Selectivity definition discrepancy (Cimino 2019) ──
  Paper defines 'butanol yield' = butanol/ethanol_reacted × 100 (vol/vol)
  → This is the conventional SELECTIVITY definition (~12.5% for 20% MgO/AC)
  Callum's GT has selectivity=60% for 20% MgO/C — likely a different
  definition (possibly C-atom based) or figure-reading interpretation.
  This is a key finding: manual GT extraction itself has ambiguity.
"""


if __name__ == "__main__":
    args = sys.argv[1:]
    ablation = "--ablation" in args
    audit = "--protocol-audit" in args

    if "--legacy" in args:
        P = Protocol.legacy()
    else:
        P = Protocol()

    # C3: --c3/--no-c3 set both factors; --c3-match/--c3-gate set them singly.
    if "--c3" in args:
        P.c3_match = P.c3_gate = True
    if "--no-c3" in args:
        P.c3_match = P.c3_gate = False
    if "--c3-match" in args:
        P.c3_match = True
    if "--c3-gate" in args:
        P.c3_gate = True

    for a in args:
        if a.startswith("--min-overlap="):
            P.min_overlap = int(a.split("=", 1)[1])
        elif a.startswith("--pred-dir="):
            PRED_DIR = Path(a.split("=", 1)[1]).expanduser()
            if not PRED_DIR.is_absolute():
                PRED_DIR = (PIPELINE_DIR / PRED_DIR).resolve()
        elif a.startswith("--variant="):
            PRED_VARIANT = a.split("=", 1)[1]

    print(f"[predictions] {PRED_DIR}"
          f"{'  variant=' + PRED_VARIANT if PRED_VARIANT else ''}")

    positional = [a for a in args if not a.startswith("--")]
    paper_filter = positional[0] if positional else None
    papers = [paper_filter] if paper_filter else discover_papers()
    if not papers:
        sys.exit(1)

    if audit:
        print_protocol_audit(papers)
    elif ablation:
        print_ablation(papers, P)
    else:
        corpus, per_paper = run_corpus(papers, P)
        if len(per_paper) > 1:
            print_corpus_aggregate(corpus, per_paper, P)
        if "Cimino 2019" in papers:
            print(CIMINO_NOTE)

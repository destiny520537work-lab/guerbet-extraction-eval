"""
Score a pipeline run against ground_truth.csv and print the B0 -> V4 ablation table.

This is the reporting half of pipeline.py. It reuses evaluate.py's scoring
machinery unchanged -- the audited Protocol (dimensional tolerances, unit
normalisation, interval containment, row-overlap gating, unmatched-row penalty),
greedy row matching, and the dual absolute / text-recoverable recall -- so the
ablation numbers are produced by the same scorer as every other number in the
dissertation. Changing the protocol here would make this table incomparable with
the rest of the write-up; change it in evaluate.py or not at all.

Two deliberate departures from calling `evaluate.run_corpus`:

  1. A paper that produced *no* rows is scored as a full set of false negatives
     rather than skipped. run_corpus skips a paper with no predictions, which
     would quietly drop the worst cases out of the average and flatter any
     version that failed to return parseable output at all -- precisely the
     failure mode B0 is expected to exhibit.

  2. Scoring is per (version, repeat), so the spread across repeats is reported
     rather than averaged away. A prompt-ladder claim resting on one sample per
     rung is not a result (CONSTRAINT 3 in pipeline.py).

Predictions come from runs/<run_id>/; the ground truth from its usual location.
Note the asymmetry that keeps this honest: the *extractor* never sees
ground_truth.csv (it runs tool-less in an empty temp directory), while the
*scorer* obviously must. Extraction and scoring being separate processes is what
preserves that separation.

Usage:
    python3 score_runs.py <run_id>                 # ablation table
    python3 score_runs.py <run_id> --c3            # composition-aware catalyst match
    python3 score_runs.py <run_id> --legacy        # pre-audit v1 protocol
    python3 score_runs.py <run_id> --per-paper     # add per-paper breakdown
    python3 score_runs.py <run_id> --ceiling       # add contaminated manual line
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import evaluate
from pipeline import ALL_VERSIONS, CORPUS, RUNS_DIR

PIPELINE_DIR = Path(__file__).parent

_OUT_RE = re.compile(r"^(?P<slug>.+?)_(?P<version>B0|V\d)_r(?P<rep>\d+)_extracted\.json$")


def add_stats(into: dict, other: dict) -> None:
    for col, counts in other.items():
        for k, v in counts.items():
            into[col][k] += v


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        r = {k: row.get(k) for k in evaluate.ALL_COLS}
        r["_text_recoverable"] = row.get("_text_recoverable") or {}
        r["_uncertain"] = row.get("_uncertain") or {}
        r["_raw_values"] = row.get("_raw_values") or {}
        out.append(r)
    return out


def score_prediction_set(preds: dict[str, list[dict]], P,
                         slugs: list[str] | None = None) -> tuple[dict, dict]:
    """Pool cell-level stats over the corpus for one prediction set.

    A slug present in the corpus but absent (or empty) here is scored, not
    skipped: every populated ground-truth cell for that paper becomes a false
    negative. See note 1 in the module docstring.
    """
    pooled = evaluate.new_stats(P)
    rows = {"n_gt": 0, "n_pred": 0, "n_matched": 0, "n_unmatched_pred": 0}
    for slug in (slugs or list(CORPUS)):
        _pdf, gt_name = CORPUS[slug]
        gt_rows = evaluate.load_gt(paper_filter=gt_name)
        if not gt_rows:
            continue
        stats, info = evaluate.evaluate(gt_rows, preds.get(slug, []), P)
        add_stats(pooled, stats)
        for k in rows:
            rows[k] += info.get(k, 0)
    return pooled, rows


def summarise(stats: dict) -> dict:
    tp, fp, fn, ff, tp_rng, fp_un = evaluate.totals(stats)
    p, r, f1 = evaluate.prf(tp, fp, fn)
    _, r_txt, f1_txt = evaluate.prf(tp, fp, fn - ff)
    return {"p": p, "r": r, "f1": f1, "r_text": r_txt, "f1_text": f1_txt,
            "tp": tp, "fp": fp, "fn": fn, "fn_fig": ff,
            "tp_rng": tp_rng, "fp_unmatched": fp_un}


def discover(run_dir: Path) -> dict[tuple[str, int], dict[str, list[dict]]]:
    """(version, rep) -> {slug: rows} for every output file in the run."""
    found: dict[tuple[str, int], dict[str, list[dict]]] = defaultdict(dict)
    for path in sorted(run_dir.glob("*_extracted.json")):
        m = _OUT_RE.match(path.name)
        if m:
            found[(m["version"], int(m["rep"]))][m["slug"]] = load_rows(path)
    return found


def fmt(x, nd=3):
    return f"{x:.{nd}f}" if isinstance(x, float) else "  n/a"


def mean_of(per_rep: list[dict], key: str):
    vals = [s[key] for s in per_rep if s.get(key) is not None]
    return statistics.fmean(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("run_id")
    ap.add_argument("--c3", action="store_true",
                    help="composition-aware catalyst matching + row gate (C3)")
    ap.add_argument("--c3-match-only", action="store_true",
                    help="composition-aware catalyst cell comparison only")
    ap.add_argument("--c3-gate-only", action="store_true",
                    help="composition-aware row-alignment gate only")
    ap.add_argument("--legacy", action="store_true",
                    help="pre-audit v1 protocol (regression baseline)")
    ap.add_argument("--reference-restricted", action="store_true",
                    help="do not penalise prediction rows outside the current reference")
    ap.add_argument("--per-paper", action="store_true")
    ap.add_argument("--ceiling", action="store_true",
                    help="also score manual_ceiling/ (contaminated; reference only)")
    ap.add_argument("--json-out", help="write the summary table as JSON")
    args = ap.parse_args()

    run_dir = RUNS_DIR / args.run_id
    if not run_dir.is_dir():
        print(f"No such run: {run_dir}")
        return 2

    c3_match = args.c3 or args.c3_match_only
    c3_gate = args.c3 or args.c3_gate_only
    P = (evaluate.Protocol.legacy(c3_match=c3_match, c3_gate=c3_gate)
         if args.legacy else
         evaluate.Protocol(c3_match=c3_match, c3_gate=c3_gate,
                           penalise_unmatched=not args.reference_restricted))

    found = discover(run_dir)
    if not found:
        print(f"No *_extracted.json outputs in {run_dir}")
        return 2

    meta = {}
    if (run_dir / "meta.json").exists():
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))

    # 30 Aug 2026 fix: meta.json may be incomplete (sequential same-run-id
    # invocations once clobbered it). Trust the files on disk; warn on mismatch.
    file_slugs = sorted({slug for per_slug in found.values() for slug in per_slug})
    meta_slugs = list((meta.get("papers") or {}).keys())
    intended_slugs = sorted(set(file_slugs) | set(meta_slugs)) or list(CORPUS)
    if meta_slugs and set(meta_slugs) != set(intended_slugs):
        print(f"  WARNING: meta.json lists {len(meta_slugs)} papers but files cover "
              f"{len(intended_slugs)}; scoring the union from disk.")

    versions = [v for v in ALL_VERSIONS if any(k[0] == v for k in found)]
    n_gt = sum(len(evaluate.load_gt(paper_filter=CORPUS[s][1]))
               for s in intended_slugs)

    print(f"\n{'='*80}")
    print(f"  ABLATION  B0 -> V4    run={args.run_id}")
    print(f"{'='*80}")
    print(f"  protocol: {'legacy v1' if args.legacy else 'audited reference-restricted' if args.reference_restricted else 'audited strict'}"
          f"   catalyst matching: "
          f"{'C3 match+gate' if c3_match and c3_gate else 'C3 cell-only' if c3_match else 'C3 gate-only' if c3_gate else 'string'}")
    if meta:
        t = meta.get("totals", {})
        print(f"  model={meta.get('model_requested')}   "
              f"reps={meta.get('reps')}   papers={len(meta.get('papers', {}))}   "
              f"GT rows={n_gt}")
        print(f"  claude calls={t.get('claude_calls')}   "
              f"failed={t.get('failed_calls')} "
              f"({(t.get('call_failure_rate') or 0)*100:.1f}%)   "
              f"wall={((t.get('wall_clock_s') or 0)/60):.1f} min   "
              f"OCR repair={'on' if meta.get('ocr_repair_active') else 'off'}")

    print(f"\n{'Version':<9} {'F1':>7} {'sd':>6} {'P':>7} {'R':>7} "
          f"{'F1_text':>8} {'rows':>6} {'reps':>5}")
    print("-" * 80)

    table = {}
    for version in versions:
        reps = sorted(r for v, r in found if v == version)
        per_rep, rowcounts = [], []
        for rep in reps:
            preds = found[(version, rep)]
            stats, _info = score_prediction_set(preds, P, intended_slugs)
            per_rep.append(summarise(stats))
            rowcounts.append(sum(len(v) for v in preds.values()))
        f1s = [s["f1"] for s in per_rep if s["f1"] is not None]
        mean_f1 = statistics.fmean(f1s) if f1s else None
        sd_f1 = statistics.stdev(f1s) if len(f1s) > 1 else 0.0
        rows_mean = statistics.fmean(rowcounts) if rowcounts else 0.0

        table[version] = {
            "f1_mean": mean_f1, "f1_sd": sd_f1, "f1_per_rep": f1s,
            "p_mean": mean_of(per_rep, "p"), "r_mean": mean_of(per_rep, "r"),
            "f1_text_mean": mean_of(per_rep, "f1_text"),
            "rows_mean": rows_mean, "n_reps": len(reps), "per_rep": per_rep,
        }
        print(f"{version:<9} {fmt(mean_f1):>7} {sd_f1:>6.3f} "
              f"{fmt(mean_of(per_rep, 'p')):>7} {fmt(mean_of(per_rep, 'r')):>7} "
              f"{fmt(mean_of(per_rep, 'f1_text')):>8} {rows_mean:>6.1f} {len(reps):>5}")
    print("-" * 80)

    if args.ceiling:
        preds = {slug: load_rows(PIPELINE_DIR / "manual_ceiling" / f"{slug}_extracted.json")
                 for slug in intended_slugs}
        stats, _ = score_prediction_set(preds, P, intended_slugs)
        s = summarise(stats)
        print(f"{'manual*':<9} {fmt(s['f1']):>7} {'-':>6} {fmt(s['p']):>7} "
              f"{fmt(s['r']):>7} {fmt(s['f1_text']):>8} "
              f"{sum(len(v) for v in preds.values()):>6} {'-':>5}")
        print("  * manual = human careful read WITH ground_truth.csv visible.")
        print("    Contaminated: an upper reference line, not a comparable system.")
        table["manual_ceiling_contaminated"] = s

    if args.per_paper:
        print(f"\nPer-paper F1 (mean over repeats)")
        print(f"{'Paper':<18} " + " ".join(f"{v:>7}" for v in versions))
        print("-" * 80)
        for slug in intended_slugs:
            _pdf, gt_name = CORPUS[slug]
            gt_rows = evaluate.load_gt(paper_filter=gt_name)
            cells = []
            for version in versions:
                vals = []
                for (v, _rep), preds in found.items():
                    if v != version:
                        continue
                    stats, _ = evaluate.evaluate(gt_rows, preds.get(slug, []), P)
                    tp, fp, fn, *_ = evaluate.totals(stats)
                    _, _, f1 = evaluate.prf(tp, fp, fn)
                    if f1 is not None:
                        vals.append(f1)
                cells.append(f"{statistics.fmean(vals):>7.3f}" if vals else f"{'n/a':>7}")
            print(f"{gt_name:<18} " + " ".join(cells))

    print(f"\nGT rows scored: {n_gt} over {len(intended_slugs)} papers "
          f"(restricted to this run's intended corpus).")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"run_id": args.run_id, "c3_match": c3_match,
                        "c3_gate": c3_gate,
                        "reference_restricted": args.reference_restricted,
                        "legacy_protocol": args.legacy, "table": table},
                       indent=2, default=float), encoding="utf-8")
        print(f"Wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

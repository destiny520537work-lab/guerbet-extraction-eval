#!/usr/bin/env python3
"""
repair.py -- Deterministic, rule-based repair of OCR digit/letter confusion in
the text layer of scanned patent PDFs.

No language model is involved anywhere in this file, by design. The claim this
code supports is that the corruption must be repaired *before* the extraction
model is called, using rules whose behaviour is auditable and reproducible.
Using an LLM to guess "4S.O" back to "45.0" would reproduce exactly the
hallucination the pipeline is meant to avoid.

Usage
-----
    python3 repair.py <file.pdf>                    # summary + metrics
    python3 repair.py <file.pdf> --out repaired.txt # write repaired text
    python3 repair.py <file.pdf> --diff             # show every change
    python3 repair.py <file.pdf> --verify 20        # sampling sheet for manual
                                                    # checking against page images
    python3 repair.py <file.pdf> --scope all        # also repair non-table lines
    python3 repair.py <file.pdf> --decimal-consensus  # enable low-confidence D2

Safety invariants
-----------------
I1. Only tokens that the diagnostic stage flagged as damaged are ever modified.
    A token that survived OCR intact is never rewritten. This is what protects
    the temperature label "450" in a row of one-decimal values from being
    "corrected" to 45.0, while still allowing the damaged "2SO" in the same row
    to become 25.0.

I2. No substitution is applied outside a numeric context. All chemical guards
    from diagnose.py are in force, in particular the oxygen guard: in "Mg Al O
    85 0 1" the O is oxygen and is left alone, whereas in "Y-Al2O3 93 O O O 1"
    the same character is a mis-read zero and is repaired.

I3. English words and element symbols are unreachable: the character-class gate
    rejects any token containing a letter outside {O, S, B, G, I, l}, so MgO,
    Al2O3, H2O, CO3, TiO2 and ordinary prose can never enter the repair path.

I4. By default only numeric data rows are rewritten (--scope data). Citation
    strings are deliberately left corrupted: they are not extraction targets,
    and rewriting them would damage patent numbers.

Repair tiers
------------
T1  Character substitution: O->0, S->5, l->1, I->1, B->8, G->6.
    Always on. This is the high-confidence core.

D1  Leading-zero decimal reconstruction. After T1, a damaged token of the form
    0 followed by further digits with no decimal separator ("050", "06") had its
    decimal point lost by the OCR: no genuine measurement is written with a
    leading zero and no separator. Rewritten as 0.50 and 0.6. On by default.

D2  Row-consensus decimal reconstruction (OFF by default, --decimal-consensus).
    A damaged token that became a 3-digit integer inside a row whose other
    values all carry exactly one decimal place and are below 100 probably lost
    its separator too ("2SO" -> 250 -> 25.0). This is lower confidence because a
    temperature label such as 250 is shape-identical to the value 25.0; only
    invariant I1 keeps it tractable. Reported separately, never folded into the
    headline number.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("pdfplumber is required:  python3 -m pip install pdfplumber")

from diagnose import (
    CLEAN_NUMBER_RE,
    candidate_suspects,
    classify_line,
    is_clean_number,
    repair_token_chars,
    strip_edges,
)

# --------------------------------------------------------------------------
# Decimal reconstruction rules
# --------------------------------------------------------------------------

# D1: "050" / "06" / "0050" -- a leading zero followed by more digits and no
# separator. Genuine measurements are never written this way.
LEADING_ZERO_RE = re.compile(r"^0(\d{1,4})$")

# D2 candidate: a bare 3-digit integer produced by repairing a damaged token.
THREE_DIGIT_RE = re.compile(r"^\d{3}$")

ONE_DP_RE = re.compile(r"^\d{1,2}\.\d$")


def apply_d1(repaired: str) -> str | None:
    """Leading-zero decimal reconstruction. Returns new value or None."""
    m = LEADING_ZERO_RE.match(repaired)
    if not m:
        return None
    return "0." + m.group(1)


def row_consensus_one_dp(tokens: list[str], skip_idx: set[int]) -> bool:
    """
    True when the pristine numeric tokens of this row overwhelmingly carry
    exactly one decimal place and stay below 100 -- the signature of a
    single-precision measurement row.
    """
    # Index 0 is the row label (the independent variable, e.g. a set temperature
    # of 275) and is excluded: it is an integer by nature and would otherwise
    # veto the "all values below 100" test for every row.
    vals = [strip_edges(t) for i, t in enumerate(tokens)
            if i not in skip_idx and i != 0 and is_clean_number(t)]
    if len(vals) < 4:
        return False
    one_dp = sum(1 for v in vals if ONE_DP_RE.match(v))
    try:
        under_100 = all(float(v.rstrip(".,;:%)")) < 100 for v in vals)
    except ValueError:
        return False
    return under_100 and one_dp / len(vals) >= 0.60


def apply_d2(repaired: str, idx: int, tokens: list[str],
             suspect_idx: set[int]) -> str | None:
    """
    Row-consensus decimal reconstruction. Never applied to the first token of a
    row, which is the independent variable (a temperature such as 300).
    """
    if idx == 0 or not THREE_DIGIT_RE.match(repaired):
        return None
    if int(repaired) < 100:
        return None
    if not row_consensus_one_dp(tokens, suspect_idx):
        return None
    return repaired[:2] + "." + repaired[2:]


# --------------------------------------------------------------------------
# Line-level repair
# --------------------------------------------------------------------------

@dataclass
class Change:
    page: int
    kind: str
    before: str
    after: str
    tier: str
    line_before: str
    line_after: str


def repair_line(text: str, page: int, scope: str = "data",
                use_d1: bool = True, use_d2: bool = False
                ) -> tuple[str, list[Change]]:
    """
    Repair one physical line. Returns (repaired_line, changes).
    Only tokens confirmed by diagnose.classify_line are touched (invariant I1).
    """
    lv = classify_line(text, page)
    if lv is None:
        return text, []
    if scope == "data" and lv.kind != "DATA":
        return text, []

    tokens = text.split()
    # Recover the indices of the confirmed suspects. classify_line returns the
    # surface forms; re-derive positions so we can rewrite in place.
    wanted = list(lv.suspects)
    suspect_idx: list[int] = []
    for i, tok in enumerate(tokens):
        if tok in wanted:
            suspect_idx.append(i)
            wanted.remove(tok)
    if not suspect_idx:
        return text, []

    sidx = set(suspect_idx)
    changes: list[Change] = []
    new_tokens = list(tokens)

    for i in suspect_idx:
        raw = tokens[i]
        core = strip_edges(raw)
        # Keep surrounding punctuation exactly as it was.
        lead = raw[:raw.find(core)] if core in raw else ""
        trail = raw[raw.find(core) + len(core):] if core in raw else ""

        fixed = repair_token_chars(core)
        tier = "T1"

        if use_d1:
            d1 = apply_d1(fixed)
            if d1 is not None:
                fixed, tier = d1, "D1"
        if use_d2 and tier == "T1":
            d2 = apply_d2(fixed, i, tokens, sidx)
            if d2 is not None:
                fixed, tier = d2, "D2"

        if fixed != core:
            new_tokens[i] = lead + fixed + trail
            changes.append(Change(page=page, kind=lv.kind, before=raw,
                                  after=new_tokens[i], tier=tier,
                                  line_before=text.strip(), line_after=""))

    if not changes:
        return text, []

    # Rebuild while preserving the original run-length spacing where possible.
    new_line = text
    for ch in changes:
        new_line = re.sub(r"(?<!\S)" + re.escape(ch.before) + r"(?!\S)",
                          ch.after, new_line, count=1)
    for ch in changes:
        ch.line_after = new_line.strip()
    return new_line, changes


# --------------------------------------------------------------------------
# Document-level repair and metrics
# --------------------------------------------------------------------------

@dataclass
class RepairReport:
    path: str
    name: str
    scope: str = "data"
    changes: list[Change] = field(default_factory=list)
    # Metric: numeric tokens that a downstream parser can read, inside rows
    # classified as experimental data.
    parseable_before: int = 0
    parseable_after: int = 0
    data_tokens_total: int = 0
    tier_counts: dict = field(default_factory=dict)
    text_before: str = ""
    text_after: str = ""

    @property
    def rescued(self) -> int:
        return self.parseable_after - self.parseable_before

    @property
    def coverage_before(self) -> float:
        return (100.0 * self.parseable_before / self.data_tokens_total
                if self.data_tokens_total else 0.0)

    @property
    def coverage_after(self) -> float:
        return (100.0 * self.parseable_after / self.data_tokens_total
                if self.data_tokens_total else 0.0)


def count_parseable(line: str) -> tuple[int, int]:
    """(parseable numeric tokens, total tokens) for one line."""
    toks = line.split()
    return sum(1 for t in toks if CLEAN_NUMBER_RE.match(strip_edges(t))), len(toks)


def repair_pdf(path: str, scope: str = "data", use_d1: bool = True,
               use_d2: bool = False) -> RepairReport:
    rep = RepairReport(path=path,
                       name=os.path.splitext(os.path.basename(path))[0],
                       scope=scope)
    out_before: list[str] = []
    out_after: list[str] = []

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.split("\n"):
                fixed, changes = repair_line(line, pno, scope=scope,
                                             use_d1=use_d1, use_d2=use_d2)
                out_before.append(line)
                out_after.append(fixed)
                rep.changes.extend(changes)

                # Metrics are computed over rows that carry experimental data,
                # which is what the extraction stage actually consumes.
                lv = classify_line(line, pno)
                is_data = (lv is not None and lv.kind == "DATA")
                if not is_data:
                    # A row can also be pristine data with no suspects at all;
                    # include it when it looks numeric, so the denominator is
                    # the whole table surface rather than only damaged rows.
                    toks = line.split()
                    n_clean = sum(1 for t in toks
                                  if CLEAN_NUMBER_RE.match(strip_edges(t)))
                    is_data = len(toks) >= 4 and n_clean >= 3
                if is_data:
                    pb, tot = count_parseable(line)
                    pa, _ = count_parseable(fixed)
                    rep.parseable_before += pb
                    rep.parseable_after += pa
                    rep.data_tokens_total += tot

    for ch in rep.changes:
        rep.tier_counts[ch.tier] = rep.tier_counts.get(ch.tier, 0) + 1
    rep.text_before = "\n".join(out_before)
    rep.text_after = "\n".join(out_after)
    return rep


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", help="PDF file(s) to repair")
    ap.add_argument("--out", help="write repaired text to this file "
                                  "(one file per PDF when several are given)")
    ap.add_argument("--diff", action="store_true", help="print every change")
    ap.add_argument("--verify", type=int, default=0, metavar="N",
                    help="emit an N-row manual verification sheet (TSV)")
    ap.add_argument("--verify-out", help="path for the verification sheet")
    ap.add_argument("--scope", choices=("data", "all"), default="data",
                    help="repair only numeric data rows (default) or every line")
    ap.add_argument("--no-d1", action="store_true",
                    help="disable leading-zero decimal reconstruction")
    ap.add_argument("--decimal-consensus", action="store_true",
                    help="enable the lower-confidence D2 rule")
    ap.add_argument("--json", help="write metrics as JSON")
    args = ap.parse_args(argv)

    all_metrics = []
    for path in args.pdfs:
        if not os.path.exists(path):
            print(f"!! missing: {path}", file=sys.stderr)
            continue
        rep = repair_pdf(path, scope=args.scope, use_d1=not args.no_d1,
                         use_d2=args.decimal_consensus)

        print(f"\n=== {rep.name} ===")
        print(f"  scope                    : {rep.scope}")
        print(f"  tokens modified          : {len(rep.changes)}  {rep.tier_counts}")
        print(f"  data-row tokens (total)  : {rep.data_tokens_total}")
        print(f"  parseable numbers before : {rep.parseable_before} "
              f"({rep.coverage_before:.1f}% of data-row tokens)")
        print(f"  parseable numbers after  : {rep.parseable_after} "
              f"({rep.coverage_after:.1f}% of data-row tokens)")
        print(f"  NUMERIC TOKENS RESCUED   : {rep.rescued}"
              + (f"  (+{100.0 * rep.rescued / rep.parseable_before:.1f}% "
                 f"relative)" if rep.parseable_before else ""))

        if args.diff:
            print("  changes:")
            for ch in rep.changes:
                print(f"    p{ch.page:<3} [{ch.tier}] {ch.before!r} -> {ch.after!r}")
                print(f"          {ch.line_before[:100]}")

        if args.verify:
            rows = ["page\ttier\tocr_token\trepaired_token\tocr_line\ttruth_value\tcorrect(y/n)"]
            step = max(1, len(rep.changes) // args.verify)
            for ch in rep.changes[::step][:args.verify]:
                rows.append(f"{ch.page}\t{ch.tier}\t{ch.before}\t{ch.after}\t"
                            f"{ch.line_before[:80]}\t\t")
            sheet = "\n".join(rows)
            dest = args.verify_out or f"verify_{rep.name}.tsv"
            with open(dest, "w") as fh:
                fh.write(sheet + "\n")
            print(f"  [verification sheet] {dest} ({len(rows) - 1} rows)")

        if args.out:
            dest = args.out if len(args.pdfs) == 1 else f"{rep.name}.repaired.txt"
            with open(dest, "w") as fh:
                fh.write(rep.text_after)
            print(f"  [repaired text] {dest}")

        all_metrics.append({
            "name": rep.name, "scope": rep.scope,
            "tokens_modified": len(rep.changes), "tiers": rep.tier_counts,
            "data_tokens_total": rep.data_tokens_total,
            "parseable_before": rep.parseable_before,
            "parseable_after": rep.parseable_after,
            "rescued": rep.rescued,
            "coverage_before_pct": round(rep.coverage_before, 2),
            "coverage_after_pct": round(rep.coverage_after, 2),
        })

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(all_metrics, fh, indent=2)
        print(f"\n[json] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

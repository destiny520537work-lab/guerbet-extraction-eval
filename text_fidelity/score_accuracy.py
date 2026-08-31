#!/usr/bin/env python3
"""
score_accuracy.py -- Measure repair accuracy against manual ground truth.

The ground truth in ground_truth_US9056811_p6p7.tsv was read by eye from
190-dpi renders of the scanned page images, so this is a genuine
image-vs-output comparison rather than a self-consistency check.

    python3 score_accuracy.py
    python3 score_accuracy.py --decimal-consensus     # score with D2 enabled
"""

from __future__ import annotations

import argparse
import os
import sys

import repair as R

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(HERE, "..", "..", "1.2.4 邮件7.24专利", "US9056811.pdf")
TRUTH = os.path.join(HERE, "ground_truth_US9056811_p6p7.tsv")


def load_truth(path: str) -> list[tuple[int, int, str, str]]:
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.startswith("idx\t"):
                continue
            idx, page, tok, truth = line.split("\t")
            rows.append((int(idx), int(page), tok, truth))
    return rows


def num_eq(a: str, b: str) -> bool:
    """Numeric equality, so 0.50 == 0.5 is not counted as an error."""
    try:
        return abs(float(a.rstrip(".,;:%)")) - float(b)) < 1e-9
    except ValueError:
        return a == b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decimal-consensus", action="store_true")
    ap.add_argument("--pdf", default=PDF)
    ap.add_argument("--truth", default=TRUTH)
    args = ap.parse_args(argv)

    truth = load_truth(args.truth)
    rep = R.repair_pdf(args.pdf, scope="data", use_d1=True,
                       use_d2=args.decimal_consensus)
    changes = [c for c in rep.changes if c.page in (6, 7)]

    if len(changes) != len(truth):
        print(f"!! change count {len(changes)} != truth rows {len(truth)}; "
              "the ground-truth file must be regenerated", file=sys.stderr)

    mode = "T1 + D1 + D2" if args.decimal_consensus else "T1 + D1 (default)"
    print(f"Repair configuration : {mode}")
    print(f"Repairs scored       : {min(len(changes), len(truth))} "
          f"(US9056811 pages 6-7)\n")

    correct, wrong = 0, []
    for (idx, page, tok, want), ch in zip(truth, changes):
        got = R.strip_edges(ch.after)
        if tok != ch.before:
            print(f"  !! alignment drift at idx {idx}: truth token {tok!r} "
                  f"vs pipeline {ch.before!r}", file=sys.stderr)
        if num_eq(got, want):
            correct += 1
        else:
            wrong.append((idx, page, tok, got, want, ch.tier, ch.line_before))

    n = correct + len(wrong)
    print(f"  correct : {correct}/{n}  ({100.0 * correct / n:.1f}%)")
    print(f"  wrong   : {len(wrong)}")
    for idx, page, tok, got, want, tier, line in wrong:
        print(f"\n    [{idx}] p{page} {tok!r} -> {got!r}  but printed value is "
              f"{want!r}   (tier {tier})")
        print(f"         {line[:88]}")

    print(f"\n  tier usage across whole document: {rep.tier_counts}")
    print(f"  numeric tokens rescued (document): {rep.rescued}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

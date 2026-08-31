"""Finalise an experiment run directory before any result is used.

Usage: python3 finalize_run.py <run_id> --expect slug1,slug2 --versions V4[,V5] --reps 3
                               [--no-repair-expected]

Steps (GPT-audit remediation, 30 Aug 2026):
 1. Verify every expected (slug, version, rep) has a *_extracted.json on disk.
 2. Rebuild meta.json 'papers' and 'cells' from disk for any invocation whose
    metadata was clobbered by the old last-writer-wins bug (raw/FAILED file
    counts give per-cell call statistics).
 3. Record the effective OCR-repair state for the whole run.
 4. Write a SHA-256 manifest of every artefact in the directory.
 5. Plant DO_NOT_RERUN so no future invocation can overwrite the directory.
Exit code 0 = finalised; 1 = expectations not met (nothing written).
"""
import argparse, hashlib, json, re, sys
from pathlib import Path

RUNS = Path(__file__).parent / "runs"
OUT_RE = re.compile(r"^(?P<slug>.+?)_(?P<version>B0|V\d)_r(?P<rep>\d+)_extracted\.json$")
RAW_RE = re.compile(r"^(?P<slug>.+?)_(?P<version>B0|V\d)_r(?P<rep>\d+)_(?P<label>.+?)(\.FAILED)?\.txt$")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id"); ap.add_argument("--expect", required=True)
    ap.add_argument("--versions", required=True); ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--no-repair-expected", action="store_true")
    a = ap.parse_args()
    run_dir = RUNS / a.run_id
    slugs = [s.strip() for s in a.expect.split(",")]
    versions = [v.strip() for v in a.versions.split(",")]

    # 1. completeness check
    have = {}
    for p in run_dir.glob("*_extracted.json"):
        m = OUT_RE.match(p.name)
        if m: have[(m["slug"], m["version"], int(m["rep"]))] = p
    missing = [(s, v, r) for s in slugs for v in versions for r in range(1, a.reps + 1)
               if (s, v, r) not in have]
    if missing:
        print(f"NOT FINALISED — {len(missing)} expected cells missing, e.g. {missing[:4]}")
        return 1

    # 2. rebuild meta from disk
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    raw_counts, fail_counts = {}, {}
    for p in (run_dir / "raw").glob("*.txt") if (run_dir / "raw").exists() else []:
        m = RAW_RE.match(p.name)
        if not m: continue
        key = (m["slug"], m["version"], int(m["rep"]))
        if p.name.endswith(".FAILED.txt"): fail_counts[key] = fail_counts.get(key, 0) + 1
        else: raw_counts[key] = raw_counts.get(key, 0) + 1
    cells = []
    for (s, v, r), p in sorted(have.items()):
        rows = json.loads(p.read_text())
        cells.append({"slug": s, "version": v, "rep": r,
                      "n_rows": len(rows) if isinstance(rows, list) else None,
                      "n_calls": raw_counts.get((s, v, r)),
                      "n_failed_calls": fail_counts.get((s, v, r), 0),
                      "source": "rebuilt-from-disk"})
    meta["papers_finalised"] = slugs
    meta["cells_rebuilt_from_disk"] = cells
    meta["ocr_repair_effective"] = (not a.no_repair_expected)
    meta["finalised"] = True
    total_raw = sum(c["n_calls"] or 0 for c in cells)
    total_fail = sum(c["n_failed_calls"] for c in cells)
    meta["totals_rebuilt"] = {"cells": len(cells), "calls_with_saved_reply": total_raw,
                              "failed_call_files": total_fail}
    meta_path.write_text(json.dumps(meta, indent=2))

    # 3+4. manifest
    lines = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name not in {"manifest_SHA256.txt", "DO_NOT_RERUN"}:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{h}  {p.relative_to(run_dir)}")
    (run_dir / "manifest_SHA256.txt").write_text("\n".join(lines) + "\n")

    # 5. freeze
    (run_dir / "DO_NOT_RERUN").write_text("finalised; do not rerun into this directory\n")
    print(f"FINALISED {a.run_id}: {len(cells)} cells, {len(slugs)} papers, "
          f"{len(lines)} artefacts hashed, repair_effective={not a.no_repair_expected}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

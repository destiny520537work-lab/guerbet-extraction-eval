# Guerbet Reaction Record Extraction — Evaluation Pipeline and Data

Code, reference tables, scored artefacts and adjudication records for an
LLM-based pipeline that extracts catalyst–condition–performance records from
Guerbet-reaction papers and patents, together with the scoring protocol used
to evaluate it. This repository is the set of supporting tools and resources
for an accompanying written report; section numbers below refer to that report.

## Contents

| Path | What it is |
|---|---|
| `extraction_pipeline/` | Extraction, scoring and orchestration code |
| `extraction_pipeline/pipeline.py` | Runs the extraction grid: PDF → text → chunks → pinned model → JSON, in an isolated subprocess |
| `extraction_pipeline/prompts.py` | The frozen prompt ladder B0–V5, plus the post hoc controlled pair V6/V7 (named V4c/V5c in the report) |
| `extraction_pipeline/evaluate.py` | One-to-one alignment scorer; strict and reference-restricted readings; catalyst-comparator modes |
| `extraction_pipeline/normalise.py`, `units.py` | Catalyst comparator (24 self-tests) and unit handling (28 tests) |
| `extraction_pipeline/score_runs.py`, `finalize_run.py` | Score a finalised run; rebuild run accounting from per-cell files on disk |
| `extraction_pipeline/ground_truth.csv` | Expert-curated journal reference (35 rows) |
| `extraction_pipeline/ground_truth_portillo_corrected.csv` | Variant with the two audit-documented corrections to the Portillo row |
| `extraction_pipeline/patent_ground_truth.csv` | Patent reference mapped from the curator's spreadsheet (416 rows; °C→K, atm→kPa conversions documented in `build_patent_gt.py`) |
| `extraction_pipeline/analysis_artifacts/` | Saved scoring outputs (A–E series, `.json` + captured stdout) with `PROVENANCE.json` (exact command, reference hash, run-manifest hash per job) and `MANIFEST_SHA256.txt` |
| `extraction_pipeline/analysis_artifacts/haiku_probe_*` | Raw output of the auxiliary-model attribution probe (report §3.6) |
| `extraction_pipeline/manual_ceiling/` | Seven hand-written extractions kept only as a contaminated informed-reader ceiling; not extractor output (see its README) |
| `extraction_pipeline/test_evaluate.py`, `test_isolation.py` | Scorer regression tests and adversarial isolation probes |
| `extraction_pipeline/extract.py` | Original single-paper script; its PDF reader and chunker are reused by `pipeline.py` |
| `text_fidelity/` | Document-integrity diagnosis and guarded OCR repair, with manual-validation data and `RESULTS.md` |
| `figures/` | Scripts that generate every figure in the report from run artefacts (`figdata.py` holds every number) |
| `adjudication/` | Manual adjudication evidence for the Portillo Crespo 2022 case study (report §4.7) |
| `manifest_*.txt` | SHA-256 manifests of the six finalised run directories |

## Reproducing scores

```
cd extraction_pipeline
python3 test_evaluate.py          # scorer regression (5 tests)
python3 score_runs.py <run_id>    # requires the run directory under runs/
```

The exact command behind every saved score, including the `PIPELINE_GT`
environment variable where one was used, is recorded per job in
`analysis_artifacts/PROVENANCE.json`.

Raw run directories (model replies, per-cell JSON) are not included for
size; their SHA-256 manifests are, so any provided artefact can be verified
against them. Source PDFs are not redistributed: the journal articles are
identified in the report's reference list, and each document's licence
category is recorded in the project's data management plan.

Run identifiers are historical. In particular `heldout_portillo_20260830` was
named before the document's development history was audited; the report
classifies that run as a post hoc transfer case study, not a held-out
evaluation.

No personal data is contained in this repository.

## Adjudication records

`adjudication/audit_sheet_2026-08-30.md` is the row-by-row audit sheet with
page-anchored evidence for the Portillo Crespo 2022 extractions.
`adjudication/adjudication_record_verbatim_2026-08-31.md` is the author's
verbatim working record of the manual adjudication, dictated in Chinese and
kept unedited as primary evidence; informality and language are properties of
a working record. The report's evidence ledger refers to these two files by
these filenames.

# Manual ceiling reference (NOT extractor output)

These seven JSON files were produced by a human reading each paper in an
interactive session, not by any program. They are preserved as an
**informed-reader ceiling**, and must never be reported as pipeline results.

Two reasons they cannot be treated as a measurement:

1. **Evaluation contamination.** They were written while `ground_truth.csv` was
   present in the same directory and visible to the author. Several `_notes`
   fields cite the answer key directly ("GT ERROR", "GT says 573 K"). An
   extraction that has seen the answer key measures nothing about extraction.
2. **Not reproducible.** No code path produced them. `extraction_pipeline/prompts.py`
   was never imported by anything (its bytecode was absent from `__pycache__`),
   and `extract.py` requires `ANTHROPIC_API_KEY` and was never run.

Real, reproducible extractor output lives in `../runs/<run_id>/`, produced by
`../pipeline.py`, which runs each paper through a tool-less subprocess in an
empty temp directory that cannot reach `ground_truth.csv`.

Use these files only as an upper reference line: roughly what a careful human
reader extracts when they already know what they are looking for.

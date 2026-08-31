# Extraction pipeline

Programmatic, reproducible extraction of catalyst performance rows from the
Guerbet paper corpus, plus the scorer that turns a run into the B0 → V4 ablation
table.

## Why this exists

The accompanying report describes a five-rung prompt ladder (B0, V1, V2, V3, V4). Until
this package, that ladder had never been executed. `prompts.py` defined the five
system prompts but no module imported it, and `extract.py` required an
`ANTHROPIC_API_KEY` that was never set. The seven `*_extracted.json` files that
existed were written by a human reading each paper in an interactive session with
`ground_truth.csv` visible in the same directory — several of their `_notes`
fields quote the answer key directly. They are preserved in `manual_ceiling/` as a
contaminated reference line and are not extractor results.

## Files

| File | Role |
| --- | --- |
| `pipeline.py` | Runs the extraction grid. PDF → text → chunks → `claude -p` → JSON |
| `score_runs.py` | Scores a run against `ground_truth.csv`; prints the ablation table |
| `test_isolation.py` | Adversarial check that the extractor cannot reach the answer key |
| `prompts.py` | The five prompt versions (unchanged; now actually imported) |
| `extract.py` | Original single-paper script; its PDF reader and chunker are reused |
| `evaluate.py` | Scoring protocol (owned by the evaluation-audit workstream) |
| `manual_ceiling/` | The seven contaminated hand-written extractions |
| `runs/<run_id>/` | Per-run outputs, raw replies, `meta.json`, `attempts.jsonl` |

## Cost

Zero marginal cost. Extraction shells out to `claude -p`, which authenticates
against the Claude Pro subscription; no API key is involved. The `cost_usd`
figures in `meta.json` are the notional API-list equivalent the CLI reports, not
money spent.

## The three hard constraints

These govern the design and are documented inline in `pipeline.py`. They are the
reason the numbers are usable.

**1. Evaluation isolation.** The extraction subprocess cannot see
`ground_truth.csv` or any other project file. This is structural, not a
convention:

- each call runs with `cwd` set to a freshly created empty temp directory outside
  the project tree, so the answer key is not on any relative path;
- `--tools ""` removes every built-in tool, so the subprocess has no `Read`,
  `Bash`, `Glob` or `WebFetch` with which to reach the filesystem at all;
- `--safe-mode` suppresses `CLAUDE.md` discovery, skills, plugins, hooks and MCP
  servers;
- `--setting-sources ""` and `--strict-mcp-config` block user, project and local
  settings.

The only channel into the model is the system prompt and the paper text on stdin.
`test_isolation.py` attacks this directly — it asks the model to list its tools,
to read the answer key by absolute path, to search the filesystem for it, to dump
its working directory, and to recite the benchmark values from memory — and fails
the build if any ground-truth value comes back. Run it before trusting a run:

```bash
python3 test_isolation.py
```

**2. Version locking and provenance.** The model is pinned by full name
(`claude-sonnet-5`), never by a floating alias such as `sonnet`. Prompts are
hashed at run time. `runs/<run_id>/meta.json` records the timestamp, requested and
observed models, prompt hashes, per-paper chunk counts and labels, chunker
configuration, retry budget, whether OCR repair was active, and the call-failure
rate.

**3. Repeated sampling.** Each (paper, version) cell runs `--reps` times, default
3, and `score_runs.py` reports the standard deviation of F1 across repeats. A
prompt-ladder claim resting on one sample per rung is not a result.

## Chunking

The chunker differs by version, and that difference is the experimental
manipulation rather than an implementation detail:

- **B0, V1, V2** get `extract.py`'s original page-packing chunker: 12 000
  characters, no overlap, no section awareness.
- **V3, V4** get section-aware chunks. Reference lists and acknowledgements are
  stripped, headings are detected and classified, and Methods is presented before
  Results, because V3's prompt promises exactly that focus. If V3 were fed the
  same character chunks as V2 the two rungs would differ only in wording.

Content preservation is the invariant: apart from identified back matter, every
character reaches some chunk. Two corpus-specific notes worth knowing:

- Several publishers in this corpus (MDPI in particular) print Results and
  Discussion *before* Materials and Methods, so the span following "Introduction"
  carries the whole results narrative. Treating it as front matter discarded ~75%
  of the paper; oversized front blocks are therefore reclassified as results.
- `pdfplumber`'s table detector fires mostly on figures and layout rules in these
  PDFs, producing table markers whose cells are empty. A dedicated table chunk is
  only emitted when it carries real content, which on this corpus is never. The
  code path is kept because it is correct for a PDF with a genuine table text
  layer, and the emptiness is recorded rather than passed off as table data.

## Usage

```bash
# What is in the corpus, and is the PDF actually present?
python3 pipeline.py --list

# Smoke test: one paper, one version
python3 pipeline.py --paper liu2022 --version V1 --reps 1 --run-id smoke01

# Full grid: 6 papers x 5 versions x 3 repeats
python3 pipeline.py --all --reps 3 --workers 6 --run-id grid01

# Score it
python3 score_runs.py grid01 --per-paper --ceiling
python3 score_runs.py grid01 --c3          # composition-aware catalyst matching

# Re-derive JSONs from saved raw replies after a post-processing fix — no API calls
python3 pipeline.py --rebuild --run-id grid01
```

Every model reply is kept under `runs/<run_id>/raw/`, so a bug in parsing, key
harmonisation or deduplication can be corrected and reapplied without paying to
extract again. The model output is the measurement; it should not have to be
re-collected because the code that reads it changed.

## Corpus

Six papers, not seven. `ground_truth.csv` and `manual_ceiling/` both cover
Portillo Crespo 2022, but no PDF for it exists in `../guerbet_papers/`, so it
cannot be extracted programmatically. `score_runs.py` scores the six extractable
papers and says so in its footer.

| Slug | PDF | Ground-truth name |
| --- | --- | --- |
| `cimino2019` | `applsci-09-01371.pdf` | Cimino 2019 |
| `herrera2024` | `hucal2024.pdf` | Herrera 2024 |
| `liu2022` | `liu2022.pdf` | Liu 2022 |
| `malina2024` | `malina2024.pdf` | Malina 2024 |
| `malina2025` | `malina2025.pdf` | Malina 2025 |
| `xi2020` | `xi2020.pdf` | Xi 2020 |

## Failure accounting

JSON parse failures are retried up to three times per chunk, and both the failure
count and the unparseable reply are kept (`raw/*.FAILED.txt`,
`attempts.jsonl`, and the `call_failure_rate` in `meta.json`). The failure rate is
a reportable result, not noise to be suppressed: comparable work has lost whole
papers to unparseable model output.

The dominant failure mode observed so far is not malformed JSON but prose: when a
chunk contains no extractable data, the model explains that it found none instead
of returning the empty array it was asked for. That is an instruction-following
failure and is counted as one.

## Scoring notes

`score_runs.py` reuses `evaluate.py`'s protocol unchanged so the ablation table is
comparable with every other number in the write-up. Two deliberate differences
from calling `evaluate.run_corpus` directly:

1. A paper that produced no rows is scored as a full set of false negatives rather
   than skipped. `run_corpus` skips papers with no predictions, which would drop
   the worst cases out of the average and flatter any version that failed to
   return parseable output at all.
2. Scoring is per (version, repeat) so the spread across repeats can be reported.

Field names are harmonised onto the schema before scoring, using one alias map
applied identically to every version — B0 is given no schema and so names fields
however it likes, and this separates "used a different key name" from "failed to
find the value". Values are never rewritten: B0 reports temperatures in Celsius
because nothing told it to convert, and that deficit is left to score as one. A
bare `space_velocity` field is routed to GHSV, LHSV or WHSV by reading the value
for the discriminating token, and dropped when it says nothing, rather than being
guessed into one of three physically different columns.

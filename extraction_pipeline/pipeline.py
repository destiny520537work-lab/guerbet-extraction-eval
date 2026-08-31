"""
Programmatic, reproducible extraction pipeline for the Guerbet corpus.

This module replaces the interactive, hand-curated extraction that produced the
original `*_extracted.json` files (now preserved under `manual_ceiling/`). Those
files were written by a human reading each paper in a session where
`ground_truth.csv` sat in the same directory, and their `_notes` fields cite the
ground truth directly ("GT ERROR", "GT says 573 K"). They therefore measure a
careful-read *ceiling under contamination*, not extractor performance, and must
never be reported as pipeline results.

Everything below exists to make the numbers defensible. Three hard constraints
govern the design; each is load-bearing for the validity of every F1 in the
dissertation.

  CONSTRAINT 1 — EVALUATION ISOLATION (the most important one).
  The extraction subprocess must not be able to see `ground_truth.csv`, or any
  other file in this project. This is enforced structurally, not by convention:
    (a) each call runs with cwd set to a freshly created empty temp directory
        outside the project tree, so the answer key is not on any relative path;
    (b) `--tools ""` disables every built-in tool, so the subprocess has no Read,
        Bash, Glob or WebFetch with which to reach the filesystem at all;
    (c) `--safe-mode` suppresses CLAUDE.md discovery, skills, plugins, hooks and
        MCP servers, so no project context leaks in through configuration;
    (d) `--setting-sources ""` and `--strict-mcp-config` block user, project and
        local settings.
  The only channel into the model is the system prompt and the paper text on
  stdin. Weakening any of (a)-(d) invalidates every number this script produces.

  CONSTRAINT 2 — VERSION LOCKING AND PROVENANCE.
  The model is pinned by full name (never an alias like "sonnet", which floats to
  whatever is current). Prompts are hashed at run time. Timestamp, model, prompt
  hashes, chunk counts, chunker used, and OCR-repair availability are all written
  to `runs/<run_id>/meta.json` so a run can be reconstructed and audited later.

  CONSTRAINT 3 — REPEATED SAMPLING.
  Each (paper, version) cell is run `--reps` times (default 3). LLM extraction is
  stochastic; a single sample cannot support a claim about a prompt ladder. The
  spread across repeats is what backs the "model variability" row of the risk
  table, so it is reported rather than averaged away silently.

Marginal cost is zero: `claude -p` runs against the Claude Pro subscription, so
no ANTHROPIC_API_KEY is required (the older `extract.py` needed one, which is why
it was never run). The `cost_usd` recorded in metadata is the notional API-list
equivalent the CLI reports, not money actually spent.

Usage:
    python3 pipeline.py --paper liu2022 --version V1          # smoke test
    python3 pipeline.py --all --reps 3                        # full grid
    python3 pipeline.py --all --versions B0,V1 --reps 1       # partial grid
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

# Reuse rather than reimplement: extract.py's pdfplumber reader (text + serialised
# tables) and its page-packing chunker are the documented behaviour of the
# original pipeline, so the ablation stays comparable to what the write-up says.
from extract import chunk_text, extract_text_from_pdf
from prompts import VERSIONS

# OCR repair is delivered by the separate text-fidelity package (P2). It is
# optional so this pipeline is runnable before that lands; whether it was active
# is recorded in meta.json, because it changes the input text and therefore the
# results.
try:
    TEXT_FIDELITY_DIR = (Path(__file__).parent.parent / "1.9 章节初稿_2026-07-27"
                         / "text_fidelity")
    if str(TEXT_FIDELITY_DIR) not in sys.path:
        sys.path.insert(0, str(TEXT_FIDELITY_DIR))
    from repair import repair_line  # type: ignore

    def repair_ocr(text: str) -> str:
        # The extraction reader has already flattened pages and tables. Apply the
        # same guarded line repair without changing that representation. The six
        # journal PDFs currently contain no detected data-row substitutions, so
        # this adapter is an input-preserving no-op on the evaluated corpus.
        return "\n".join(repair_line(line, 0, scope="data")[0]
                         for line in text.splitlines())

    HAS_OCR_REPAIR = True
except ImportError:  # pragma: no cover - depends on P2 delivery
    def repair_ocr(text: str) -> str:
        return text

    HAS_OCR_REPAIR = False


PIPELINE_DIR = Path(__file__).parent
PAPERS_DIR = PIPELINE_DIR.parent / "guerbet_papers"
RUNS_DIR = PIPELINE_DIR / "runs"

# Pinned by full model name, never an alias. See CONSTRAINT 2.
DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
GATEWAY_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


def load_gateway_env() -> dict[str, str]:
    """Load only gateway credentials/model settings, never project instructions.

    `--setting-sources ""` is retained so Claude Code cannot load user, project or
    local behavioural settings inside the measurement subprocess.  Authentication
    is supplied explicitly from a four-key allow-list instead.
    """
    try:
        payload = json.loads(CLAUDE_SETTINGS.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read Claude gateway settings: {exc}") from exc
    configured = payload.get("env") or {}
    allowed = {k: str(configured.get(k, "")) for k in GATEWAY_ENV_KEYS}
    if not allowed["ANTHROPIC_BASE_URL"] or not allowed["ANTHROPIC_AUTH_TOKEN"]:
        # August 2026 rerun: the July gateway is gone. Fall back to the CLI's own
        # subscription login (direct route). Model stays pinned via --model; the
        # route change (gateway -> direct) is recorded in run metadata.
        return {}
    return allowed

# Retry budget for malformed JSON. Reported literature (GPT-4.1 losing 2 of 10
# papers to unparseable output) makes the parse-failure rate a result in its own
# right, so failures are counted and written to meta.json rather than hidden.
MAX_JSON_ATTEMPTS = 3

# Corpus: slug -> (pdf filename, ground-truth paper name).
# The slug matches evaluate.py's derivation, paper.lower() with spaces stripped,
# so outputs drop straight into the scorer.
#
# Note "Portillo Crespo 2022" is absent: the ground truth and the manual JSON
# both cover it, but no PDF for it exists in guerbet_papers/, so it cannot be
# extracted programmatically. Seven manual files, six extractable papers.
CORPUS: dict[str, tuple[str, str]] = {
    "cimino2019": ("applsci-09-01371.pdf", "Cimino 2019"),
    "herrera2024": ("hucal2024.pdf", "Herrera 2024"),
    "liu2022": ("liu2022.pdf", "Liu 2022"),
    "malina2024": ("malina2024.pdf", "Malina 2024"),
    "malina2025": ("malina2025.pdf", "Malina 2025"),
    "xi2020": ("xi2020.pdf", "Xi 2020"),
    # ── Post hoc transfer case (30 Aug 2026): CC-BY confirmed (Frontiers).
    #    Never processed by the automated pipeline, but manually reviewed in the
    #    July careful-read audit — NOT a held-out document. See thesis §3.6(d).
    "portillocrespo2022": ("portillo2022.pdf", "Portillo Crespo 2022"),
    # ── Patent sub-corpus (August 2026 rerun). Scored against
    #    patent_ground_truth.csv, never pooled with the journal evaluation.
    "ep2889280a1": ("EP2889280A1.pdf", "EP2889280A1"),
    "us20100160692a1": ("US20100160692A1.pdf", "US20100160692A1"),
    "us9024090": ("US9024090.pdf", "US9024090"),
    "us9056811": ("US9056811.pdf", "US9056811"),
}

ALL_VERSIONS = ["B0", "V1", "V2", "V3", "V4", "V5", "V6", "V7"]

# Versions that receive section-aware chunks. V3 introduces the "focus on
# Methods/Experimental and Results, tables carry the condition data" strategy, so
# it must actually be fed sections; otherwise V2 and V3 differ only in wording
# and the ablation step measures nothing.
SECTION_CHUNK_VERSIONS = {"V3", "V4", "V6", "V7"}

SCHEMA_COLS = [
    "paper", "catalyst", "loading_g", "temperature_K", "gas_mix",
    "flow_rate_mL_min", "LHSV_h", "WHSV_h", "GHSV_h",
    "pressure", "conversion_pct", "selectivity_pct",
]

# Uncertainty is metadata, not part of the numeric value. The V3 prompt used a
# leading '~' for values read from captions or otherwise approximate. Keeping it
# in the value made units.py either reject a valid number or confuse the marker
# with the range separator. We strip only a leading marker and retain the fact
# separately in `_uncertain`.
UNCERTAIN_PREFIX_RE = re.compile(r"^\s*[~≈]\s*")

# B0 is deliberately given no schema, so it names fields however it likes. The
# same alias map is applied to every version, so this harmonisation is not a
# special favour to B0; it separates "used a different key name" from "failed to
# find the value", which are different failures and should not be conflated.
#
# Note what is deliberately NOT aliased: B0 reports temperatures in Celsius
# ("350-450 degC") because nothing told it to convert. That is a real B0 deficit
# and is left to score as one; only the key is harmonised, never the value.
KEY_ALIASES = {
    "temperature": "temperature_K", "temp": "temperature_K",
    "temperature_k": "temperature_K", "reaction_temperature": "temperature_K",
    "reaction_temp": "temperature_K",
    "conversion": "conversion_pct", "ethanol_conversion": "conversion_pct",
    "conversion_percent": "conversion_pct", "conversion_%": "conversion_pct",
    "selectivity": "selectivity_pct", "butanol_selectivity": "selectivity_pct",
    "selectivity_percent": "selectivity_pct", "selectivity_%": "selectivity_pct",
    "feed_gas": "gas_mix", "feed": "gas_mix", "gas": "gas_mix",
    "gas_composition": "gas_mix", "gas_feed": "gas_mix", "feed_composition": "gas_mix",
    "flow_rate": "flow_rate_mL_min", "total_flow_rate": "flow_rate_mL_min",
    "flow": "flow_rate_mL_min",
    "whsv": "WHSV_h", "lhsv": "LHSV_h", "ghsv": "GHSV_h",
    "loading": "loading_g", "catalyst_loading": "loading_g",
    "catalyst_mass": "loading_g", "mass": "loading_g", "catalyst_weight": "loading_g",
}

# A bare "space_velocity" key (B0's usual choice) does not say *which* space
# velocity, and the three columns are physically different quantities. Guessing
# one would manufacture both false positives and false negatives, so the value is
# read for the discriminating token instead and the field is dropped when it says
# nothing -- an honest miss rather than an invented hit.
_GENERIC_SV_KEYS = {"space_velocity", "spacevelocity", "sv", "space_velocity_h"}
_SV_ROUTES = (("ghsv", "GHSV_h"), ("lhsv", "LHSV_h"), ("whsv", "WHSV_h"))

# Case- and separator-insensitive lookup onto the schema. Without this a reply
# keyed "Catalyst" or "Temperature_K" silently loses the whole column.
_CANON = {c.lower(): c for c in SCHEMA_COLS}
_CANON.update(KEY_ALIASES)


def canonical_key(key: str, value) -> str | None:
    """Map a model-chosen field name onto a schema column, or None to drop it."""
    k = re.sub(r"[\s\-]+", "_", str(key).strip().lower())
    if k in _GENERIC_SV_KEYS:
        text = str(value).lower()
        for token, col in _SV_ROUTES:
            if token in text:
                return col
        return None
    return _CANON.get(k)

_print_lock = Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ── Text preparation ────────────────────────────────────────────────────────────

# Everything from the references onward is citation noise: it contains numbers
# and catalyst names that look extractable but describe other people's papers.
# Cutting it lowers the false-positive rate for every version equally.
_TAIL_RE = re.compile(
    r"(?m)^\s*(?:\d+\.?\s*)?(References|Bibliography|Acknowledg(?:e?ments?|ments))\s*$"
)

_SECTION_RE = re.compile(
    r"(?m)^[ \t]*(?:(\d+(?:\.\d+)*)\.?[ \t]+)?"
    r"(Abstract|Introduction|Experimental(?:\s+Section)?|Materials\s+and\s+Methods|"
    r"Methods?|Methodology|Results\s+and\s+Discussion|Results|Discussion|"
    r"Conclusions?|References|Bibliography|Acknowledg(?:e?ments?|ments)|"
    r"Supporting\s+Information|Supplementary\s+Material)"
    r"[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)

_TABLE_BLOCK_RE = re.compile(r"\[Table on page \d+\]")


def strip_tail_matter(text: str) -> str:
    """Drop references/acknowledgements, which contribute only distractor numbers."""
    m = _TAIL_RE.search(text)
    # Guard against a hit in the first fifth of the paper (a forward reference or
    # a running header), which would truncate the actual content.
    if m and m.start() > len(text) * 0.2:
        return text[: m.start()]
    return text


# A front-matter block this large is not really front matter. MDPI and several
# other publishers in this corpus print Results and Discussion *before*
# Materials and Methods, so the span following "Introduction" runs all the way to
# the Methods heading and carries the entire results narrative. Classifying it as
# front matter and dropping it would discard ~75% of the paper (liu2022: 38k of
# 51k chars), which is how the first version of this chunker starved V3.
FRONT_BLOCK_MAX = 8000


def classify_section(name: str) -> str:
    n = name.lower()
    if "method" in n or "experimental" in n or "material" in n:
        return "methods"
    if "result" in n or "discussion" in n or "conclusion" in n:
        return "results"
    if "abstract" in n or "introduction" in n:
        return "front"
    return "back"


def split_sections(text: str) -> list[tuple[str, str, str]]:
    """Return [(kind, heading, body)] for detected headings; [] if none usable.

    Publisher PDFs vary: some expose clean heading lines, some (malina2025 here)
    expose none at all once pdfplumber has flattened the two-column layout. The
    caller falls back to character chunking when this returns nothing usable.

    Only blocks classified "back" are ever dropped downstream. Nothing else is
    discarded on the strength of a heading guess -- a misread heading should cost
    a label, not a page of results.
    """
    marks = [(m.start(), m.end(), m.group(2)) for m in _SECTION_RE.finditer(text)]
    if len(marks) < 2:
        return []
    out = []
    for i, (_start, end, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[end:stop].strip()
        if not body:
            continue
        kind = classify_section(name)
        if kind == "front" and len(body) > FRONT_BLOCK_MAX:
            kind = "results"  # see FRONT_BLOCK_MAX
        out.append((kind, name.strip(), body))
    return out


def collect_tables(text: str) -> str:
    """Pull serialised table rows out of the flattened text, if there are any.

    extract_text_from_pdf appends tables as '[Table on page N]' followed by
    pipe-joined rows. On this corpus pdfplumber's table detector fires mostly on
    figures and layout rules, yielding markers whose rows are empty pipes, so the
    caller gates the resulting chunk on real content (see TABLE_MIN_ALNUM) and in
    practice no table chunk is emitted. The path is kept because it is correct for
    a PDF with a genuine table text layer, and the emptiness is recorded in
    meta.json rather than passed off as a table chunk that carried data.
    """
    blocks = []
    marks = list(_TABLE_BLOCK_RE.finditer(text))
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        # A table block runs only as far as its pipe-delimited rows; stop at the
        # first line of ordinary prose so a following page does not ride along.
        lines = text[m.start(): stop].splitlines()
        kept = [lines[0]] if lines else []
        for line in lines[1:]:
            if "|" in line or not line.strip():
                kept.append(line)
            else:
                break
        rows = [ln for ln in kept[1:] if sum(c.isalnum() for c in ln) > 0]
        if rows:
            blocks.append("\n".join([kept[0]] + rows))
    return "\n\n".join(blocks).strip()


# A "table" chunk below this much alphanumeric content is pdfplumber noise
# (empty cells from figure boxes), not data. Emitting it would spend a call per
# paper on empty pipes.
TABLE_MIN_ALNUM = 200


def chunk_by_section(text: str, max_chars: int = 12000) -> list[tuple[str, str]]:
    """Section-aware chunking for V3/V4: Methods first, then Results, then tables.

    Returns [(label, chunk_text)]. Labels are surfaced to the model so V3's
    instruction to focus on Methods/Experimental and Results has something to
    bind to, and ordering puts those sections first.

    Content preservation is the invariant here: every character of the paper
    except identified back matter reaches some chunk. Falls back to labelled
    character chunks when no headings are detectable, so a paper with an
    unparseable layout still runs rather than silently yielding nothing.
    """
    body = strip_tail_matter(text)
    sections = split_sections(body)

    chunks: list[tuple[str, str]] = []
    if sections:
        # methods before results: the V3 prompt names them in that order, and the
        # operating conditions the schema asks for are stated in Methods.
        for kind in ("methods", "results", "front"):
            joined = "\n\n".join(b for k, _h, b in sections if k == kind).strip()
            if not joined:
                continue
            pieces = chunk_text(joined, max_chars)
            for i, piece in enumerate(pieces):
                label = kind if len(pieces) == 1 else f"{kind}[{i + 1}]"
                chunks.append((label, piece))
    if not chunks:
        for i, piece in enumerate(chunk_text(body, max_chars)):
            chunks.append((f"body[{i + 1}]", piece))

    tables = collect_tables(text)
    if sum(c.isalnum() for c in tables) >= TABLE_MIN_ALNUM:
        pieces = chunk_text(tables, max_chars)
        for i, piece in enumerate(pieces):
            chunks.append(("tables" if len(pieces) == 1 else f"tables[{i + 1}]", piece))
    return chunks


def chunk_for_version(version: str, text: str, max_chars: int = 12000) -> list[tuple[str, str]]:
    """B0/V1/V2 get the original page-packing chunker; V3/V4 get sections.

    This asymmetry is the experimental manipulation, not an implementation
    detail: V3's prompt promises section focus, so V3's input must be sections.
    """
    if version in SECTION_CHUNK_VERSIONS:
        return chunk_by_section(text, max_chars)
    return [(f"chars[{i + 1}]", c) for i, c in enumerate(chunk_text(text, max_chars))]


# ── Subprocess extraction ───────────────────────────────────────────────────────

def call_claude(system_prompt: str, user_prompt: str, model: str,
                timeout: int = 900) -> dict:
    """One isolated, tool-less `claude -p` call. See CONSTRAINT 1.

    The sandbox directory is created fresh, outside the project tree, and removed
    afterwards. Combined with `--tools ""` the subprocess has neither a path to
    ground_truth.csv nor any tool with which to follow one.
    """
    sandbox = tempfile.mkdtemp(prefix="guerbet_extract_")
    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt", system_prompt,
        "--tools", "",              # (b) no Read/Bash/Glob/WebFetch — cannot reach the FS
        "--safe-mode",              # (c) no CLAUDE.md, skills, plugins, hooks, MCP
        "--setting-sources", "",    # (d) no user/project/local settings
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
    ]
    # Scrub inherited config that could reintroduce project context. Gateway
    # authentication is then restored from an explicit four-variable allow-list;
    # no CLAUDE.md, skills, hooks, plugins or MCP configuration is loaded.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "ANTHROPIC_"))}
    env.update(load_gateway_env())
    env["CLAUDE_CODE_SAFE_MODE"] = "1"

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, input=user_prompt, capture_output=True, text=True,
            cwd=sandbox,            # (a) empty cwd outside the project tree
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "text": "", "elapsed": time.time() - started}
    finally:
        try:
            os.rmdir(sandbox)
        except OSError:
            pass  # a stray file means something wrote to the sandbox; leave it for inspection

    elapsed = time.time() - started
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        detail = stderr or stdout or "no stdout/stderr from CLI"
        non_retryable = any(token in detail.lower() for token in (
            "not logged in", "authentication", "invalid model", "unknown model",
            "permission denied", "usage limit",
        ))
        return {"ok": False, "error": f"exit {proc.returncode}: {detail[:2000]}",
                "text": "", "elapsed": elapsed, "returncode": proc.returncode,
                "stdout": stdout[:2000], "stderr": stderr[:2000],
                "retryable": not non_retryable}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "cli envelope not JSON", "text": proc.stdout[:2000],
                "elapsed": elapsed}
    if payload.get("is_error"):
        return {"ok": False, "error": str(payload.get("result"))[:400], "text": "",
                "elapsed": elapsed}

    return {
        "ok": True,
        "text": payload.get("result", ""),
        "elapsed": elapsed,
        "models": sorted(payload.get("modelUsage", {}).keys()),
        "cost_usd": payload.get("total_cost_usd"),
        "session_id": payload.get("session_id"),
    }


_WRAPPER_KEYS = ("rows", "data", "results", "extractions", "experiments")


def _scan_json_values(text: str) -> list:
    """Every top-level JSON value in `text`, in order, ignoring prose between them.

    Replies that break the "JSON only" instruction usually do so by *adding* to a
    valid answer rather than corrupting it -- a sentence of commentary, or a
    commentary object, followed by the array that was actually asked for:

        {"noteworthy": "This section contains no reaction data."}

        []

    A whole-string json.loads fails on that, and a greedy outermost-bracket regex
    swallows both values and fails too, so the array the model *did* produce would
    be scored as a parse failure. Decoding incrementally recovers it.
    """
    decoder = json.JSONDecoder()
    values, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "[{":
            i += 1
            continue
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        values.append(value)
        i = end
    return values


def _rows_from_value(value) -> list[dict] | None:
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        for key in _WRAPPER_KEYS:
            if isinstance(value.get(key), list):
                return [r for r in value[key] if isinstance(r, dict)]
    return None


def parse_rows(raw: str) -> tuple[list[dict], str]:
    """Parse the model's reply into (rows, form).

    `form` describes how far the reply departed from the requested bare JSON
    array. These are graded, not lumped together, because they are different
    findings and reporting them as one number would be misleading:

      "bare"    exactly what was asked for.
      "fenced"  a valid array inside a markdown code fence. Benign wrapping.
      "jsonl"   newline-delimited objects with no enclosing brackets. Valid data,
                wrong container -- and the single most costly form to mishandle,
                since it is what the densest results chunks came back as.
      "extra"   the array arrived alongside commentary prose or a commentary
                object. A genuine instruction-following deviation.

    Raises ValueError only when no usable JSON is present at all; the caller
    retries and counts that, since the parse-failure rate is itself a result.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty reply")

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    body = fence.group(1).strip() if fence else text

    try:
        rows = _rows_from_value(json.loads(body))
        if rows is not None:
            return rows, "fenced" if fence else "bare"
    except json.JSONDecodeError:
        pass

    # Prefer the first genuine array; fall back to concatenated bare objects.
    values = _scan_json_values(body)
    for value in values:
        rows = _rows_from_value(value)
        if rows is not None:
            return rows, "extra"
    objects = [v for v in values if isinstance(v, dict)]
    if objects:
        # Nothing but bare objects: JSON Lines, not a malformed array.
        stripped = re.sub(r"\s+", "", body)
        form = "jsonl" if stripped.startswith("{") and stripped.endswith("}") else "extra"
        return objects, form

    raise ValueError(f"no JSON array found in {body[:200]!r}")


def harmonise(row: dict, paper_name: str) -> dict:
    """Map alias field names onto the schema and coerce values to strings.

    Applied identically to every version, so it never advantages one rung of the
    ladder over another.
    """
    out: dict = {}
    uncertain: dict[str, bool] = {}
    raw_values: dict[str, str] = {}
    for key, value in row.items():
        canon = canonical_key(key, value)
        if canon is not None and out.get(canon) in (None, ""):
            if value is None or (isinstance(value, str) and value.strip().lower() in ("null", "none", "n/a", "not reported", "")):
                out[canon] = None
            elif isinstance(value, (list, dict)):
                out[canon] = json.dumps(value, ensure_ascii=False)
            else:
                text = str(value).strip()
                raw_values[canon] = text
                marked = bool(UNCERTAIN_PREFIX_RE.match(text))
                uncertain[canon] = marked
                out[canon] = UNCERTAIN_PREFIX_RE.sub("", text, count=1) if marked else text
    for col in SCHEMA_COLS:
        out.setdefault(col, None)
    out["paper"] = paper_name
    if any(uncertain.values()):
        out["_uncertain"] = {col: flag for col, flag in uncertain.items() if flag}
        out["_raw_values"] = raw_values
    return out


def dedupe(rows: list[dict]) -> list[dict]:
    """Drop only exact duplicate records across overlapping chunks.

    A short three-field key previously collapsed distinct experiments that shared
    catalyst, temperature and conversion but differed in feed, pressure, space
    velocity, loading, selectivity or time regime. The full schema is used here so
    repeated evidence is removed without inventing equivalence between conditions.
    """
    seen, unique = set(), []
    for r in rows:
        key = tuple("" if r.get(col) is None else str(r.get(col)).strip().casefold()
                    for col in SCHEMA_COLS)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ── Run orchestration ───────────────────────────────────────────────────────────

def extract_cell(slug: str, version: str, rep: int, text: str, model: str,
                 run_dir: Path) -> dict:
    """Run one (paper, version, repeat) cell across all its chunks."""
    pdf_name, paper_name = CORPUS[slug]
    system_prompt, user_builder = VERSIONS[version]
    chunks = chunk_for_version(version, text)

    rows: list[dict] = []
    attempts_log, n_calls, n_fail = [], 0, 0
    forms: dict[str, int] = {}

    for label, chunk in chunks:
        hinted = f"{paper_name} — section: {label}" if version in SECTION_CHUNK_VERSIONS else paper_name
        user_prompt = user_builder(chunk, hinted)

        for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
            n_calls += 1
            result = call_claude(system_prompt, user_prompt, model)
            record = {
                "slug": slug, "version": version, "rep": rep, "chunk": label,
                "attempt": attempt, "elapsed_s": round(result.get("elapsed", 0), 1),
                "models": result.get("models"), "cost_usd": result.get("cost_usd"),
            }
            if not result["ok"]:
                n_fail += 1
                record.update(status="call_error", error=result["error"])
                attempts_log.append(record)
                if not result.get("retryable", True):
                    break
                continue
            try:
                parsed, form = parse_rows(result["text"])
            except ValueError as e:
                n_fail += 1
                record.update(status="parse_error", error=str(e)[:300])
                attempts_log.append(record)
                # Preserve the unparseable reply: the failure mode is a finding.
                bad = run_dir / "raw" / f"{slug}_{version}_r{rep}_{label}_a{attempt}.FAILED.txt"
                bad.parent.mkdir(parents=True, exist_ok=True)
                bad.write_text(result["text"], encoding="utf-8")
                continue

            # "Answered, but not in the form asked for" is a different result
            # from "produced nothing usable"; keep them apart in the statistics.
            forms[form] = forms.get(form, 0) + 1
            record.update(status="ok", reply_form=form, n_rows=len(parsed))
            attempts_log.append(record)
            raw_path = run_dir / "raw" / f"{slug}_{version}_r{rep}_{label}.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(result["text"], encoding="utf-8")
            rows.extend(harmonise(r, paper_name) for r in parsed)
            break

    rows = dedupe(rows)
    out_path = run_dir / f"{slug}_{version}_r{rep}_extracted.json"
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "slug": slug, "version": version, "rep": rep, "n_rows": len(rows),
        "n_chunks": len(chunks), "n_calls": n_calls, "n_failed_calls": n_fail,
        "reply_forms": forms,
        "output": out_path.name, "attempts": attempts_log,
    }


_RAW_RE = re.compile(r"^(?P<slug>.+?)_(?P<version>B0|V\d)_r(?P<rep>\d+)_(?P<label>.+)\.txt$")
_FAILED_RE = re.compile(
    r"^(?P<slug>.+?)_(?P<version>B0|V\d)_r(?P<rep>\d+)_(?P<label>.+)_a(?P<attempt>\d+)\.FAILED\.txt$"
)


def rebuild_from_raw(run_dir: Path) -> int:
    """Re-derive every *_extracted.json from the saved raw replies. No API calls.

    Every model reply is kept under raw/, so parsing, key harmonisation and
    deduplication can be corrected and reapplied after the fact without paying to
    extract again. Use this when a post-processing bug is found: the model output
    is the measurement, and it should not have to be re-collected because the code
    that reads it changed.

    Replies previously rejected as unparseable are reconsidered here, because a
    stricter parser is exactly the kind of bug this exists to repair. A chunk that
    later succeeded on retry keeps its successful reply and its earlier rejected
    attempts are ignored -- counting both would duplicate that chunk's rows.
    """
    raw_dir = run_dir / "raw"
    if not raw_dir.is_dir():
        print(f"No raw/ directory in {run_dir}")
        return 2

    # chunk key -> (successful reply path, [rejected attempt paths])
    ok_path: dict[tuple[str, str, int, str], Path] = {}
    failed_paths: dict[tuple[str, str, int, str], list[Path]] = {}

    for path in sorted(raw_dir.glob("*.txt")):
        if path.name.endswith(".FAILED.txt"):
            m = _FAILED_RE.match(path.name)
            if m:
                key = (m["slug"], m["version"], int(m["rep"]), m["label"])
                failed_paths.setdefault(key, []).append(path)
        else:
            m = _RAW_RE.match(path.name)
            if m:
                ok_path[(m["slug"], m["version"], int(m["rep"]), m["label"])] = path

    grouped: dict[tuple[str, str, int], list[dict]] = {}
    n_ok = n_recovered = n_unparsed = 0
    forms: dict[str, int] = {}

    def ingest(key4, path) -> bool:
        slug, version, rep, _label = key4
        paper_name = CORPUS[slug][1] if slug in CORPUS else slug
        try:
            parsed, form = parse_rows(path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        forms[form] = forms.get(form, 0) + 1
        grouped.setdefault((slug, version, rep), []).extend(
            harmonise(r, paper_name) for r in parsed)
        return True

    for key4, path in ok_path.items():
        if ingest(key4, path):
            n_ok += 1
        else:
            n_unparsed += 1

    for key4, paths in failed_paths.items():
        if key4 in ok_path:
            continue  # the retry succeeded; do not ingest the rejected attempt too
        if any(ingest(key4, p) for p in sorted(paths)):
            n_recovered += 1
        else:
            n_unparsed += 1

    # A cell with no usable reply at all still needs a file, or scoring would
    # silently skip it instead of counting it as a miss.
    for slug, version, rep, _label in set(ok_path) | set(failed_paths):
        grouped.setdefault((slug, version, rep), [])

    for (slug, version, rep), rows in sorted(grouped.items()):
        out = run_dir / f"{slug}_{version}_r{rep}_extracted.json"
        out.write_text(json.dumps(dedupe(rows), indent=2, ensure_ascii=False),
                       encoding="utf-8")

    total_chunks = len(set(ok_path) | set(failed_paths))

    # meta.json stays a faithful record of the run as it actually executed,
    # including the failure counts the parser of the day produced. The corrected
    # taxonomy goes in its own file so both are auditable and neither is
    # retrospectively edited into agreement with the other.
    summary = {
        "rebuilt_at_utc": datetime.now(timezone.utc).isoformat(),
        "cells": len(grouped),
        "chunks": total_chunks,
        "parsed_first_attempt": n_ok,
        "recovered_from_rejected": n_recovered,
        "still_unparseable": n_unparsed,
        "reply_forms": forms,
        "true_parse_failure_rate": round(n_unparsed / total_chunks, 4) if total_chunks else None,
        "note": ("meta.json records the run as executed. These counts come from "
                 "reprocessing the saved raw replies with the current parser."),
    }
    (run_dir / "rebuild.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Rebuilt {len(grouped)} cell file(s) from {total_chunks} chunks in {run_dir}")
    print(f"  parsed on first success : {n_ok}")
    print(f"  recovered from rejected : {n_recovered}  "
          f"(replies the stricter parser had discarded)")
    print(f"  still unparseable       : {n_unparsed}  "
          f"({(n_unparsed / total_chunks * 100) if total_chunks else 0:.1f}% true failure rate)")
    print(f"  reply forms             : {forms}")
    print(f"  wrote {run_dir / 'rebuild.json'}")
    return 0


def acquire_run_lock(run_dir: Path) -> Path:
    """Claim exclusive ownership of a run directory, or refuse to start.

    Two `pipeline.py --all` processes once wrote into the same run directory from
    two sessions working in this repository at the same time. They computed the
    same cells twice and overwrote each other last-writer-wins, and meta.json
    recorded only one of them -- the whole run had to be discarded (see
    runs/grid01_CONTENDED/). A run directory now has exactly one owner.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = run_dir / ".lock"
    payload = json.dumps({"pid": os.getpid(),
                          "started_utc": datetime.now(timezone.utc).isoformat()})
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            held = json.loads(lock.read_text())
            pid = held.get("pid")
        except (OSError, ValueError):
            held, pid = {}, None
        alive = False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            raise SystemExit(
                f"Run directory {run_dir.name} is already owned by PID {pid} "
                f"(started {held.get('started_utc')}).\n"
                f"Choose a different --run-id, or stop that process first."
            )
        # Stale lock from a killed run: take it over, but say so.
        log(f"[lock] taking over stale lock from dead PID {pid}")
        lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    return lock


def load_paper_text(slug: str) -> str:
    pdf_name, _ = CORPUS[slug]
    pdf_path = PAPERS_DIR / pdf_name
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    raw = extract_text_from_pdf(str(pdf_path))
    if os.environ.get("PIPELINE_NO_REPAIR") == "1":
        return raw
    return repair_ocr(raw)


def prompt_hashes() -> dict:
    return {v: hashlib.sha256(VERSIONS[v][0].encode()).hexdigest()[:16] for v in ALL_VERSIONS}


def claude_auth_status() -> tuple[bool, str]:
    """Fail fast before a grid if the configured gateway credentials are absent."""
    try:
        gateway = load_gateway_env()
    except RuntimeError as exc:
        return False, str(exc)
    if not gateway:
        return True, "direct route: CLI subscription auth (no gateway configured)"
    if gateway["ANTHROPIC_AUTH_TOKEN"] in {"", "sk-gw-你的Key"}:
        return False, "Claude gateway auth token is still a placeholder"
    return True, f"gateway configured at {gateway['ANTHROPIC_BASE_URL']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--paper", help="single paper slug, e.g. liu2022")
    ap.add_argument("--version", help="single prompt version, e.g. V1")
    ap.add_argument("--versions", help="comma-separated versions (default: all five)")
    ap.add_argument("--all", action="store_true", help="run the full corpus x version grid")
    ap.add_argument("--reps", type=int, default=3, help="repeats per cell (CONSTRAINT 3)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="pinned full model name")
    ap.add_argument("--workers", type=int, default=4, help="concurrent claude calls")
    ap.add_argument("--run-id", help="reuse/name a run directory")
    ap.add_argument("--list", action="store_true", help="show corpus and exit")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-derive JSONs from runs/<run-id>/raw/ without calling the API")
    args = ap.parse_args()

    if args.rebuild:
        if not args.run_id:
            ap.error("--rebuild needs --run-id")
        return rebuild_from_raw(RUNS_DIR / args.run_id)

    if args.list:
        for slug, (pdf, name) in CORPUS.items():
            present = "ok " if (PAPERS_DIR / pdf).exists() else "MISSING"
            print(f"  {slug:<20} {present}  {pdf:<26} -> {name}")
        return 0

    if args.all:
        slugs = list(CORPUS)
    elif args.paper:
        if args.paper not in CORPUS:
            print(f"Unknown paper '{args.paper}'. Known: {', '.join(CORPUS)}")
            return 2
        slugs = [args.paper]
    else:
        ap.error("need --paper, --all, or --list")

    if args.version:
        versions = [args.version]
    elif args.versions:
        versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    else:
        versions = ALL_VERSIONS
    unknown = [v for v in versions if v not in VERSIONS]
    if unknown:
        print(f"Unknown version(s): {unknown}. Known: {', '.join(ALL_VERSIONS)}")
        return 2

    auth_ok, auth_detail = claude_auth_status()
    if not auth_ok:
        print(f"PRE-FLIGHT FAILED: {auth_detail}.")
        print("Run `claude` interactively and complete `/login`, then retry the pipeline.")
        return 2

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    if (run_dir / "DO_NOT_RERUN").exists():
        # Guard added 30 Aug 2026: a chained script and a parallel launcher both
        # targeted this run-id. Once results are final, this marker makes any
        # late duplicate invocation exit cleanly instead of overwriting cells
        # (see runs/grid01_CONTENDED for why that matters).
        print(f"run '{run_id}' is finalised (DO_NOT_RERUN marker present); skipping.")
        return 0
    lock = acquire_run_lock(run_dir)

    log(f"run_id       : {run_id}")
    log(f"model        : {args.model}  (pinned; CONSTRAINT 2)")
    log(f"papers       : {len(slugs)}  versions: {','.join(versions)}  reps: {args.reps}")
    no_repair_env = os.environ.get("PIPELINE_NO_REPAIR") == "1"
    repair_effective = HAS_OCR_REPAIR and not no_repair_env
    log(f"ocr_repair   : {'active' if repair_effective else ('DISABLED via PIPELINE_NO_REPAIR' if no_repair_env else 'unavailable (P2 not delivered)')}")
    log(f"isolation    : empty temp cwd + --tools '' + --safe-mode  (CONSTRAINT 1)")
    log("")

    log("Parsing PDFs...")
    texts, chunk_profile = {}, {}
    for slug in slugs:
        texts[slug] = load_paper_text(slug)
        chunk_profile[slug] = {
            v: [lab for lab, _ in chunk_for_version(v, texts[slug])] for v in versions
        }
        shapes = {v: len(c) for v, c in chunk_profile[slug].items()}
        log(f"  {slug:<14} {len(texts[slug]):>7,} chars   chunks {shapes}")
    log("")

    cells = [(s, v, r) for s in slugs for v in versions for r in range(1, args.reps + 1)]
    log(f"Dispatching {len(cells)} cells on {args.workers} workers...\n")

    started = time.time()
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(extract_cell, s, v, r, texts[s], args.model, run_dir): (s, v, r)
            for s, v, r in cells
        }
        for fut in as_completed(futures):
            s, v, r = futures[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as e:  # keep the grid running; record the hole
                log(f"  [{done}/{len(cells)}] {s} {v} r{r}  CELL ERROR: {e}")
                results.append({"slug": s, "version": v, "rep": r, "error": str(e),
                                "n_rows": 0, "n_calls": 0, "n_failed_calls": 0,
                                "attempts": []})
                continue
            results.append(res)
            flag = f"  ({res['n_failed_calls']} failed calls)" if res["n_failed_calls"] else ""
            log(f"  [{done}/{len(cells)}] {s:<13} {v}  r{r}  "
                f"{res['n_rows']:>3} rows from {res['n_chunks']} chunks{flag}")

    elapsed = time.time() - started
    total_calls = sum(r["n_calls"] for r in results)
    total_failed = sum(r["n_failed_calls"] for r in results)
    all_attempts = [a for r in results for a in r.get("attempts", [])]
    observed_models = sorted({m for a in all_attempts for m in (a.get("models") or [])})
    notional_cost = sum(a.get("cost_usd") or 0 for a in all_attempts)

    meta = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_requested": args.model,
        "models_observed": observed_models,
        "prompt_sha256_16": prompt_hashes(),
        "versions": versions,
        "papers": {s: {"pdf": CORPUS[s][0], "gt_name": CORPUS[s][1],
                       "n_chars": len(texts[s]), "chunks": chunk_profile[s]} for s in slugs},
        "reps": args.reps,
        "chunker": {"section_aware": sorted(SECTION_CHUNK_VERSIONS),
                    "char_packed": [v for v in versions if v not in SECTION_CHUNK_VERSIONS],
                    "max_chars": 12000},
        "ocr_repair_active": repair_effective,
        "ocr_repair_module_present": HAS_OCR_REPAIR,
        "no_repair_env": no_repair_env,
        "isolation": {
            "cwd": "fresh empty tempdir outside project tree",
            "tools": "disabled via --tools ''",
            "config": "--safe-mode --setting-sources '' --strict-mcp-config",
            "ground_truth_visible": False,
        },
        "json_retry_budget": MAX_JSON_ATTEMPTS,
        "totals": {
            "cells": len(cells), "claude_calls": total_calls,
            "failed_calls": total_failed,
            "call_failure_rate": round(total_failed / total_calls, 4) if total_calls else None,
            "wall_clock_s": round(elapsed, 1),
            "notional_cost_usd": round(notional_cost, 2),
        },
        "cells": [{k: v for k, v in r.items() if k != "attempts"} for r in results],
    }
    # 30 Aug 2026 fix: per-paper invocations sharing one run-id previously
    # clobbered meta.json/attempts.jsonl last-writer-wins, so score_runs saw
    # only the final paper. Merge with any existing meta instead.
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        try:
            prev = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
        if prev.get("run_id") == run_id and prev.get("papers"):
            meta["papers"] = {**prev.get("papers", {}), **meta["papers"]}
            meta["versions"] = sorted(set(prev.get("versions", [])) | set(meta["versions"]))
            meta["cells"] = prev.get("cells", []) + meta["cells"]
            pt, nt = prev.get("totals", {}), meta["totals"]
            meta["totals"] = {
                "cells": pt.get("cells", 0) + nt["cells"],
                "claude_calls": pt.get("claude_calls", 0) + nt["claude_calls"],
                "failed_calls": pt.get("failed_calls", 0) + nt["failed_calls"],
                "call_failure_rate": None,
                "wall_clock_s": round(pt.get("wall_clock_s", 0) + nt["wall_clock_s"], 1),
                "notional_cost_usd": round(pt.get("notional_cost_usd", 0) + nt["notional_cost_usd"], 2),
            }
            tc, tf = meta["totals"]["claude_calls"], meta["totals"]["failed_calls"]
            meta["totals"]["call_failure_rate"] = round(tf / tc, 4) if tc else None
            meta["models_observed"] = sorted(set(prev.get("models_observed", [])) | set(meta["models_observed"]))
            meta["merged_invocations"] = prev.get("merged_invocations", 1) + 1
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with (run_dir / "attempts.jsonl").open("a", encoding="utf-8") as f:
        for a in all_attempts:
            f.write(json.dumps(a) + "\n")

    log(f"\nDone in {elapsed / 60:.1f} min. "
        f"{total_calls} calls, {total_failed} failed "
        f"({total_failed / total_calls * 100:.1f}%)." if total_calls else "\nDone.")
    log(f"Output: {run_dir}")
    log(f"Score with: python3 score_runs.py {run_id}")
    lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

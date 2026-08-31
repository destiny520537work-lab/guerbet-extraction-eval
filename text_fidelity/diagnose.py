#!/usr/bin/env python3
"""
diagnose.py -- Text-layer fidelity diagnosis for chemistry PDFs.

Purpose
-------
Detect systematic OCR digit->letter substitution (0->O, 5->S, 1->l/I, 8->B,
6->G) in the extracted text layer of a PDF, decide whether the text layer is
"born-digital" or "scanned + OCR", and separate corruption that lands on real
experimental data from corruption that only affects bibliographic strings
(patent numbers, citations), which is harmless for downstream extraction.

Why this matters
----------------
A downstream LLM cannot repair this class of error. Given the token "4S.O" the
model can only (a) hallucinate a plausible value such as 45.0, which happens to
be right here but is an ungrounded guess, or (b) skip the cell, producing a
silent omission. Either way the failure is introduced before the model is ever
called, so it must be measured -- and fixed -- at the parsing stage.

Usage
-----
    python3 diagnose.py <file.pdf> [<file2.pdf> ...] [--json out.json]
                                   [--markdown out.md] [--samples N]
    python3 diagnose.py --dir <folder>        # every *.pdf in folder

Design notes
------------
Detection is deliberately three-layered, because the naive rule "replace O with
0 near digits" destroys chemistry: in MgO, Al2O3, H2O and CO3 the letter O is
oxygen, not a mis-read zero. The layers are:

  1. Character-class gate. A candidate token may contain ONLY characters from
     the OCR-confusable set {O, S, B, G, I, l} plus digits and numeric
     punctuation. Any other letter disqualifies the token outright, which is
     what protects MgO (M, g), H2O (H), Al2O3 (A), CO3 (C), TiO2 (T, i),
     SiO2 (S, i -- lowercase i is not in the confusable set).

  2. Explicit chemical / lexical deny-list. A handful of real formulae are
     built purely from confusable characters (SO2, SO3, SO4, B2O3, BO3, GO,
     IO3, OsO4 uppercased) as are a few English words (ISO, SOS, BOB). These
     are never touched, regardless of context.

  3. Line context. Tokens that carry no digit and no decimal point at all
     (e.g. "OSO", "S.O" has a point, "SO" does not) are only accepted inside a
     numeric-dominant table row and only when flanked by numeric neighbours.
     This is what stops "SO" in prose from becoming "50".

Every accepted candidate must additionally parse as a well-formed number after
substitution; otherwise it is discarded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass, field, asdict
from typing import Iterable

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("pdfplumber is required:  python3 -m pip install pdfplumber")


# --------------------------------------------------------------------------
# 1. The confusion model
# --------------------------------------------------------------------------

# Digit <- letter substitutions observed in USPTO full-text OCR of monospaced
# (Courier) patent print. Direction is letter -> digit because OCR emitted the
# letter where the source document printed the digit.
CONFUSION_MAP = {
    "O": "0",   # capital O  -> zero        (by far the most frequent)
    "S": "5",   # capital S  -> five
    "l": "1",   # lowercase L -> one
    "I": "1",   # capital i  -> one
    "B": "8",   # capital B  -> eight
    "G": "6",   # capital G  -> six
}
CONFUSABLE = set(CONFUSION_MAP)

# Characters allowed inside a numeric token besides digits and confusables.
NUMERIC_PUNCT = set(".,%()-+/±–—:;*")

# Tokens that are built only from confusable characters but are NOT numbers.
# Chemistry first -- these are the ones that would silently corrupt a dataset.
CHEMICAL_DENY = {
    "SO", "SO2", "SO3", "SO4", "SOX", "SO42-", "SO3H",
    "OS", "OSO4", "OSO2",
    "B2O3", "BO3", "BO2", "BO", "B2O",
    "GO", "IO", "IO3", "IO4", "IO4-",
    "OB", "BS", "SB", "BI", "GI", "OI", "IB", "SI", "IS",
    "OG", "GS", "SG",
    "O2", "O3",   # dioxygen / ozone, and the tail of a split "Al2 O3"
}
# All-caps English / abbreviation tokens made only of confusable characters.
LEXICAL_DENY = {
    "ISO", "SOS", "BOB", "GOB", "SOB", "BIOS", "GIS", "SIB", "OBI",
    "IBS", "BOS", "SIS", "GIG", "BIG", "LOGS", "GOSS", "BOSS",
    "II", "III", "IV", "OO",            # roman numerals / noise
}

# Single-character tokens are almost always genuine letters (list markers,
# initials, the element symbol O). The one exception worth recovering is a bare
# capital "O" standing for the value 0, which is common in patent yield and
# selectivity tables (e.g. "Y-Al2O3 93 O O O 1 65" in US9024090). It is only
# ever accepted inside a confirmed numeric data row with a numeric neighbour;
# "S", "B", "G", "I" and "l" alone are never repaired.
SINGLE_CHAR_ALLOWED = {"O"}
DENY = CHEMICAL_DENY | LEXICAL_DENY

# A well-formed number after repair: optional sign/bracket, digits, at most one
# decimal separator, optional trailing unit punctuation.
CLEAN_NUMBER_RE = re.compile(r"^[-+(]?\d{1,6}(?:[.,]\d{1,6})?[)%]?[.,;:]?$")

# Strong bibliographic markers. If a line carries one of these, corruption on
# that line is a citation-string artefact and is irrelevant to data extraction.
CITATION_MARKERS = [
    re.compile(r"\b(?:US|EP|WO|JP|CN|DE|FR|GB|KR|CA|AU|RU|TW)\s?[\dOSlIBG]{2}[\dOSlIBG,/\s\-]{3,}"),
    re.compile(r"\b[AB][12]\b"),                       # kind codes A1 / B2
    re.compile(r"(?i)\b(?:patent|publication|application|appl|pub\.|ser\.|no\.)"),
    re.compile(r"(?i)\b(?:et\s?al|cited by|references|bibliograph|sheet\s+\d)"),
    re.compile(r"\b\d{1,2}/\d{3},?\s?\d{3}\b"),        # US serial numbers
    re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\.?\s+\d{1,2},\s*(?:19|20)\d{2}"),
    # IPC / CPC classification blocks: "B01J 23/00 (2006.01)", "Int. Cl.",
    # "Field of Classification Search". The "(2006.01)" scheme-version tag is
    # the reliable giveaway.
    re.compile(r"\(20[\dOSlIB]{2}\.[\dOSlIB]{2}\)"),
    re.compile(r"(?i)\b(?:int\.?\s*cl|u\.?s\.?\s*cl|classification|cpc\b|ipc\b)"),
]

# IPC/CPC class codes such as "B01J", "C07C" survive OCR as "BOI.", "CO7C".
# The leading letter there is a genuine letter, not a mis-read 8 or 6, so these
# tokens must never enter the repair path. Real numeric tokens never begin with
# a letter in A-H followed by an O/0, so this shape is safe to exclude.
CPC_CODE_RE = re.compile(r"^[A-H][O0oQ][\dOSlIB][A-Z]?\.?$")

# ---------------------------------------------------------------------------
# False-positive guards derived from auditing the corpus by hand. Each of these
# shapes is built only from confusable characters, so the character-class gate
# lets it through, yet in every observed case the letter is a genuine letter.
# ---------------------------------------------------------------------------

# (a) Sample / run / catalyst identifiers in an experiment table: "B101",
#     "B102" ... EP2889280A1 labels its runs this way and they sit in otherwise
#     perfectly numeric rows, so row context cannot save us -- the shape must.
#     A mis-read number never looks like this: OCR damage in these documents
#     either keeps a leading digit ("2OO", "1O.S") or carries a decimal point
#     ("O.90", "S.O"). A leading letter followed by two or more digits and no
#     decimal point is an identifier.
IDENTIFIER_RE = re.compile(r"^[A-Za-z][\dOSlIBG]*\d[\dOSlIBG]*$")

# (b) Table / figure panel labels: "1B", "2B", "5B,", "(300B)". Applied only
#     outside numeric data rows, so a genuine "4S" inside a table is still
#     repairable.
PANEL_LABEL_RE = re.compile(r"^\(?\d+[A-Z]\)?[.,;]?$")

# (c) Author initials in reference lists: "S.;", "G.", "I.". A single letter
#     followed by a period is never a number in this corpus. The period is
#     required, so a bare "O" meaning zero (see SINGLE_CHAR_ALLOWED) survives.
INITIAL_RE = re.compile(r"^[A-Za-z]\.[;,]?$")


# (d) THE OXYGEN GUARD -- the single most dangerous confusion in this corpus.
#     pdfplumber renders subscripted catalyst formulae as separate tokens, so
#     "Mg3Al1O" arrives as "Mg Al O" or "c-Mg 3 Al 1 O". A bare "O" there is
#     oxygen; rewriting it to 0 would silently corrupt the catalyst identity.
#     But a bare "O" is genuinely a mis-read zero in patent yield tables, e.g.
#     "Y-Al2O3 93 O O O 1 65" (US9024090 p9), where the formula is one token
#     and the zeros follow a real measurement.
#
#     The discriminator is a *formula chain*: walking left from the candidate,
#     if an element symbol can be reached by passing only through plausible
#     stoichiometric subscripts (integers <= 20), the O belongs to a formula.
#     If a real measurement (a number > 20, or a decimal) is hit first, the
#     chain is broken and the O is a zero.
#
#         "Mg Al O 85 ..."       left = Al            -> element     -> oxygen
#         "c-Mg 3 Al 1 O 1395"   left = 1, then Al    -> element     -> oxygen
#         "Y-Al2O3 93 O O O ..." left = 93 (>20)      -> broken      -> zero
#         "Example 1 ... 84 8 O" left = 8, then 84    -> broken      -> zero
SUBSCRIPT_MAX = 20

# Two-letter element symbols relevant to heterogeneous catalysis. The
# OCR-confusable letters O, S, B, I and G are deliberately NOT usable as
# single-letter evidence, so "Comp. B" (comparative example B) is not mistaken
# for boron and "S" is not mistaken for sulfur.
_TWO_LETTER = [
    "Mg", "Al", "Ca", "Zn", "Ti", "Si", "Zr", "Cu", "Ni", "Co", "La", "Ba",
    "Sr", "Fe", "Mn", "Cr", "Cs", "Rb", "Pd", "Pt", "Ru", "Rh", "Ag", "Sn",
    "Ce", "Nb", "Mo", "Hf", "Ta", "Li", "Be", "Na", "Cl", "Br", "Sc", "Ga",
    "Ge", "Se", "In", "Sb", "Te", "Cd", "Ir", "Au", "Pb", "Bi", "Th", "Yb",
    "Er", "Nd", "Sm", "Eu", "Gd", "Pr", "Ho", "Tm", "Lu", "Re", "Os", "Tl",
    "Hg", "As", "Ar", "Ne", "He", "Kr", "Xe", "Ac", "Pa", "Np", "Pu", "Ru",
]
_BOUNDARY = r"[\d\-()·•/,\.]"
# A symbol only counts when it sits at a chemically plausible boundary: start of
# token, or next to a digit / bracket / hyphen / another capital. This stops the
# "Ca" inside "Catalyst" and the "In" inside "Int" from counting as elements.
ELEMENT_2_RE = re.compile(
    rf"(?:^|{_BOUNDARY}|[A-Z])(?:{'|'.join(_TWO_LETTER)})(?=$|{_BOUNDARY}|[A-Z])")
ELEMENT_1_RE = re.compile(rf"(?:^|{_BOUNDARY})[KPCNHFVWYU](?=$|{_BOUNDARY}|[A-Z])")


def has_element(core: str) -> bool:
    """True when the token carries a recognisable element symbol."""
    return bool(ELEMENT_2_RE.search(core) or ELEMENT_1_RE.search(core))


def in_formula_chain(tokens: list[str], i: int, max_steps: int = 4) -> bool:
    """
    Walk left from token i through stoichiometric subscripts looking for an
    element symbol. See the OXYGEN GUARD note above.
    """
    # An element symbol immediately to the right also implies a formula.
    if i + 1 < len(tokens) and has_element(strip_edges(tokens[i + 1])):
        return True
    j, steps = i - 1, 0
    while j >= 0 and steps < max_steps:
        core = strip_edges(tokens[j])
        if not core:
            j -= 1
            continue
        if has_element(core):
            return True
        if re.fullmatch(r"\d{1,2}", core) and int(core) <= SUBSCRIPT_MAX:
            j -= 1
            steps += 1
            continue
        return False                      # a real measurement: chain broken
    return False


def is_identifier(core: str) -> bool:
    """Guard (a): leading letter + >=2 digits + no decimal point => run ID."""
    if "." in core or "," in core:
        return False
    if not core[:1].isalpha():
        return False
    return len(re.findall(r"\d", core)) >= 2 and bool(IDENTIFIER_RE.match(core))

# Formula-ish signature: an element symbol followed by a subscript digit, or a
# parenthesised group. Used to mark a line as chemical context.
FORMULA_RE = re.compile(r"[A-Z][a-z]?\d|\([A-Z][a-z]?[A-Z]?\)\d?|\bH2O\b|\bwt\s?%")


# --------------------------------------------------------------------------
# 2. Token- and line-level classification
# --------------------------------------------------------------------------

def repair_token_chars(tok: str) -> str:
    """Pure character substitution, no decimal-point reasoning."""
    return "".join(CONFUSION_MAP.get(c, c) for c in tok)


def strip_edges(tok: str) -> str:
    """Drop leading/trailing punctuation so 'S.O,' matches like 'S.O'."""
    return tok.strip("(),;:*[]‘’“”\"'")


def is_clean_number(tok: str) -> bool:
    return bool(CLEAN_NUMBER_RE.match(strip_edges(tok)))


def token_shape_ok(core: str) -> bool:
    """Character-class gate: only digits, confusables and numeric punctuation."""
    if not core:
        return False
    saw_allowed = False
    for c in core:
        if c.isdigit() or c in NUMERIC_PUNCT:
            saw_allowed = True
            continue
        if c in CONFUSABLE:
            saw_allowed = True
            continue
        return False          # any other letter -> chemistry or prose
    return saw_allowed


def candidate_suspects(tokens: list[str]) -> list[int]:
    """
    Indices of tokens that pass layers 1 and 2 and become a valid number after
    substitution. Layer 3 (line context) is applied by classify_line.
    """
    out = []
    for i, raw in enumerate(tokens):
        core = strip_edges(raw)
        if not core:
            continue
        if len(core) == 1 and core not in SINGLE_CHAR_ALLOWED:
            continue
        if core.upper() in DENY:
            continue
        if CPC_CODE_RE.match(core):
            continue                      # IPC/CPC class code, not a number
        if is_identifier(core):
            continue                      # sample/run ID such as B101
        if INITIAL_RE.match(core):
            continue                      # author initial "S.;" / list marker
        if not any(c in CONFUSABLE for c in core):
            continue                      # nothing to repair
        if not token_shape_ok(core):
            continue
        if not is_clean_number(repair_token_chars(core)):
            continue
        out.append(i)
    return out


def needs_numeric_context(core: str) -> bool:
    """
    True when the token contains no unambiguous numeric evidence of its own
    (no digit and no decimal point), e.g. "OSO", "SO", "IS". Such tokens are
    only trusted inside a numeric-dominant row with numeric neighbours.
    """
    core = strip_edges(core)
    return not any(c.isdigit() for c in core) and "." not in core and "," not in core


@dataclass
class LineVerdict:
    page: int
    text: str
    kind: str                     # DATA | CITATION | PROSE
    suspects: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)


def classify_line(text: str, page: int) -> LineVerdict | None:
    """
    Classify one physical line and return the confirmed suspect tokens on it.
    Returns None when the line has no confirmed suspects.
    """
    tokens = text.split()
    if not tokens:
        return None

    cand_idx = candidate_suspects(tokens)
    if not cand_idx:
        return None

    n_clean = sum(1 for t in tokens if is_clean_number(t))
    cand_set = set(cand_idx)
    numericish = n_clean + len(cand_idx)
    # A data row is dominated by numbers. Candidate tokens count towards the
    # numeric mass because they already passed the strict shape gate; requiring
    # two *pristine* numbers would misclassify heavily-corrupted header rows
    # like "Reaction S.O 2.0 1.O O.90 O.8O OSO O.20 O.OSO", where only a single
    # value survived OCR intact.
    is_data_row = (n_clean >= 1 and numericish >= 3
                   and numericish / len(tokens) >= 0.50)

    is_citation = any(p.search(text) for p in CITATION_MARKERS)
    chemical_context = bool(FORMULA_RE.search(text)) and not is_data_row

    confirmed: list[int] = []
    for i in cand_idx:
        core = strip_edges(tokens[i])
        # Guard (b): "Table 1B", "Figure 5B", "(300B)" -- a panel label, not a
        # mis-read 8. Only outside data rows, so a real "4S" in a table body is
        # still repaired.
        if not is_data_row and PANEL_LABEL_RE.match(core):
            continue
        # Guard (d): the oxygen guard. Only tokens that would gain a 0 from an
        # "O" can be affected, so numbers such as "S4" are unaffected.
        if "O" in core and in_formula_chain(tokens, i):
            continue
        if needs_numeric_context(core):
            # Layer 3: require a numeric-dominant row AND a numeric neighbour.
            if not is_data_row:
                continue
            left_ok = i > 0 and (is_clean_number(tokens[i - 1]) or (i - 1) in cand_set)
            right_ok = (i + 1 < len(tokens)
                        and (is_clean_number(tokens[i + 1]) or (i + 1) in cand_set))
            if not (left_ok or right_ok):
                continue
        elif chemical_context:
            # Token has its own numeric evidence, but the line reads as a
            # formula rather than a data row -- stay out.
            continue
        confirmed.append(i)

    if not confirmed:
        return None

    kind = "CITATION" if is_citation else ("DATA" if is_data_row else "PROSE")
    return LineVerdict(
        page=page,
        text=text,
        kind=kind,
        suspects=[tokens[i] for i in confirmed],
        repaired=[repair_token_chars(strip_edges(tokens[i])) for i in confirmed],
    )


# --------------------------------------------------------------------------
# 3. Document-level structural evidence (native text layer vs scanned OCR)
# --------------------------------------------------------------------------

SUBSET_FONT_RE = re.compile(r"^[A-Z]{6}\+")

# Base-14 fonts that a PDF may reference without embedding. An OCR text layer
# written over a scanned image almost always uses one of these, unembedded.
NON_EMBEDDED_BASE = {
    "Courier", "Courier-Bold", "Courier-Oblique",
    "Helvetica", "Helvetica-Bold", "Times-Roman", "Arial", "ArialMT",
}


@dataclass
class DocReport:
    path: str
    name: str
    pages: int = 0
    n_chars: int = 0
    n_tables_lattice: int = 0
    n_tables_text: int = 0
    n_example_kw: int = 0
    n_suspect_tokens: int = 0
    n_suspect_lines: int = 0
    lines_data: int = 0
    lines_citation: int = 0
    lines_prose: int = 0
    suspects_in_data: int = 0
    suspects_in_citation: int = 0
    suspects_in_prose: int = 0
    corruption_per_1k: float = 0.0
    subset_font_frac: float = 0.0
    n_distinct_fonts: int = 0
    fonts: list[str] = field(default_factory=list)
    page_image_cover: float = 0.0
    verdict: str = ""
    verdict_reason: str = ""
    char_counts: dict = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)


def count_tables(page, strategy: str) -> int:
    """Table count under an explicit ruling strategy; failures count as zero."""
    settings = ({"vertical_strategy": "lines", "horizontal_strategy": "lines"}
                if strategy == "lattice"
                else {"vertical_strategy": "text", "horizontal_strategy": "text"})
    try:
        return len(page.find_tables(table_settings=settings))
    except Exception:
        return 0


def analyse(path: str, n_samples: int = 12) -> DocReport:
    rep = DocReport(path=path, name=os.path.splitext(os.path.basename(path))[0])
    font_chars: dict[str, int] = {}
    img_fracs: list[float] = []
    verdicts: list[LineVerdict] = []
    char_hits: dict[str, int] = {k: 0 for k in CONFUSION_MAP}

    with pdfplumber.open(path) as pdf:
        rep.pages = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            rep.n_chars += len(text)
            rep.n_example_kw += len(re.findall(r"(?i)\bexamples?\b", text))
            rep.n_tables_lattice += count_tables(page, "lattice")
            rep.n_tables_text += count_tables(page, "text")

            for ch in page.chars:
                fn = ch.get("fontname") or "?"
                font_chars[fn] = font_chars.get(fn, 0) + 1

            page_area = float(page.width) * float(page.height) or 1.0
            biggest = 0.0
            for im in page.images:
                a = (abs(float(im["x1"]) - float(im["x0"]))
                     * abs(float(im["y1"]) - float(im["y0"])))
                biggest = max(biggest, a / page_area)
            img_fracs.append(min(biggest, 1.0))

            for line in text.split("\n"):
                lv = classify_line(line, pno)
                if lv is None:
                    continue
                verdicts.append(lv)
                for tok in lv.suspects:
                    for c in strip_edges(tok):
                        if c in char_hits:
                            char_hits[c] += 1

    # ---- aggregate line/token statistics -------------------------------
    rep.n_suspect_lines = len(verdicts)
    rep.n_suspect_tokens = sum(len(v.suspects) for v in verdicts)
    for v in verdicts:
        if v.kind == "DATA":
            rep.lines_data += 1
            rep.suspects_in_data += len(v.suspects)
        elif v.kind == "CITATION":
            rep.lines_citation += 1
            rep.suspects_in_citation += len(v.suspects)
        else:
            rep.lines_prose += 1
            rep.suspects_in_prose += len(v.suspects)
    rep.corruption_per_1k = round(
        1000.0 * rep.n_suspect_tokens / rep.n_chars, 3) if rep.n_chars else 0.0
    rep.char_counts = {k: v for k, v in char_hits.items() if v}

    # ---- structural evidence -------------------------------------------
    total_fc = sum(font_chars.values()) or 1
    rep.n_distinct_fonts = len(font_chars)
    rep.fonts = sorted(font_chars, key=font_chars.get, reverse=True)[:6]
    rep.subset_font_frac = round(
        sum(n for f, n in font_chars.items() if SUBSET_FONT_RE.match(f)) / total_fc, 3)
    rep.page_image_cover = round(statistics.mean(img_fracs), 3) if img_fracs else 0.0

    # ---- verdict --------------------------------------------------------
    #  Criterion A (structural, decisive): no subset-embedded fonts, a very
    #  small font repertoire, and a page-filling raster image on essentially
    #  every page => the visible page IS the image and the text is an OCR
    #  overlay.
    #  Criterion B (statistical, corroborating): corruption density on
    #  numeric tokens above 0.5 per 1000 characters.
    structural = (rep.subset_font_frac < 0.05
                  and rep.n_distinct_fonts <= 3
                  and rep.page_image_cover > 0.80
                  and any(f.split("+")[-1] in NON_EMBEDDED_BASE for f in rep.fonts))
    statistical = rep.corruption_per_1k >= 0.5

    if structural and statistical:
        rep.verdict = "SCANNED_OCR"
        rep.verdict_reason = (
            f"no embedded subset fonts (subset frac {rep.subset_font_frac}), "
            f"{rep.n_distinct_fonts} base font(s) {rep.fonts}, "
            f"page-filling image on avg {rep.page_image_cover:.0%} of page area, "
            f"and {rep.corruption_per_1k}/1k-char numeric corruption")
    elif structural:
        rep.verdict = "SCANNED_OCR (clean)"
        rep.verdict_reason = (
            "structural OCR signature present but corruption density "
            f"only {rep.corruption_per_1k}/1k chars")
    elif statistical:
        rep.verdict = "NATIVE (corruption flagged)"
        rep.verdict_reason = (
            f"embedded subset fonts ({rep.subset_font_frac:.0%} of glyphs) yet "
            f"{rep.corruption_per_1k}/1k-char corruption -- inspect manually")
    else:
        rep.verdict = "NATIVE_TEXT"
        rep.verdict_reason = (
            f"{rep.subset_font_frac:.0%} of glyphs use embedded subset fonts, "
            f"{rep.n_distinct_fonts} distinct fonts, "
            f"corruption {rep.corruption_per_1k}/1k chars")

    # ---- samples, data rows first --------------------------------------
    ordered = sorted(verdicts, key=lambda v: (v.kind != "DATA", v.page))
    rep.samples = [
        {"page": v.page, "kind": v.kind, "raw": v.text.strip()[:120],
         "suspects": v.suspects, "repaired": v.repaired}
        for v in ordered[:n_samples]
    ]
    return rep


# --------------------------------------------------------------------------
# 4. Reporting
# --------------------------------------------------------------------------

MD_HEADER = (
    "| Document | Pages | Chars | Tables (lattice) | Tables (text) | "
    "\"Example\" | Suspect tokens | Suspect lines (data / cite / prose) | "
    "Corruption /1k chars | Subset fonts | Verdict |\n"
    "|---|---:|---:|---:|---:|---:|---:|:---:|---:|---:|---|\n"
)


def md_row(r: DocReport) -> str:
    return (f"| `{r.name}` | {r.pages} | {r.n_chars:,} | {r.n_tables_lattice} | "
            f"{r.n_tables_text} | {r.n_example_kw} | {r.n_suspect_tokens} | "
            f"{r.lines_data} / {r.lines_citation} / {r.lines_prose} | "
            f"{r.corruption_per_1k} | {r.subset_font_frac:.0%} | **{r.verdict}** |\n")


def print_detail(r: DocReport) -> None:
    print(f"\n=== {r.name} ===")
    print(f"  path              : {r.path}")
    print(f"  pages / chars     : {r.pages} / {r.n_chars:,}")
    print(f"  tables lattice/txt: {r.n_tables_lattice} / {r.n_tables_text}")
    print(f"  'Example' hits    : {r.n_example_kw}")
    print(f"  fonts             : {r.n_distinct_fonts} distinct, "
          f"subset-embedded {r.subset_font_frac:.1%}")
    print(f"  top fonts         : {r.fonts}")
    print(f"  page image cover  : {r.page_image_cover:.2f}")
    print(f"  suspect tokens    : {r.n_suspect_tokens} "
          f"(data {r.suspects_in_data}, citation {r.suspects_in_citation}, "
          f"prose {r.suspects_in_prose})")
    print(f"  suspect lines     : {r.n_suspect_lines} "
          f"(data {r.lines_data}, citation {r.lines_citation}, prose {r.lines_prose})")
    print(f"  per-char hits     : {r.char_counts}")
    print(f"  corruption /1k    : {r.corruption_per_1k}")
    print(f"  VERDICT           : {r.verdict}")
    print(f"    reason          : {r.verdict_reason}")
    if r.samples:
        print("  samples:")
        for s in r.samples:
            print(f"    p{s['page']:<3} [{s['kind']:<8}] {s['raw']}")
            print(f"             suspects {s['suspects']} -> {s['repaired']}")


def collect_paths(args) -> list[str]:
    paths: list[str] = list(args.pdfs)
    if args.dir:
        for fn in sorted(os.listdir(args.dir)):
            if fn.lower().endswith(".pdf"):
                paths.append(os.path.join(args.dir, fn))
    return paths


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", help="PDF file(s) to diagnose")
    ap.add_argument("--dir", help="diagnose every *.pdf in this folder")
    ap.add_argument("--json", help="write full report as JSON")
    ap.add_argument("--markdown", help="write the summary table as Markdown")
    ap.add_argument("--samples", type=int, default=12,
                    help="how many example lines to keep per document")
    ap.add_argument("--quiet", action="store_true", help="table only, no detail")
    args = ap.parse_args(list(argv) if argv is not None else None)

    paths = collect_paths(args)
    if not paths:
        ap.error("give at least one PDF path or --dir FOLDER")

    reports = []
    for p in paths:
        if not os.path.exists(p):
            print(f"!! missing: {p}", file=sys.stderr)
            continue
        rep = analyse(p, n_samples=args.samples)
        reports.append(rep)
        if not args.quiet:
            print_detail(rep)

    table = MD_HEADER + "".join(md_row(r) for r in reports)
    print("\n" + table)

    if args.markdown:
        with open(args.markdown, "w") as fh:
            fh.write("# Text-layer fidelity diagnosis\n\n" + table)
        print(f"[markdown] {args.markdown}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump([asdict(r) for r in reports], fh, indent=2)
        print(f"[json]     {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

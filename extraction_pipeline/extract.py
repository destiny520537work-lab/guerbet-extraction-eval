"""
Guerbet Catalyst Data Extraction Pipeline — V1 (zero-shot baseline)
Reads a Guerbet reaction PDF, sends text chunks to Claude, outputs structured rows.
Compare against Callum's ground truth table (MDS Activity Data Extract.xlsx).
"""

import pdfplumber
import json
import sys
import os
from pathlib import Path

# ── Schema (mirrors Callum's 14-column table) ──────────────────────────────
SCHEMA = {
    "paper": "str — paper short citation e.g. 'Cimino 2019'",
    "catalyst": "str — catalyst description e.g. '10% MgO/C'",
    "loading_g": "str — catalyst mass loaded in reactor (g), e.g. '0.3' or '0.3-1' for range",
    "temperature_K": "str — reaction temperature in Kelvin, e.g. '623' or '623-723' for range",
    "gas_mix": "str — feed gas composition e.g. '3% Ethanol/N2'",
    "flow_rate_mL_min": "str — total gas/liquid flow rate in mL/min, or null if not given",
    "LHSV_h": "str — liquid hourly space velocity in h⁻¹, or null",
    "WHSV_h": "str — weight hourly space velocity in h⁻¹, or null",
    "GHSV_h": "str — gas hourly space velocity in h⁻¹, or null",
    "pressure": "str — operating pressure with unit e.g. '101 kPa' or '1 bar', or null",
    "conversion_pct": "str — ethanol conversion %, e.g. '60' or '40-70' for range",
    "selectivity_pct": "str — selectivity to butanol (or target product) %, or null",
}

# ── Simon's parameter crib sheet (key relationships for the prompt) ─────────
CRIB_SHEET = """
KEY PARAMETER RELATIONSHIPS (from supervisor crib sheet):
- LHSV (h⁻¹) = Liquid volumetric flow rate (mL/h) / Catalyst volume (mL)
- GHSV (h⁻¹) = Gas volumetric flow rate (mL/h) / Catalyst volume (mL)
- WHSV (h⁻¹) = Mass flow rate of feed (g/h) / Catalyst mass (g)
- Yield (%) = Conversion (%) × Selectivity (%) / 100
- sccm = mL/min (standard cubic centimetres per minute)
- 1 atm = 101.325 kPa = 1.01325 bar = 0.101325 MPa
- Temperature: always convert to Kelvin (K = °C + 273.15)
- Loading: metal loading wt% means g metal / 100 g catalyst
- One row per (catalyst × operating condition) combination
"""

SYSTEM_PROMPT = f"""You are a scientific data extraction assistant specialising in heterogeneous catalysis.
Extract structured data from Guerbet reaction papers into rows matching Callum's benchmark schema.

SCHEMA (one JSON object per row, one row per catalyst × condition combination):
{json.dumps(SCHEMA, indent=2)}

{CRIB_SHEET}

RULES:
1. Extract ALL distinct (catalyst, condition) combinations reported with quantitative data.
2. Use null for fields not reported in the text.
3. Keep original units but note them (e.g. "623 K" not just "623").
4. For ranges, use "min-max" format (e.g. "623-723").
5. Selectivity = selectivity to 1-butanol (or the target Guerbet alcohol) specifically.
6. Do NOT invent numbers. Only extract what is explicitly stated.
7. Return ONLY a JSON array of objects, no prose.
"""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from PDF using pdfplumber."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"[Page {i+1}]\n{page_text}")
            # Also extract tables
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                if table:
                    text_parts.append(f"[Table on page {i+1}]")
                    for row in table:
                        if row:
                            text_parts.append(" | ".join(str(c) if c else "" for c in row))
    return "\n\n".join(text_parts)


def chunk_text(text: str, max_chars: int = 12000) -> list[str]:
    """Split text into chunks that fit in context."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    # Try to split on page boundaries
    pages = text.split("[Page ")
    current = ""
    for page in pages:
        if len(current) + len(page) < max_chars:
            current += "[Page " + page if page else ""
        else:
            if current:
                chunks.append(current)
            current = "[Page " + page
    if current:
        chunks.append(current)
    return chunks



# NOTE (package copy): a legacy July direct-API extraction path was removed
# from this copy; the experiments in the dissertation use pipeline.py, which
# imports only extract_text_from_pdf and chunk_text from this module.
# The full historical file is preserved in the project workspace.

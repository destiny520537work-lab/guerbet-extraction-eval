"""
Ablation prompt versions for the Guerbet extraction pipeline.
B0 → V1 → V2 → V3 → V4 corresponds to the ladder in the dissertation.
"""

import json

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = {
    "paper": "str — paper short citation e.g. 'Cimino 2019'",
    "catalyst": "str — catalyst formula e.g. '10% MgO/C'",
    "loading_g": "str — catalyst mass in reactor (g); use range 'min-max' if varied",
    "temperature_K": "str — reaction temperature in K; use range if varied",
    "gas_mix": "str — feed gas composition e.g. '3% Ethanol/N2'",
    "flow_rate_mL_min": "str — total flow rate in mL/min, or null",
    "LHSV_h": "str — liquid hourly space velocity h⁻¹, or null",
    "WHSV_h": "str — weight hourly space velocity h⁻¹, or null",
    "GHSV_h": "str — gas hourly space velocity h⁻¹, or null",
    "pressure": "str — operating pressure with unit e.g. '101 kPa', or null",
    "conversion_pct": "str — ethanol conversion %; use range if varied",
    "selectivity_pct": "str — selectivity to 1-butanol %; or null if not reported",
}

CRIB = """
KEY CHEMISTRY (Simon's crib sheet):
- LHSV (h⁻¹) = liquid vol. flow rate (mL/h) / catalyst volume (mL)
- GHSV (h⁻¹) = gas vol. flow rate (mL/h) / catalyst volume (mL)
- WHSV (h⁻¹) = mass flow rate of feed (g/h) / catalyst mass (g)
- Selectivity to butanol = butanol_produced / ethanol_reacted × 100 (%)
- Yield (%) = Conversion (%) × Selectivity (%) / 100
- 1 atm = 101.325 kPa; T(K) = T(°C) + 273.15; sccm = mL/min
- One row per distinct (catalyst × operating condition) combination.
"""

# ── Prompt versions ─────────────────────────────────────────────────────────────

# B0: Bare zero-shot — no schema, no crib, no examples
B0_SYSTEM = """You are a scientific data extraction assistant.
Extract catalyst performance data from the Guerbet reaction paper text provided.
Return a JSON array of objects, one per experiment. Include:
catalyst, temperature, feed gas, space velocity, pressure, conversion, selectivity.
Return ONLY valid JSON, no prose."""

def b0_user(text_chunk: str, paper_hint: str) -> str:
    return f"Paper: {paper_hint}\n\nExtract all catalyst performance data:\n\n{text_chunk}"


# V1: Schema-guided — adds the 12-column schema
V1_SYSTEM = f"""You are a scientific data extraction assistant specialising in heterogeneous catalysis.
Extract structured data from Guerbet reaction papers into rows matching this schema:

SCHEMA (one JSON object per row):
{json.dumps(SCHEMA, indent=2)}

RULES:
1. Extract ALL distinct (catalyst, condition) combinations with quantitative data.
2. Use null for fields not reported.
3. Include units in values (e.g. "101 kPa").
4. For ranges, use "min-max" format.
5. Do NOT invent numbers — only extract what is explicitly stated.
6. Return ONLY a JSON array of objects, no prose."""

def v1_user(text_chunk: str, paper_hint: str) -> str:
    return f"Paper: {paper_hint}\n\nExtract ALL catalyst performance rows:\n\n{text_chunk}"


# V2: Schema + crib sheet — adds chemistry parameter relationships
V2_SYSTEM = f"""You are a scientific data extraction assistant specialising in heterogeneous catalysis.
Extract structured data from Guerbet reaction papers into rows matching this schema:

SCHEMA (one JSON object per row):
{json.dumps(SCHEMA, indent=2)}

{CRIB}

RULES:
1. Extract ALL distinct (catalyst, condition) combinations with quantitative data.
2. Use null for fields not reported.
3. Include units in values (e.g. "101 kPa" not just "101").
4. For ranges, use "min-max" format.
5. Always convert temperatures to Kelvin.
6. Do NOT invent numbers. Only extract what is explicitly stated.
7. Return ONLY a JSON array of objects, no prose."""

def v2_user(text_chunk: str, paper_hint: str) -> str:
    return f"Paper: {paper_hint}\n\nExtract ALL catalyst performance rows (remember: convert T to K):\n\n{text_chunk}"


# V3: V2 + chunking hint — adds instruction about section focus
V3_SYSTEM = V2_SYSTEM + """

EXTRACTION STRATEGY:
- Focus on Methods/Experimental and Results sections for quantitative data.
- Tables often contain the most complete condition + result data.
- Text paragraphs may contain key absolute values (e.g. "68% ethanol conversion").
- If a value appears only in a figure caption or axis label (not as a number in text),
  still extract it if you can read it clearly from the text; mark uncertain values with '~'.
"""

def v3_user(text_chunk: str, paper_hint: str) -> str:
    return f"""Paper: {paper_hint}

Focus on the Methods/Experimental and Results sections.
Extract ALL catalyst × condition rows including those in tables.

TEXT:
{text_chunk}"""


# V4: V3 + composition normalisation hint (Contribution C3)
V4_SYSTEM = V3_SYSTEM + """

CATALYST NORMALISATION:
- Normalise catalyst names: 'MgO/AC', 'MgO/C', 'MgO/activated carbon' → use 'MgO/C'
- 'γ-Al₂O₃', 'gamma-Al2O3', 'Al2O3' all refer to alumina support → use 'Al2O3'
- Include wt% loading as prefix: '10% MgO/C', '20% MgO/C'
- Metal promoters before slash: 'Ru/MgO/Al2O3', 'Cu/Mg-Al'
"""

def v4_user(text_chunk: str, paper_hint: str) -> str:
    return f"""Paper: {paper_hint}

Extract ALL catalyst × condition rows. Apply catalyst name normalisation.

TEXT:
{text_chunk}"""


# V5: V3 + NEUTRAL composition-normalisation instruction (August 2026 control).
# Motivation: V4 hard-codes reference-table spellings ('MgO/AC' -> use 'MgO/C'),
# which leaks the curator's surface conventions into the prompt. V5 asks for
# internal consistency only, naming no target spellings, so a V4-vs-V5 gap
# measures how much of V4's gain came from convention alignment.
V5_SYSTEM = V3_SYSTEM + """

CATALYST NAMING CONSISTENCY:
- Use one consistent surface form for each distinct catalyst across all rows.
- If the same material appears under several names or abbreviations in the text,
  choose the most explicit form the paper itself uses and reuse it for every row.
- Keep the wt% loading as part of the catalyst name where reported.
- Never merge materials that differ in composition, loading or support.
"""

def v5_user(text_chunk: str, paper_hint: str) -> str:
    return f"""Paper: {paper_hint}

Extract ALL catalyst x condition rows. Keep catalyst naming internally consistent.

TEXT:
{text_chunk}"""



# ── V6/V7: post hoc controlled pair (31 Aug 2026), thesis names V4c/V5c ──
# Built by string surgery on the frozen V4/V5 prompts so the ONLY changes are:
#   (1) the schema's catalyst example loses the curator-convention form
#       '10% MgO/C' (shared by both arms, byte-identical otherwise);
#   (2) the naming section differs between arms exactly as V4 vs V5 did.
# Chunking: both arms registered section-aware (see pipeline.SECTION_CHUNK_VERSIONS).
_OLD_CAT_LINE = "str \\u2014 catalyst formula e.g. '10% MgO/C'"
_NEW_CAT_LINE = "str \\u2014 catalyst identity exactly as represented in the source document"
V6_SYSTEM = V4_SYSTEM.replace(_OLD_CAT_LINE, _NEW_CAT_LINE)
V7_SYSTEM = V5_SYSTEM.replace(_OLD_CAT_LINE, _NEW_CAT_LINE)
assert V6_SYSTEM != V4_SYSTEM and V7_SYSTEM != V5_SYSTEM, "catalyst example line not found"

def v6_user(text_chunk: str, paper_hint: str) -> str:
    return v4_user(text_chunk, paper_hint)

def v7_user(text_chunk: str, paper_hint: str) -> str:
    return v5_user(text_chunk, paper_hint)

# ── Version registry ─────────────────────────────────────────────────────────
VERSIONS = {
    "B0": (B0_SYSTEM, b0_user),
    "V1": (V1_SYSTEM, v1_user),
    "V2": (V2_SYSTEM, v2_user),
    "V3": (V3_SYSTEM, v3_user),
    "V4": (V4_SYSTEM, v4_user),
    "V5": (V5_SYSTEM, v5_user),
    "V6": (V6_SYSTEM, v6_user),
    "V7": (V7_SYSTEM, v7_user),
}

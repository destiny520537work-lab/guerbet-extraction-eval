"""Single source of truth for every number that appears in a figure.

===========================================================================
PROVENANCE AND STABILITY  --  READ BEFORE EDITING
===========================================================================

Updated 27 July 2026: work package P3 has landed
(see `评估协议修正_v2_结果对照_2026-07-27.md`). All figures below now come
from the corrected `evaluate.py` protocol (v2), not the 15 July pre-correction
run. Two readings exist for every pooled/per-paper number -- STRICT (every
surplus predicted row scored as a false positive) and REFERENCE-LIMITED
(scored only over rows the benchmark covers) -- because the benchmark is a
representative, not exhaustive, sample of each paper (section 3.3/7.3). Which
reading a figure should show is noted per block below. Neither reading is
"the" number; report both where the figure has room, and default to STRICT
as the honest headline where it does not.

Anything tagged ``STABLE`` is a corpus fact, a governance fact or a verbatim
text observation and was never affected by P3.
===========================================================================
"""

# ==========================================================================
# Figure 1 -- per-column cell-level F1
# Source: CHAPTER_Methodology_draft_v1.md section 7.4; 评估协议修正_v2 section 4.
# Status: FINAL (v2 protocol). Reading: STRICT, composition normalisation off,
# unless noted. `paper` is removed -- it is no longer a scored column (section 4).
# ==========================================================================

#: (column, F1, F1 after composition normalisation or None, annotation key)
#: F1 is None where the column has no populated reference cells and is
#: therefore never scored -- deliberately distinct from a scored 0.00.
PER_COLUMN_F1 = [
    ("GHSV_h", 1.00, 1.00, None),
    ("loading_g", 0.86, 0.86, None),
    ("gas_mix", 0.68, 0.68, None),
    ("temperature_K", 0.65, 0.65, None),
    ("pressure", 0.67, 0.67, None),
    ("selectivity_pct", 0.67, 0.67, None),
    ("conversion_pct", 0.72, 0.68, None),
    ("WHSV_h", 0.56, 0.56, None),
    ("catalyst", 0.47, 0.76, "c3"),
    ("LHSV_h", 0.18, 0.18, "convention"),
    ("flow_rate_mL_min", None, None, "unscored"),
]

#: Pooled corpus figures, v2 strict reading (surplus predicted rows scored as FP).
#: This is the honest headline -- see section 7.3 for why it differs sharply
#: from the reference-limited reading below.
POOLED_F1_STRICT = 0.66
POOLED_F1_STRICT_C3 = 0.69
POOLED_F1_TEXT_STRICT = 0.67
POOLED_F1_TEXT_STRICT_C3 = 0.70

#: Pooled corpus figures, reference-limited reading (scored only over rows the
#: benchmark covers -- isolates extraction quality from benchmark coverage).
#: Numerically close to the pre-P3 figures but NOT the same computation
#: (TP=134/FP=28 here vs TP=152/FP=31 before; see 评估协议修正_v2 section 3).
POOLED_F1_REFLIMITED = 0.83
POOLED_F1_REFLIMITED_C3 = 0.87
POOLED_F1_TEXT_REFLIMITED = 0.85
POOLED_F1_TEXT_REFLIMITED_C3 = 0.90

#: True text-recoverable RECALL (distinct from F1_text above -- the two were
#: previously conflated by a mislabelled column header; section 7.5).
R_TEXT_STRICT = 0.87
R_TEXT_STRICT_C3 = 0.92

#: Backward-compatible aliases so any not-yet-updated figure script still runs;
#: new scripts should reference the STRICT/REFLIMITED names explicitly above.
POOLED_F1 = POOLED_F1_STRICT
POOLED_F1_C3 = POOLED_F1_STRICT_C3
POOLED_F1_TEXT = POOLED_F1_TEXT_STRICT
POOLED_F1_TEXT_C3 = POOLED_F1_TEXT_STRICT_C3

# ==========================================================================
# Figure 3 -- OCR corruption, repair and residual ambiguity
# Source: US9056811 ("Method for producing alcohol by Guerbet reaction"),
#         pdfplumber text layer, verbatim; quoted in section 5.3.
# Status: STABLE (a verbatim text observation; P3 does not touch it)
# ==========================================================================

#: Token classes used for colouring.
#:   "ok"   clean in the raw text layer
#:   "bad"  corrupted by OCR digit->letter substitution
#:   "fix"  restored by deterministic character substitution alone
#:   "flag" character substitution is insufficient: the decimal separator is
#:          absent from the text layer, so the value is flagged for human
#:          review rather than silently reconstructed
OCR_TABLE_HEADER = (
    "US9056811, performance table indexed by reaction pressure (header row, atm) "
    "and temperature (first column)"
)

#: Column layout of the fixed-width block: label column then eight value columns.
OCR_COL_STARTS = [0, 10, 17, 24, 31, 38, 45, 52, 59]
OCR_NCOLS = 66

OCR_RAW = [
    [("Reaction", "ok"), ("S.O", "bad"), ("2.0", "ok"), ("1.O", "bad"),
     ("O.90", "bad"), ("O.8O", "bad"), ("OSO", "bad"), ("O.20", "bad"), ("O.OSO", "bad")],
    [("350", "ok"), ("22.9", "ok"), ("35.5", "ok"), ("41.9", "ok"),
     ("42.1", "ok"), ("42.2", "ok"), ("4S.O", "bad"), ("47.2", "ok"), ("49.8", "ok")],
    [("3OO", "bad"), ("15.4", "ok"), ("19.9", "ok"), ("2SO", "bad"),
     ("25.6", "ok"), ("26.3", "ok"), ("3O.S", "bad"), ("36.4", "ok"), ("43.3", "ok")],
]

OCR_REPAIRED = [
    [("Reaction", "ok"), ("5.0", "fix"), ("2.0", "ok"), ("1.0", "fix"),
     ("0.90", "fix"), ("0.80", "fix"), ("0.50*", "flag"), ("0.20", "fix"), ("0.050", "fix")],
    [("350", "ok"), ("22.9", "ok"), ("35.5", "ok"), ("41.9", "ok"),
     ("42.1", "ok"), ("42.2", "ok"), ("45.0", "fix"), ("47.2", "ok"), ("49.8", "ok")],
    [("300", "fix"), ("15.4", "ok"), ("19.9", "ok"), ("25.0*", "flag"),
     ("25.6", "ok"), ("26.3", "ok"), ("30.5", "fix"), ("36.4", "ok"), ("43.3", "ok")],
]

OCR_TRUTH = [
    [("Reaction", "ok"), ("5.0", "ok"), ("2.0", "ok"), ("1.0", "ok"),
     ("0.90", "ok"), ("0.80", "ok"), ("0.50", "ok"), ("0.20", "ok"), ("0.050", "ok")],
    [("350", "ok"), ("22.9", "ok"), ("35.5", "ok"), ("41.9", "ok"),
     ("42.1", "ok"), ("42.2", "ok"), ("45.0", "ok"), ("47.2", "ok"), ("49.8", "ok")],
    [("300", "ok"), ("15.4", "ok"), ("19.9", "ok"), ("25.0", "ok"),
     ("25.6", "ok"), ("26.3", "ok"), ("30.5", "ok"), ("36.4", "ok"), ("43.3", "ok")],
]

#: Strings the repair rule must leave untouched. `O` inside MgO is oxygen, not a
#: mangled zero, and this is the single largest failure mode of a naive rule.
OCR_GUARD_RAIL = ["MgO", "Al2O3", "Cu/MgAlOx", "CuNi-PMO", "US 2010/0160692 A1"]

#: Corruption census for the document, section 5.3. STABLE.
OCR_SUSPECT_LINES_TOTAL = 32
OCR_SUSPECT_LINES_DATA = 29
OCR_SUSPECT_LINES_CITATION = 3

# ==========================================================================
# Figure 4 -- text fidelity across the full ten-document corpus
# Source: text_fidelity/RESULTS.md section 2 (P2, complete). STABLE.
# All six journal articles are now measured, not just the four patents.
# ==========================================================================

DOC_FIDELITY = [
    # (document, characters, tables (text strategy), "Example" hits, suspect tokens, text layer)
    ("EP2889280A1", 153_451, 37, 31, 0, "native"),
    ("US20100160692A1", 28_539, 6, 17, 0, "scanned_clean"),
    ("US9024090", 54_737, 10, 26, 24, "scanned"),
    ("US9056811", 38_339, 10, 92, 172, "scanned"),
    ("applsci-09-01371 (Cimino)", 42_616, 10, 0, 0, "native"),
    ("hucal2024", 64_255, 16, 0, 3, "native"),  # 3 = figure-axis prose artefact, not OCR
    ("liu2022", 60_348, 13, 1, 0, "native"),
    ("malina2024", 59_029, 14, 8, 0, "native"),
    ("malina2025", 66_716, 14, 3, 0, "native"),
    ("xi2020", 68_315, 16, 0, 0, "native"),
]

#: Repair effect, section 5.4. STABLE.
REPAIR_EFFECT = [
    # (document, tokens modified, parseable before, parseable after)
    ("US9056811", 148, 484, 632),
    ("US9024090", 24, 299, 323),
    ("US20100160692A1", 0, 82, 82),
]
REPAIR_ACCURACY_DEFAULT = 0.957  # 67/70 against page-image ground truth
REPAIR_ACCURACY_CONSENSUS = 0.986  # 69/70 with --decimal-consensus

# ==========================================================================
# Figure 7 -- licence tiering and corpus flow
# Source: sections 2.1, 2.2, 3.1 and ground_truth.csv. Status: STABLE
# (a governance decision taken before any measurement; P3 cannot change it).
# ==========================================================================

PAPERS_SUPPLIED = 11
PAPERS_CAT_A = 7
PAPERS_CAT_BC = 4
PATENTS_SUPPLIED = 4

SHEET_ROWS_TOTAL = 35
SHEET_ROWS_ATTRIBUTED = 30
ROWS_EVALUABLE = 21
ROWS_WITHHELD = 9  # Guo 2021 x6, Hanspal 2017 x3 -- attributable but Category B/C
ROWS_UNATTRIBUTED = 5  # internal evidence points to Lopez-Olmos 2020

# "215" in earlier drafts did not reconcile under any natural definition
# (see 评估协议修正_v2 section 10.1). Replaced with two well-defined counts:
POPULATED_REFERENCE_CELLS = 162  # denominator for recall; excludes `paper`
CELLS_COMPARED_STRICT = 275  # v2 protocol; TP+FP+FN under the strict reading
CELLS_SCORED = POPULATED_REFERENCE_CELLS  # backward-compatible alias

CAT_A_VENUES = "MDPI (Catalysts, Appl. Sci.) · ACS Omega · Front. Chem."
CAT_BC_PAPERS = "Goulas 2016 · Guo 2021 · Hanspal 2017 · López-Olmos 2020"
CAT_BC_VENUES = "JACS · Green Chem. · J. Catal. · Ind. Eng. Chem. Res."

# ==========================================================================
# Figure 6 -- per-paper F1, both readings
# Source: 评估协议修正_v2 section 5. Status: FINAL (v2 protocol).
# strict = every surplus predicted row scored as FP; reflimited = scored only
# over reference-covered rows. The gap between them IS the finding (7.3):
# it tracks reference coverage (pred_rows - ref_rows), not extraction error.
# ==========================================================================

PER_PAPER_F1 = [
    # (paper, ref_rows, pred_rows, F1_strict, F1_strict_C3, F1_reflimited, F1_reflimited_C3, dominant failure mode)
    ("Malina 2025", 9, 9, 1.00, 1.00, 0.98, 0.98, "Range containment resolved the only miss"),
    ("Cimino 2019", 3, 3, 0.86, 0.86, 0.88, 0.88, "Performance figure-only; selectivity definition conflict"),
    ("Liu 2022", 4, 4, 0.71, 0.82, 0.74, 0.84, "Reference column confusion; catalyst alias"),
    ("Portillo Crespo 2022", 1, 2, 0.48, 0.48, 0.75, 0.75, "Reference condition mismatch"),
    ("Hucal 2024", 1, 6, 0.24, 0.29, 0.88, 1.00, "REFERENCE COVERAGE: six ratios tested, one recorded"),
    ("Xi 2020", 1, 3, 0.22, 0.30, 0.35, 0.47, "Reference conversion/selectivity apparently transposed"),
    ("Frolich 2024", 2, 7, 0.18, 0.21, 0.48, 0.57, "REFERENCE COVERAGE; all catalytic data figure-only"),
]

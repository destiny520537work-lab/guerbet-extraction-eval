"""Figure 5 -- the extraction and evaluation pipeline.

Drawn in matplotlib rather than exported from mermaid or draw.io so that it
carries the same palette, the same font and the same 300 dpi PNG + vector PDF
export as every other figure, and so that the status colouring is generated
from a single declaration rather than maintained by hand.

The status colouring is the point of the diagram, not decoration. The chapter
is explicit that some stages are implemented and measured, some implemented
only in part, and some merely specified; a flowchart that flattened that
distinction would misrepresent the state of the project.

A mermaid rendering of the same graph lives in fig05_pipeline.mmd, for the
markdown build of the chapter.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

import figstyle as S

S.apply_style()

# --- status vocabulary ----------------------------------------------------

DONE = "done"  # implemented and measured
PART = "part"  # implemented in part, or not yet validated
TODO = "todo"  # designed, not yet run

STATUS_FACE = {DONE: S.PRIMARY_XL, PART: S.AMBER_L, TODO: "white"}
STATUS_EDGE = {DONE: S.PRIMARY, PART: S.AMBER, TODO: S.MUTED}
STATUS_DASH = {DONE: "solid", PART: "solid", TODO: (0, (3, 2))}
STATUS_LABEL = {
    DONE: "implemented and measured",
    PART: "implemented in part, or not yet validated",
    TODO: "designed, not yet run",
}

# --- graph ----------------------------------------------------------------
# (key, heading, subtitle, constraint or None, status), top to bottom.
# A constraint line is rendered inside the box in the failure colour: these are
# the conditions on which the validity of the numbers depends, so they belong
# in the box rather than in a floating callout.

MAIN = [
    ("pdf", "Source documents", "6 journal papers · 4 patents (EP ×1 · US ×3)", None, DONE),
    ("parse", "Local parsing — pdfplumber", "text layer; table blocks contribute no data rows", None, DONE),
    ("fidelity", "Text-fidelity diagnosis and guarded repair", "deterministic digit restoration on damaged patents", None, DONE),
    ("chunk", "Chunking", "page-packed (B0–V2, V5) · section-aware (V3–V4c/V5c)", None, DONE),
    ("extract", "LLM extraction — B0 → V4 · pair V4c/V5c",
     "pinned claude-sonnet-4-6 · three repeats",
     "subprocess cannot see the reference (6/6 probes)", DONE),
    ("dedup", "Schema-constrained JSON records", "full-record deduplication · raw replies retained on disk", None, DONE),
    ("norm", "Normalisation", "units to K and kPa · four-case null handling at scoring", None, DONE),
    ("score", "Cell-level scoring, greedy 1-to-1 alignment", "strict · reference-restricted · comparator readings", None, DONE),
]

SIDE = [
    ("sheet", "chunk", "Expert reference spreadsheet",
     "35 rows × 14 columns, curated by hand", None, DONE),
    ("audit", "extract", "Reference audit",
     "six candidate discrepancies kept on record", None, DONE),
    ("gt", "dedup", "ground_truth.csv — journals",
     "20 scored rows across the six papers", None, DONE),
    ("pgt", "norm", "Patent reference — 416 rows",
     "curator's spreadsheets · °C→K · atm→kPa", None, DONE),
]

# --- canvas ---------------------------------------------------------------

H = 5.4
fig = plt.figure(figsize=(S.FULL_W, H))
ax = fig.add_axes([0, 0, 1, 1])
S.blank_axes(ax)

MAIN_X, MAIN_W = 0.265, 0.52
SIDE_X, SIDE_W = 0.755, 0.44
BOX_H, BOX_H_TALL = 0.090, 0.112
GAP = 0.028
TOP = 0.985

# Cumulative vertical layout: boxes are not all the same height.
geom = {}
cursor = TOP
for key, _, _, constraint, _ in MAIN:
    h = BOX_H_TALL if constraint else BOX_H
    geom[key] = (cursor - h / 2, h)  # (centre y, height)
    cursor -= h + GAP


def top_of(key):
    y, h = geom[key]
    return y + h / 2


def bottom_of(key):
    y, h = geom[key]
    return y - h / 2


def box(x, y, w, h, heading, subtitle, constraint, status):
    ax.add_patch(FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0,rounding_size=0.010",
        facecolor=STATUS_FACE[status], edgecolor=STATUS_EDGE[status],
        linewidth=0.9, linestyle=STATUS_DASH[status], zorder=2))
    if constraint:
        ax.text(x, y + 0.0195, heading, ha="center", va="center", fontsize=8.5,
                color=S.INK, fontweight="bold", zorder=3)
        ax.text(x, y + 0.0015, subtitle, ha="center", va="center", fontsize=8,
                color=S.MUTED, zorder=3)
        ax.text(x, y - 0.0195, constraint, ha="center", va="center",
                fontsize=8, color=S.ACCENT, zorder=3)
    else:
        ax.text(x, y + 0.0085, heading, ha="center", va="center", fontsize=8.5,
                color=S.INK, fontweight="bold", zorder=3)
        ax.text(x, y - 0.0100, subtitle, ha="center", va="center", fontsize=8,
                color=S.MUTED, zorder=3)


def arrow(x0, y0, x1, y1, colour=S.MUTED):
    ax.add_patch(FancyArrowPatch(
        (x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=9,
        linewidth=0.9, color=colour, zorder=1, shrinkA=0, shrinkB=0))


for key, heading, subtitle, constraint, status in MAIN:
    y, h = geom[key]
    box(MAIN_X, y, MAIN_W, h, heading, subtitle, constraint, status)

side_y = {}
for key, anchor, heading, subtitle, constraint, status in SIDE:
    y = geom[anchor][0]
    side_y[key] = y
    box(SIDE_X, y, SIDE_W, BOX_H, heading, subtitle, constraint, status)

# Main chain.
for a, b in zip(MAIN, MAIN[1:]):
    arrow(MAIN_X, bottom_of(a[0]), MAIN_X, top_of(b[0]))

# Reference chain, and its junction with scoring.
arrow(SIDE_X, side_y["sheet"] - BOX_H / 2, SIDE_X, side_y["audit"] + BOX_H / 2)
arrow(SIDE_X, side_y["audit"] - BOX_H / 2, SIDE_X, side_y["gt"] + BOX_H / 2)
score_y = geom["score"][0]
ax.add_patch(FancyArrowPatch(
    (SIDE_X, side_y["pgt"] - BOX_H / 2), (MAIN_X + MAIN_W / 2, score_y),
    arrowstyle="-|>", mutation_scale=9, linewidth=0.9, color=S.MUTED, zorder=1,
    connectionstyle="angle,angleA=-90,angleB=0,rad=8", shrinkA=0, shrinkB=0))

png, pdf = S.save(fig, "fig05_pipeline")
print(f"wrote {png}\nwrote {pdf}")

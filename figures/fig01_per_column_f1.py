"""Figure 1 -- cell-level F1 by schema column.

The argument the figure has to carry: a pooled F1 is a lie of averages. The
system is saturated on some columns and worthless on others, and the two worst
columns fail for opposite reasons -- `catalyst` because normalisation was
missing (fixable, and fixed), `LHSV_h` because the reference puts the value in
a different column, which is not an extraction failure at all.

Annotation is done by colour coding rather than by leader arrows: arrows drawn
across a descending bar series always cross bars they do not refer to.

Everything numeric comes from figdata.py.
"""

import matplotlib.pyplot as plt

import figdata as D
import figstyle as S

S.apply_style()

# --- data -----------------------------------------------------------------
# Sort descending, with never-scored columns pushed to the end.

rows = sorted(
    D.PER_COLUMN_F1,
    key=lambda r: (r[1] is None, -(r[1] if r[1] is not None else 0)),
)
names = [r[0] for r in rows]

# --- canvas ---------------------------------------------------------------

fig = plt.figure(figsize=(S.FULL_W, 4.8))
ax = fig.add_axes([0.085, 0.325, 0.895, 0.545])

Y_TOP = 1.44  # headroom above 1.0 reserved for the annotation band
X_RIGHT = len(rows) - 0.25
ax.set_ylim(0, Y_TOP)
ax.set_xlim(-0.75, X_RIGHT)

ax.set_axisbelow(True)
ax.yaxis.grid(True, color=S.RULE, linewidth=0.6)
S.strip_spines(ax, keep=("left", "bottom"))
ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
ax.set_ylabel("Cell-level F1")
ax.spines["left"].set_bounds(0, 1.0)

LABEL_BOX = dict(facecolor="white", edgecolor="none", pad=0.6)

# --- pooled reference line (drawn first, so value labels sit on top) -------

ax.axhline(D.POOLED_F1, color=S.MUTED, linestyle=(0, (5, 3)), linewidth=0.9,
           zorder=1)
ax.text(X_RIGHT - 0.05, D.POOLED_F1 - 0.03,
        f"pooled corpus\nF1 = {D.POOLED_F1:.2f}",
        ha="right", va="top", fontsize=8, color=S.MUTED, linespacing=1.35)

# --- bars -----------------------------------------------------------------

BAR_W = 0.66

for i, (name, f1, f1_c3, note) in enumerate(rows):
    if f1 is None:
        # Never scored: no populated reference cells. Drawn as an absence rather
        # than a zero-height bar, which a reader would misread as F1 = 0.
        ax.text(i, 0.035, "n/a", ha="center", va="bottom", fontsize=8,
                style="italic", color=S.GREY, zorder=4)
        continue

    if note == "convention":
        # Scored low because of a reference column-definition conflict.
        ax.bar(i, f1, width=BAR_W, color=S.ACCENT, edgecolor=S.ACCENT,
               linewidth=0.7, zorder=3)
        ax.text(i, f1 + 0.03, f"{f1:.2f}", ha="center", va="bottom", fontsize=8,
                color=S.ACCENT, fontweight="bold", zorder=4, bbox=LABEL_BOX)
        continue

    hatch = "///" if note == "artefact" else None
    ax.bar(i, f1, width=BAR_W, color=S.PRIMARY_XL if hatch else S.PRIMARY,
           edgecolor=S.PRIMARY, linewidth=0.7, hatch=hatch, zorder=3)

    # Value labels sit inside the bar: several bar tops fall within a few
    # hundredths of the pooled reference line, and outside labels collide
    # with it.
    if f1_c3 is None:
        ax.text(i, f1 - 0.03, f"{f1:.2f}", ha="center", va="top", fontsize=8,
                color=S.PRIMARY if hatch else "white",
                fontweight="bold", zorder=4,
                bbox=LABEL_BOX if hatch else None)

    if f1_c3 is not None:
        # Upper segment: the gain delivered by composition normalisation (C3).
        ax.bar(i, f1_c3 - f1, bottom=f1, width=BAR_W, color=S.PRIMARY_L,
               edgecolor=S.PRIMARY, linewidth=0.7, zorder=3)
        ax.annotate("", xy=(i, f1_c3 - 0.04), xytext=(i, f1 + 0.05),
                    arrowprops=dict(arrowstyle="-|>", color=S.PRIMARY, lw=1.0,
                                    mutation_scale=9), zorder=4)
        ax.text(i, f1 - 0.045, f"{f1:.2f}", ha="center", va="top", fontsize=8,
                color="white", fontweight="bold", zorder=4)
        ax.text(i, f1_c3 + 0.025, f"{f1_c3:.2f}", ha="center", va="bottom",
                fontsize=8, color=S.PRIMARY, fontweight="bold", zorder=4)

# --- annotation band ------------------------------------------------------
# The bars are sorted descending, so the strip above y = 1.05 is empty. Both
# callouts are right-aligned there and tied to their bars by colour alone.

_cat = next(r for r in rows if r[0] == "catalyst")
_lhsv = next(r for r in rows if r[0] == "LHSV_h")
ax.text(X_RIGHT - 0.05, 1.375,
        f"catalyst  {_cat[1]:.2f} \u2192 {_cat[2]:.2f}  once the catalyst comparator is applied",
        ha="right", va="center", fontsize=8, color=S.PRIMARY)

ax.text(X_RIGHT - 0.05, 1.215,
        f"LHSV_h  {_lhsv[1]:.2f}  \u2014 the reference records this paper's LHSV in the WHSV\n"
        "column: a reference convention conflict, not an extraction failure",
        ha="right", va="center", fontsize=8, color=S.ACCENT, linespacing=1.4)

# --- axis labels ----------------------------------------------------------

ax.set_xticks(list(range(len(rows))))
ax.set_xticklabels(names, rotation=38, ha="right", rotation_mode="anchor",
                   fontfamily="monospace", fontsize=8)
for tick, (name, f1, _, note) in zip(ax.get_xticklabels(), rows):
    if note == "convention":
        tick.set_color(S.ACCENT)
    elif f1 is None:
        tick.set_color(S.GREY)

# --- footnote (kept under ~82 characters per line to fit the text width) ---

fig.text(
    0.085, 0.155,
    "Careful-read audit: seven papers, 21 reference rows, 162 populated scored cells.\n"
    "flow_rate_mL_min has no populated reference cells and is never scored.",
    ha="left", va="top", fontsize=8, color=S.MUTED, linespacing=1.5,
)

png, pdf = S.save(fig, "fig01_per_column_f1")
print(f"wrote {png}\nwrote {pdf}")

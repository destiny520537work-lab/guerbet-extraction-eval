"""Figure 7 -- licence tiering and the flow of the corpus.

The figure that turns "only seven papers were evaluated" from an apparent
weakness into what it actually is: a compliance decision taken before any
measurement, under a Data Management Plan that tiers source material by licence
rather than by extent. Ribbon width is proportional to document count, so the
four withheld papers read as a third of the supplied corpus rather than as a
footnote.

Sankey ribbons are drawn by hand as cubic Bezier bands rather than with
matplotlib.sankey, whose flow-diagram idiom (arrows, angled trunks) is built
for energy balances and reads poorly for a categorical routing decision.
"""

import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import FancyBboxPatch, PathPatch

import figdata as D
import figstyle as S

S.apply_style()

# --- canvas ---------------------------------------------------------------

H = 4.9
fig = plt.figure(figsize=(S.FULL_W, H))
ax = fig.add_axes([0, 0, 1, 1])
S.blank_axes(ax)

# Column widths are set by the longest label each has to hold: the first column
# carries short phrases, the second the four withheld paper names.
COLS = [(0.030, 0.190), (0.315, 0.320), (0.685, 0.295)]  # (left x, width)

UNIT = 0.038  # vertical extent of one document
TOP = 0.945

LH_PT = 11.5  # line pitch inside a node box
LH = LH_PT / 72 / H


def col(i):
    x0, w = COLS[i]
    return x0, x0 + w


def band(x0, y0_hi, y0_lo, x1, y1_hi, y1_lo, colour, alpha=0.42):
    """A cubic Bezier ribbon between two vertical edges."""
    xm = (x0 + x1) / 2
    verts = [
        (x0, y0_hi), (xm, y0_hi), (xm, y1_hi), (x1, y1_hi),
        (x1, y1_lo), (xm, y1_lo), (xm, y0_lo), (x0, y0_lo), (x0, y0_hi),
    ]
    codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4,
             Path.LINETO, Path.CURVE4, Path.CURVE4, Path.CURVE4,
             Path.CLOSEPOLY]
    ax.add_patch(PathPatch(Path(verts, codes), facecolor=colour, alpha=alpha,
                           edgecolor="none", zorder=1))


def node(i, y_hi, y_lo, heading, lines, edge, face, dashed=False):
    """Box with a bold heading over n centred detail lines.

    Text is laid out on an explicit line grid: matplotlib gives no way to mix
    weights inside one text object, so heading and detail are separate calls
    and their positions have to agree by construction.
    """
    x0, x1 = col(i)
    ax.add_patch(FancyBboxPatch(
        (x0, y_lo), x1 - x0, y_hi - y_lo,
        boxstyle="round,pad=0,rounding_size=0.010",
        facecolor=face, edgecolor=edge, linewidth=1.0,
        linestyle=(0, (3, 2)) if dashed else "solid", zorder=3))
    xc, yc = (x0 + x1) / 2, (y_hi + y_lo) / 2
    n_total = 1 + len(lines)
    ax.text(xc, yc + (n_total - 1) / 2 * LH, heading, ha="center", va="center",
            fontsize=8.5, fontweight="bold", color=S.INK, zorder=4)
    ax.text(xc, yc - LH / 2, "\n".join(lines), ha="center", va="center",
            fontsize=8, color=S.MUTED, linespacing=LH_PT / 8, zorder=4)


# --- journal corpus -------------------------------------------------------

src_hi, src_lo = TOP, TOP - D.PAPERS_SUPPLIED * UNIT
a_hi, a_lo = TOP, TOP - D.PAPERS_CAT_A * UNIT
bc_hi = a_lo - 0.042
bc_lo = bc_hi - D.PAPERS_CAT_BC * UNIT

band(col(0)[1], src_hi, a_lo, col(1)[0], a_hi, a_lo, S.PRIMARY)
band(col(0)[1], a_lo, src_lo, col(1)[0], bc_hi, bc_lo, S.ACCENT)
band(col(1)[1], a_hi, a_lo, col(2)[0], a_hi, a_lo, S.PRIMARY)
band(col(1)[1], bc_hi, bc_lo, col(2)[0], bc_hi, bc_lo, S.ACCENT)

node(0, src_hi, src_lo, f"{D.PAPERS_SUPPLIED} papers",
     ["identified by the", "chemistry-side", "collaborator, in", "two separate lists"],
     S.INK, "#F4F6F8")

node(1, a_hi, a_lo, "Category A — CC-BY",
     [f"{D.PAPERS_CAT_A} papers", "MDPI · ACS Omega", "Front. Chem."],
     S.PRIMARY, S.PRIMARY_XL)
node(1, bc_hi, bc_lo, "Category B / C",
     [f"publisher licence · {D.PAPERS_CAT_BC} papers",
      "Goulas 2016 · Guo 2021",
      "Hanspal 2017 · López-Olmos 2020"],
     S.ACCENT, S.ACCENT_L)

node(2, a_hi, a_lo, "External model permitted",
     ["automated corpus: 6 papers,", "20 reference rows · audit:",
      "21 rows, 162 scored cells"], S.PRIMARY, S.PRIMARY_XL)
node(2, bc_hi, bc_lo, "Local analysis only",
     [f"{D.ROWS_WITHHELD} attributable", "reference rows withheld"],
     S.ACCENT, S.ACCENT_L)

# --- patent sub-corpus ----------------------------------------------------
# Set below a rule and drawn dashed: supplied later, and not yet triaged.

pat_hi = bc_lo - 0.115
pat_lo = pat_hi - D.PATENTS_SUPPLIED * UNIT

ax.plot([0.030, 0.980], [pat_hi + 0.070, pat_hi + 0.070], color=S.RULE,
        linewidth=0.7, zorder=0)
ax.text(0.030, pat_hi + 0.030,
        "Patent sub-corpus, supplied 24 July — extracted in August under the frozen configuration",
        ha="left", va="baseline", fontsize=8, color=S.MUTED, style="italic")

band(col(0)[1], pat_hi, pat_lo, col(1)[0], pat_hi, pat_lo, S.PRIMARY, alpha=0.28)
band(col(1)[1], pat_hi, pat_lo, col(2)[0], pat_hi, pat_lo, S.PRIMARY, alpha=0.28)

node(0, pat_hi, pat_lo, f"{D.PATENTS_SUPPLIED} patents",
     ["EP ×1 · US ×3"], S.PRIMARY, "#F4F6F8")
node(1, pat_hi, pat_lo, "Processed locally, as above",
     ["text-fidelity repair", "V4 · three repeats"], S.PRIMARY, S.PRIMARY_XL)
node(2, pat_hi, pat_lo, "Scored: 416-row reference",
     ["EP2889280A1 excluded", "from cell scoring"], S.PRIMARY, S.PRIMARY_XL)

# --- footnote (kept under ~95 characters per line) ------------------------

ax.text(0.030, pat_lo - 0.052,
        f"Ribbon width is proportional to document count. A further {D.ROWS_UNATTRIBUTED} reference rows carry no\n"
        "paper attribution and are dropped by the loader; on internal evidence they are López-Olmos\n"
        "2020. The tiering is by licence, not by extent: there is no principle under which 200\n"
        "lines of a paper may be sent to an external service and 2,000 lines may not.",
        ha="left", va="top", fontsize=8, color=S.MUTED, linespacing=1.5)

png, pdf = S.save(fig, "fig07_licence_flow")
print(f"wrote {png}\nwrote {pdf}")

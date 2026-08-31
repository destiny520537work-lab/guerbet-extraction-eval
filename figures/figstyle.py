"""Shared matplotlib style and helpers for the dissertation figures.

Design constraints this module encodes:

* **Font size.** The department requires a minimum of 8 pt in the submitted
  document. Figures are therefore authored at their *final print size*
  (``FULL_W`` = 6.3 in, the text width of A4 with 25 mm margins) and must be
  placed at 100 % scale. Nothing here drops below 8 pt.
* **Palette.** A restrained, print-safe palette. No seaborn defaults: the
  report is greyscale-photocopy-plausible and the colours carry meaning
  (blue = measured, amber = needs review, rust = failure, green = recovered)
  rather than merely distinguishing series.
* **Two outputs per figure.** 300 dpi PNG for drafting and Word, and a vector
  PDF with embedded TrueType fonts for LaTeX.

Figures are saved *without* ``bbox_inches='tight'`` so that the physical size on
disk equals the declared ``figsize``. That is what makes the point-size
guarantee hold once the figure is placed in the document.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
PNG_DIR = HERE / "png"
PDF_DIR = HERE / "pdf"

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

FULL_W = 6.3  # in — A4 text width with 25 mm margins
HALF_W = 3.05  # in — two-up placement

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

INK = "#1F2933"  # body text, axis labels
MUTED = "#5C6B7A"  # secondary text, footnotes
RULE = "#C6CDD4"  # grid lines, thin separators

PRIMARY = "#35618F"  # measured result
PRIMARY_L = "#A9C3DC"  # gain / secondary segment of the same quantity
PRIMARY_XL = "#DCE6F0"  # box fill

ACCENT = "#A8442A"  # corruption, failure, zero score
ACCENT_L = "#F2DED8"

AMBER = "#B07D2B"  # partial, flagged, needs human adjudication
AMBER_L = "#FAF0DC"

GREEN = "#3F6B4A"  # deterministically recovered / verified
GREEN_L = "#DFEADF"

GREY = "#8D97A1"  # not applicable / not measured
GREY_L = "#E7EAED"

# --------------------------------------------------------------------------
# Monospace family
# --------------------------------------------------------------------------
# Figure 3 turns on the reader being able to tell `O` from `0` at a glance.
# DejaVu Sans Mono, matplotlib's bundled default, draws an unslashed zero that
# is near-indistinguishable from a capital O at 9-10 pt, which would destroy
# the figure's entire point. Menlo and Monaco both slash the zero; prefer them
# and fall back only if neither is installed.
MONO_FAMILY = ["Menlo", "Monaco", "Andale Mono", "DejaVu Sans Mono"]


def mono_available() -> bool:
    """True if a slashed-zero monospace font is installed."""
    from matplotlib import font_manager as fm

    installed = {f.name for f in fm.fontManager.ttflist}
    return bool({"Menlo", "Monaco", "Andale Mono"} & installed)


def apply_style() -> None:
    """Install the shared rcParams. Call once at the top of each figure script."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans"],
            "font.monospace": MONO_FAMILY,
            "font.size": 9.0,
            "axes.labelsize": 9.0,
            "axes.titlesize": 9.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.0,
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "axes.linewidth": 0.7,
            "xtick.color": INK,
            "ytick.color": INK,
            "xtick.major.width": 0.7,
            "ytick.major.width": 0.7,
            "text.color": INK,
            "axes.grid": False,
            "grid.color": RULE,
            "grid.linewidth": 0.6,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.unicode_minus": False,
            # Embed TrueType rather than Type 3 so the PDF text stays selectable
            # and editable, which some publishers and the university require.
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def save(fig, stem: str) -> tuple[Path, Path]:
    """Write ``stem`` as a 300 dpi PNG and a vector PDF; return both paths."""
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    png = PNG_DIR / f"{stem}.png"
    pdf = PDF_DIR / f"{stem}.pdf"
    fig.savefig(png, dpi=300)
    fig.savefig(pdf)
    plt.close(fig)
    return png, pdf


def strip_spines(ax, keep=("left", "bottom")) -> None:
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(side in keep)


def blank_axes(ax) -> None:
    """Turn an axes into a bare drawing canvas with a 0-1 coordinate system."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

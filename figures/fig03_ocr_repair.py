"""Figure 3 -- OCR corruption of the patent text layer, and its repair.

Four stacked strips over a shared fixed-width character grid: the raw
pdfplumber text layer, the output of the deterministic repair rule, the values
as printed on the page image, and the guard rail of strings the rule must never
touch.

Every character is drawn individually at a one-column pitch. That is what makes
the figure work: the *characters* that are letters standing in for digits are
coloured, not merely the tokens containing them. Relying on the reader to spot
a capital O against a zero by glyph shape alone fails in most monospace faces
(and fails completely in matplotlib's bundled DejaVu Sans Mono, whose zero is
unslashed). Colouring `O.20` as a rust O beside an ink 0 puts the two side by
side and settles it.

The figure makes three claims at once:

  * the corruption lands on experimental data, not on citation strings;
  * most of it is recoverable by a context-restricted character substitution;
  * a residue is *not* recoverable that way, because the decimal separator is
    absent from the text layer altogether. Those cells are flagged, not
    guessed -- guessing them would be exactly the hallucination the repair
    exists to prevent.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import figdata as D
import figstyle as S

S.apply_style()

# Letters the OCR substitutes for digits. Membership in this set is what marks
# a character as corrupt inside an already-flagged numeric token; it is never
# applied to running text.
OCR_LETTERS = set("OSlIBGZ")

BG = {"ok": None, "bad": S.ACCENT_L, "fix": S.GREEN_L, "flag": S.AMBER_L}

MONO_PT = 10.0  # advance is 0.6 em = 6.0 pt, just inside the column pitch

# --- layout, computed in inches then converted ----------------------------

H = 4.9
LEFT, WIDTH = 0.045, 0.925
PANEL_H_IN = 0.68
GUARD_H_IN = 0.34
STRIDE_IN = PANEL_H_IN + 0.26


def fy(inches_from_top: float) -> float:
    """Figure-fraction y from a distance measured down from the top edge."""
    return (H - inches_from_top) / H


# --- character-level colouring --------------------------------------------

def changed_positions(raw: str, repaired: str) -> set:
    """Indices the repair rewrote. Empty when the two differ in length, which
    is the signature of a lost decimal separator rather than a substitution."""
    if len(raw) != len(repaired):
        return set()
    return {i for i, (a, b) in enumerate(zip(raw, repaired)) if a != b}


def colours_raw(token, kind):
    if kind != "bad":
        return [(S.INK, "normal")] * len(token)
    return [(S.ACCENT, "bold") if ch in OCR_LETTERS else (S.INK, "normal")
            for ch in token]


def colours_repaired(token, kind, changed):
    if kind == "flag":
        return [(S.AMBER, "bold")] * len(token)
    if kind != "fix":
        return [(S.INK, "normal")] * len(token)
    return [(S.GREEN, "bold") if i in changed else (S.INK, "normal")
            for i in range(len(token))]


def colours_plain(token, _kind):
    return [(S.INK, "normal")] * len(token)


# --- drawing --------------------------------------------------------------

def new_strip(top_in, height_in, n_rows):
    ax = fig.add_axes([LEFT, fy(top_in + height_in), WIDTH, height_in / H])
    ax.set_xlim(0, D.OCR_NCOLS)
    ax.set_ylim(-0.5, n_rows - 0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.add_patch(Rectangle((0, -0.5), D.OCR_NCOLS, n_rows, facecolor="#FAFBFC",
                           edgecolor=S.RULE, linewidth=0.6, zorder=0))
    return ax


def draw_token(ax, col, y, token, kind, per_char):
    if BG.get(kind):
        ax.add_patch(Rectangle((col - 0.15, y - 0.40), len(token) + 0.30, 0.80,
                               facecolor=BG[kind], edgecolor="none", zorder=1))
    for j, ch in enumerate(token):
        colour, weight = per_char[j]
        ax.text(col + j + 0.5, y, ch, ha="center", va="center",
                fontfamily="monospace", fontsize=MONO_PT, color=colour,
                fontweight=weight, zorder=2)


fig = plt.figure(figsize=(S.FULL_W, H))

STRIPS = [
    ("A", "Raw text layer, as returned by pdfplumber", D.OCR_RAW, "raw"),
    ("B", "After deterministic repair:  O→0   S→5   l→1   (no model involved)",
     D.OCR_REPAIRED, "repaired"),
    ("C", "Value as printed on the page image", D.OCR_TRUTH, "plain"),
]

for k, (letter, title, lines, mode) in enumerate(STRIPS):
    top_in = 0.30 + k * STRIDE_IN
    fig.text(LEFT, fy(top_in - 0.06), f"{letter}   {title}",
             ha="left", va="baseline", fontsize=8.5, color=S.MUTED)
    ax = new_strip(top_in, PANEL_H_IN, len(lines))
    for r, line in enumerate(lines):
        y = len(lines) - 1 - r
        raw_line = D.OCR_RAW[r]
        for c, ((token, kind), col) in enumerate(zip(line, D.OCR_COL_STARTS)):
            if mode == "raw":
                per_char = colours_raw(token, kind)
            elif mode == "repaired":
                per_char = colours_repaired(
                    token, kind, changed_positions(raw_line[c][0], token))
            else:
                per_char = colours_plain(token, kind)
            draw_token(ax, col, y, token, kind if mode != "plain" else "ok",
                       per_char)

# --- guard-rail strip -----------------------------------------------------

guard_top_in = 0.30 + 3 * STRIDE_IN
fig.text(LEFT, fy(guard_top_in - 0.06),
         "D   Never rewritten — an O inside a formula is oxygen (blue); citation strings are excluded too",
         ha="left", va="baseline", fontsize=8.5, color=S.MUTED)

gax = new_strip(guard_top_in, GUARD_H_IN, 1)
col = 1
for item in D.OCR_GUARD_RAIL:
    is_formula = not item.startswith("US")
    per_char = [(S.PRIMARY, "bold") if (is_formula and ch == "O")
                else (S.INK, "normal") for ch in item]
    draw_token(gax, col, 0, item, "ok", per_char)
    col += len(item) + 2

# --- key ------------------------------------------------------------------
# 8 pt DejaVu Sans advances about 0.52 em, which on a 6.3 in figure is roughly
# 0.0092 of the figure width per character.

CHAR_FRAC = 0.0092
key_y = fy(guard_top_in + GUARD_H_IN + 0.24)
x = LEFT
for fg, bg, label in [
    (S.ACCENT, S.ACCENT_L, "letter substituted for a digit"),
    (S.GREEN, S.GREEN_L, "restored by rule"),
    (S.AMBER, S.AMBER_L, "flagged, not reconstructed"),
]:
    fig.patches.append(
        Rectangle((x, key_y - 0.010), 0.015, 0.021, transform=fig.transFigure,
                  facecolor=bg, edgecolor=fg, linewidth=0.8)
    )
    fig.text(x + 0.021, key_y, label, ha="left", va="center", fontsize=8,
             color=S.MUTED)
    x += 0.021 + CHAR_FRAC * len(label) + 0.028

# --- caption block --------------------------------------------------------

fig.text(
    LEFT, fy(guard_top_in + GUARD_H_IN + 0.44),
    "US9056811, a performance table indexed by reaction pressure (header row, atm) and by\n"
    "temperature (first column). Three of the 29 corrupted data-table lines in this document;\n"
    "32 suspect lines in all, of which 29 are data rows and 3 are citation strings.\n"
    "* Character substitution alone yields 050 and 250 — the decimal separator is absent from\n"
    "the text layer, so these two cells carry an uncertainty flag rather than a guessed value.",
    ha="left", va="top", fontsize=8, color=S.MUTED, linespacing=1.5,
)

png, pdf = S.save(fig, "fig03_ocr_repair")
print(f"wrote {png}\nwrote {pdf}")

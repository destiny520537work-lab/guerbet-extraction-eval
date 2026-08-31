"""
Composition-aware catalyst normalisation (Contribution C3).

Papers and the ground truth name the same catalyst in incompatible ways:

    Liu 2022    paper: Cu/MgAlOx          GT: Cu/Mg-Al
    Malina 2024 paper: Cu0.23Mg2Al1.06    GT: Cu-Mg-Al
    Herrera 2024 paper: c-Mg3Al1O         GT: Mg-Al
    Xi 2020     paper: CuNi-PMO           GT: CuNi-Mg-Al
    Cimino 2019 paper: 20% MgO/AC         GT: 20% MgO/C

String equality scores these as misses even though the chemistry is identical.
This module parses a catalyst name into its composition — loading, promoter
metals, base oxide system, support — and re-renders it in one canonical form.

Note the GT is itself inconsistent: it writes the promoter separator as "/" in
Liu 2022 (Cu/Mg-Al) but "-" in Malina 2024 (Cu-Mg-Al). Both canonicalise here.
"""

import re

# Base oxide systems: the support backbone, written as an element pair.
BASE_SYSTEMS = [("mg", "al"), ("zn", "al"), ("li", "al")]

# Metals that decorate a base system rather than constitute it.
PROMOTERS = {"cu", "ni", "co", "mn", "fe", "ru", "pd", "pt", "ag"}

# Carbon supports: distinct from a base system — the active phase sits on them.
CARBON_SUPPORTS = {"c", "ac", "activatedcarbon", "activated carbon"}

# Family aliases that expand to a base system. PMO ("porous metal oxide") is
# hydrotalcite-derived Mg-Al in this corpus (Xi 2020); likewise LDH/hydrotalcite.
FAMILY_ALIASES = {
    "pmo": ("mg", "al"),
    "ht": ("mg", "al"),
    "ldh": ("mg", "al"),
    "hydrotalcite": ("mg", "al"),
}

# Oxide markers carry no compositional information for our purposes. "Ox" is a
# non-stoichiometric oxide suffix (MgAlOx) that the element regex reads as one
# token, so it must be dropped alongside a plain O.
OXIDE_TOKENS = {"o", "ox"}

# A stoichiometric suffix must contain at least one digit.  The previous
# expression also accepted a bare full stop, so prose such as ``S.A. = 252``
# was parsed as element S with the invalid numeric suffix ``.`` and crashed the
# evaluator.  Model output is untrusted input: normalisation must be total (it
# may decline to understand a name, but it must never abort a corpus score).
ELEMENT_RE = re.compile(r"([A-Z][a-z]?)(?:(\d+(?:\.\d*)?|\.\d+))?")
LOADING_RE = re.compile(r"^\s*(\d+\.?\d*)\s*(?:wt)?\s*%\s*", re.IGNORECASE)


class Catalyst:
    """Parsed catalyst composition."""

    def __init__(self, loading=None, promoters=None, base=None, support=None,
                 base_ratio=None, raw=""):
        self.loading = loading            # wt% as float, or None
        self.promoters = promoters or []  # ordered element symbols, lowercase
        self.base = base                  # ("mg","al") | ("zn","al") | None
        self.support = support            # "c" for carbon supports, or None
        self.base_ratio = base_ratio      # (2.0, 1.0) if stoichiometry given
        self.raw = raw

    def canonical(self, with_ratio=False):
        """Render one canonical string. Ratio is opt-in: the GT never states it."""
        parts = []
        if self.loading is not None:
            pct = int(self.loading) if float(self.loading).is_integer() else self.loading
            parts.append(f"{pct}%")

        stem = ""
        if self.promoters:
            stem += "".join(p.capitalize() for p in self.promoters)

        if self.base:
            base_str = "-".join(e.capitalize() for e in self.base)
            if with_ratio and self.base_ratio:
                a, b = self.base_ratio
                base_str += f"({_fmt(a)}:{_fmt(b)})"
            stem = f"{stem}-{base_str}" if stem else base_str
        elif self.support:
            # Active phase on a carbon support: render the oxide, e.g. MgO/C
            metal = "".join(p.capitalize() for p in self.promoters)
            stem = f"{metal}O/C" if metal else "MgO/C"

        parts.append(stem)
        return " ".join(parts) if parts else self.raw

    def __repr__(self):
        return f"<Catalyst {self.canonical(with_ratio=True)!r} from {self.raw!r}>"


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def _parse_elements(s):
    """Extract [(element, stoichiometry|None)] from a formula fragment."""
    out = []
    for sym, num in ELEMENT_RE.findall(s):
        e = sym.lower()
        try:
            value = float(num) if num else None
        except (TypeError, ValueError):
            value = None
        out.append((e, value))
    return out


def parse_catalyst(name):
    """Parse a catalyst name into its composition.

    Handles: wt% prefixes, oxide/support slashes (MgO/AC), promoter/base
    slashes (Cu/MgAlOx), hyphenated forms (Cu-Mg-Al), inline stoichiometry
    (Cu0.23Mg2Al1.06), family aliases (CuNi-PMO), and commercial prefixes
    (c-Mg3Al1O).
    """
    if name is None:
        return None
    raw = str(name).strip()
    if not raw:
        return None

    s = raw

    # Remove common descriptive wrappers while retaining the compositional
    # core.  B0 in particular emits prose labels such as
    # ``3:1 Mg-Al mixed oxide (S.A. = 252 m2/g)``.  Parenthetical surface-area,
    # treatment and citation text is not part of catalyst identity and can
    # contain capital-letter/full-stop patterns that resemble formulae.
    s = re.sub(r"^\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?\s+", "", s)
    s = re.sub(r"\s*[\(\[].*$", "", s).strip()
    s = re.sub(r"\s+(?:mixed\s+oxide|catalyst|pellets?|powder|bulk)\s*$", "", s,
               flags=re.IGNORECASE)

    # 1. wt% loading prefix
    loading = None
    m = LOADING_RE.match(s)
    if m:
        loading = float(m.group(1))
        s = s[m.end():]

    # 2. Commercial / phase prefixes carry no composition ("c-Mg3Al1O", "γ-Al2O3")
    s = re.sub(r"^\s*(?:c|comm|commercial|[γgα])\s*[-–]\s*", "", s, flags=re.IGNORECASE)
    s = s.strip()

    # 3. Split promoter from base on "/", and detect carbon supports
    promoters, base_str, support = [], s, None
    if "/" in s:
        left, right = (p.strip() for p in s.split("/", 1))
        if _squash(right) in CARBON_SUPPORTS:
            # Active phase on carbon, e.g. "MgO/AC" -> promoters=[Mg], support=c
            support = "c"
            base_str = left
        else:
            left_elems = [e for e, _ in _parse_elements(left)]
            if left_elems and all(e in PROMOTERS for e in left_elems):
                promoters = left_elems
                base_str = right
            else:
                base_str = right

    # 4. Family alias anywhere in the remaining stem (CuNi-PMO)
    base_pair, base_ratio = None, None
    for alias, pair in FAMILY_ALIASES.items():
        if re.search(rf"\b{alias}\b", base_str, flags=re.IGNORECASE):
            base_pair = pair
            lead = re.split(rf"\b{alias}\b", base_str, flags=re.IGNORECASE)[0]
            for e, _ in _parse_elements(lead):
                if e in PROMOTERS and e not in promoters:
                    promoters.append(e)
            break

    # 5. Otherwise read the element sequence and split promoters from the base
    if base_pair is None:
        elems = _parse_elements(base_str.replace("-", "").replace(" ", ""))
        elems = [(e, n) for e, n in elems if e not in OXIDE_TOKENS]

        for e, _ in elems:
            if e in PROMOTERS and e not in promoters:
                promoters.append(e)

        skeleton = [(e, n) for e, n in elems if e not in PROMOTERS]
        sk_elems = [e for e, _ in skeleton]
        for pair in BASE_SYSTEMS:
            if list(pair) == sk_elems:
                base_pair = pair
                nums = [n for _, n in skeleton]
                if all(n is not None for n in nums):
                    base_ratio = tuple(nums)
                break
        else:
            if support and skeleton:
                # "MgO/C": the skeleton IS the active phase, not a base system
                promoters = [e for e, _ in skeleton]
            elif skeleton:
                base_pair = tuple(sk_elems) or None

    return Catalyst(loading=loading, promoters=promoters, base=base_pair,
                    support=support, base_ratio=base_ratio, raw=raw)


def _squash(s):
    return re.sub(r"[\s\-_]", "", str(s)).lower()


def canonical_catalyst(name, with_ratio=False):
    """Canonical string for a catalyst name; falls back to the raw string."""
    cat = parse_catalyst(name)
    return cat.canonical(with_ratio=with_ratio) if cat else None


def catalyst_match(a, b):
    """True if two catalyst names denote the same material.

    Stoichiometric ratio is a soft constraint: an unspecified ratio on either
    side is a wildcard, because the GT records "Mg-Al" for every Mg:Al ratio a
    paper tests. Where both sides state a ratio, they must agree.
    """
    ca, cb = parse_catalyst(a), parse_catalyst(b)
    if ca is None or cb is None:
        return ca is None and cb is None

    if ca.canonical() != cb.canonical():
        return False
    if ca.base_ratio and cb.base_ratio:
        ra = ca.base_ratio[0] / ca.base_ratio[1]
        rb = cb.base_ratio[0] / cb.base_ratio[1]
        if abs(ra - rb) / max(rb, 1e-9) > 0.05:
            return False
    return True


# ── Self-test: every (paper name, GT name) pair in the corpus ────────────────
CORPUS_CASES = [
    # (paper form, GT form, should_match)
    ("MgAlOx",            "Mg-Al",         True),   # Liu 2022
    ("ZnAlOx",            "Zn-Al",         True),   # Liu 2022
    ("Cu/MgAlOx",         "Cu/Mg-Al",      True),   # Liu 2022
    ("Cu/ZnAlOx",         "Cu/Zn-Al",      True),   # Liu 2022
    ("Cu0.23Mg2Al1.06",   "Cu-Mg-Al",      True),   # Malina 2024
    ("Co0.90Mg2Al1.16",   "Co-Mg-Al",      True),   # Malina 2024
    ("Mg1Al1O",           "Mg-Al",         True),   # Herrera 2024
    ("c-Mg3Al1O",         "Mg-Al",         True),   # Herrera 2024 (commercial)
    ("CuNi-PMO",          "CuNi-Mg-Al",    True),   # Xi 2020 (PMO alias)
    ("Cu-PMO",            "Cu-Mg-Al",      True),   # Xi 2020
    ("20% MgO/AC",        "20% MgO/C",     True),   # Cimino 2019 (AC alias)
    ("10% MgO/C",         "10% MgO/C",     True),   # Cimino 2019
    ("0.5% Cu/Li-Al",     "0.5% Cu/Li-Al", True),   # Malina 2025
    ("5% Cu/Li-Al",       "5% Cu/Li-Al",   True),   # Malina 2025
    ("Mg-Al",             "Mg-Al",         True),   # Portillo Crespo 2022
    # GT's own separator inconsistency must not matter
    ("Cu/Mg-Al",          "Cu-Mg-Al",      True),
    # True negatives: these are genuinely different materials
    ("Cu/Mg-Al",          "Cu/Zn-Al",      False),
    ("Mg-Al",             "Zn-Al",         False),
    ("Cu-Mg-Al",          "Co-Mg-Al",      False),
    ("CuNi-PMO",          "Cu-Mg-Al",      False),
    ("10% MgO/C",         "20% MgO/C",     False),
    ("Mg1Al1O",           "Mg3Al1O",       False),  # ratio stated both sides
    # Free-text annotations emitted by an LLM must not crash parsing.
    ("3:1 Mg-Al mixed oxide (S.A. = 252 m2/g)", "Mg-Al", True),
    ("20 wt% MgO/γ-Al2O3 pellets (S.A. = 115 m2/g)", "20% MgO/C", False),
]


def _self_test():
    ok = fail = 0
    print(f"{'paper form':<20} {'GT form':<16} {'canonical':<18} {'exp':>4} {'got':>4}")
    print("-" * 68)
    for a, b, expect in CORPUS_CASES:
        got = catalyst_match(a, b)
        mark = "✓" if got == expect else "✗ FAIL"
        ok, fail = (ok + 1, fail) if got == expect else (ok, fail + 1)
        print(f"{a:<20} {b:<16} {canonical_catalyst(a) or '—':<18} "
              f"{str(expect):>5} {str(got):>5}  {mark}")
    print("-" * 68)
    print(f"{ok} passed, {fail} failed")
    return fail == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)

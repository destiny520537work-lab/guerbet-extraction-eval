"""
Dimension-aware unit parsing, canonicalisation and numeric comparison.

WHY THIS MODULE EXISTS
----------------------
Before this module, evaluate.py compared numeric cells as lower-cased strings
with a flat 5% relative tolerance. That had three consequences:

  1. "10 Mpa" == "10 MPa" only because both were lower-cased. The equality was
     an accident of string handling, not a physical comparison.
  2. "1 bar" != "101 kPa", although they are the same pressure to within 0.3%.
  3. "623 - 723" was compared as the literal string "623-723", so a range was
     only ever equal to a byte-identical range.

This module supplies the missing physical layer: parse a written quantity into
(value | interval, unit), convert it to a canonical unit for its dimension, and
compare two quantities under an explicitly stated tolerance.

It is deliberately standalone (no dependency on evaluate.py or normalise.py) so
that the extraction pipeline can canonicalise units at extraction time, not only
at evaluation time. Together with normalise.py (composition-aware catalyst
identity) it forms the second half of Contribution C3: normalise.py resolves
*what the material is*, units.py resolves *what the number means*.

POLICY DECISIONS (documented because they affect reported scores)
----------------------------------------------------------------
* A quantity written without a unit is assumed to already be in the canonical
  unit of its dimension (kPa for pressure, K for temperature). The schema column
  names encode the expected unit (temperature_K), and every unit-less value in
  the corpus follows that convention.
* Trailing text that is not a recognised unit is ignored rather than treated as
  a parse failure: "15 mL g-1 h-1" in a space-velocity column parses as 15,
  because the column name already fixes the dimension. This is safe only for
  dimensionless columns, where no conversion is attempted.
* Ranges are first-class. A range is NOT silently collapsed to a midpoint or an
  endpoint, because both choices would fabricate a precision the source does not
  have.
"""

import re

# ── Numeric grammar ─────────────────────────────────────────────────────────
# Accepts 623, 0.3, .5, 1e-3, +4.4, -12. Kept explicit rather than using a
# looser \d+ so that "h-1" in "mL g-1 h-1" cannot be mistaken for a number that
# starts a range.
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# Unicode dashes seen in PDF-derived text, all folded to ASCII "-" before parsing.
_DASH_CHARS = "‐‑‒–—―−"

# Range separators. "-" is included, which is why _NUM must be anchored: a bare
# "-" only separates a range when a full number follows it.
_RANGE_SEP = r"(?:-|to|~|±|\.\.\.|\.\.)"

_RANGE_RE = re.compile(rf"^({_NUM})\s*{_RANGE_SEP}\s*({_NUM})\s*(.*)$", re.IGNORECASE)
_SINGLE_RE = re.compile(rf"^({_NUM})\s*(.*)$")

_NULL_TOKENS = {"", "none", "null", "n/a", "na", "-", "—", "nan"}


# ── Unit tables ─────────────────────────────────────────────────────────────
# Pressure, expressed as a multiplier onto the canonical unit kPa.
# 1 atm = 101.325 kPa exactly (SI definition); 1 psi = 6.894757 kPa;
# 1 Torr = 1 mmHg = 101.325/760 kPa.
PRESSURE_TO_KPA = {
    "pa": 1e-3,
    "hpa": 0.1,
    "kpa": 1.0,
    "mpa": 1e3,
    "gpa": 1e6,
    "bar": 100.0,
    "mbar": 0.1,
    "atm": 101.325,
    "torr": 101.325 / 760.0,
    "mmhg": 101.325 / 760.0,
    "psi": 6.894757,
    "psia": 6.894757,
    "psig": 6.894757,   # gauge vs absolute is not distinguishable from the text
}

_KELVIN_UNITS = {"k", "degk", "kelvin"}
_CELSIUS_UNITS = {"c", "degc", "celsius", "centigrade"}
_FAHRENHEIT_UNITS = {"f", "degf", "fahrenheit"}

CANONICAL_UNIT = {"pressure": "kPa", "temperature": "K"}


def _squash_unit(u: str) -> str:
    """Fold a written unit to a lookup key: strip degree marks, spaces, dots.

    "°C" -> "c", "deg C" -> "degc", "Mpa" -> "mpa", "kPa" -> "kpa".
    """
    if u is None:
        return ""
    u = u.strip().lower()
    u = re.sub(r"[°º∘˚℃℉\s\.\_]", "", u)
    return u


class Quantity:
    """A parsed physical quantity: a point (lo == hi) or a closed interval.

    lo/hi are stored in the canonical unit of the dimension. `unit` keeps the
    unit as written, for reporting; `raw` keeps the original string.
    """

    __slots__ = ("lo", "hi", "unit", "dimension", "raw")

    def __init__(self, lo, hi, unit=None, dimension=None, raw=""):
        self.lo = min(lo, hi)
        self.hi = max(lo, hi)
        self.unit = unit
        self.dimension = dimension
        self.raw = raw

    @property
    def is_range(self) -> bool:
        return self.lo != self.hi

    @property
    def value(self):
        """Point value; for a range this is the lower bound. Use with care."""
        return self.lo

    def __repr__(self):
        span = f"{self.lo}" if not self.is_range else f"{self.lo}..{self.hi}"
        unit = CANONICAL_UNIT.get(self.dimension, "")
        return f"<Quantity {span}{' ' + unit if unit else ''} from {self.raw!r}>"


def _convert(value: float, unit_key: str, dimension: str):
    """Convert `value` (written in `unit_key`) into the canonical unit.

    Returns None when the dimension is known but the unit is not recognised —
    the caller must then refuse to compare numerically rather than guess.
    """
    if dimension == "pressure":
        if not unit_key:
            return value                      # documented assumption: already kPa
        factor = PRESSURE_TO_KPA.get(unit_key)
        return None if factor is None else value * factor

    if dimension == "temperature":
        if not unit_key or unit_key in _KELVIN_UNITS:
            return value                      # documented assumption: already K
        if unit_key in _CELSIUS_UNITS:
            return value + 273.15
        if unit_key in _FAHRENHEIT_UNITS:
            return (value - 32.0) * 5.0 / 9.0 + 273.15
        return None

    # Dimensionless column (space velocities, loadings, percentages): the unit
    # is fixed by the schema column name, so trailing text is descriptive only.
    return value


def parse_quantity(text, dimension=None):
    """Parse a written quantity into a Quantity in canonical units, or None.

    None means "not numerically comparable" — the caller should fall back to
    string comparison rather than scoring the cell as a mismatch.
    """
    if text is None:
        return None
    s = str(text).strip()
    for ch in _DASH_CHARS:
        s = s.replace(ch, "-")
    if s.lower() in _NULL_TOKENS:
        return None

    m = _RANGE_RE.match(s)
    if m:
        lo_txt, hi_txt, tail = m.group(1), m.group(2), m.group(3)
    else:
        m = _SINGLE_RE.match(s)
        if not m:
            return None
        lo_txt = hi_txt = m.group(1)
        tail = m.group(2)

    try:
        lo, hi = float(lo_txt), float(hi_txt)
    except ValueError:
        return None

    # The unit is the first whitespace-delimited token of the tail, if any.
    # "10 MPa" -> "mpa"; "15 mL g-1 h-1" -> "mlg-1h-1" (unrecognised, and for a
    # dimensionless column that is fine); "60%" -> "%" (ignored).
    unit_raw = tail.strip()
    unit_key = _squash_unit(unit_raw.split()[0]) if unit_raw.split() else ""
    unit_key = unit_key.strip("%")

    c_lo = _convert(lo, unit_key, dimension)
    c_hi = _convert(hi, unit_key, dimension)
    if c_lo is None or c_hi is None:
        return None

    return Quantity(c_lo, c_hi, unit=unit_raw or None, dimension=dimension, raw=str(text))


def to_kPa(text):
    """Convenience: canonical pressure in kPa (lower bound if a range)."""
    q = parse_quantity(text, dimension="pressure")
    return None if q is None else q.lo


def to_K(text):
    """Convenience: canonical temperature in K (lower bound if a range)."""
    q = parse_quantity(text, dimension="temperature")
    return None if q is None else q.lo


# ── Comparison under an explicit tolerance ──────────────────────────────────
# tol_mode is "abs" (tolerance in canonical units, e.g. ±10 K, ±2 percentage
# points) or "rel" (fractional, e.g. ±5%). Absolute tolerance is required for
# any quantity whose zero point is arbitrary (temperature) or whose values
# approach zero (percentages), where a relative band is either far too wide or
# far too narrow.

MATCH_MISS = "miss"
MATCH_EXACT = "exact"
MATCH_RANGE = "range"      # point-in-interval hit: weaker evidence than exact
MATCH_NO_PARSE = "no_parse"


def _pad(reference_lo, reference_hi, tol_mode, tol) -> float:
    if tol_mode == "abs":
        return float(tol)
    scale = max(abs(reference_lo), abs(reference_hi), 1e-9)
    return float(tol) * scale


def _close(value, reference, tol_mode, tol) -> bool:
    return abs(value - reference) <= _pad(reference, reference, tol_mode, tol)


def values_match(pred, truth, dimension=None, tol_mode="rel", tol=0.05) -> str:
    """Compare two written quantities. Returns one of the MATCH_* constants.

    Interval semantics (this is the fix for the double-penalty bug: a predicted
    range against a single-point GT used to score FP *and* FN simultaneously):

      point  vs point  -> exact match within tolerance
      range  vs range  -> both endpoints must agree within tolerance
      range  vs point  -> the point must lie inside the interval, widened by the
                          column tolerance at each end. Reported as MATCH_RANGE
                          so it can be counted separately: a prediction of
                          "29-46" against a GT of "46" is a hit, but it is
                          weaker evidence than having produced "46" outright.
    """
    qp = parse_quantity(pred, dimension)
    qt = parse_quantity(truth, dimension)
    if qp is None or qt is None:
        return MATCH_NO_PARSE

    if qp.is_range and qt.is_range:
        ok = (_close(qp.lo, qt.lo, tol_mode, tol) and _close(qp.hi, qt.hi, tol_mode, tol))
        return MATCH_EXACT if ok else MATCH_MISS

    if not qp.is_range and not qt.is_range:
        return MATCH_EXACT if _close(qp.lo, qt.lo, tol_mode, tol) else MATCH_MISS

    rng, pt = (qp, qt) if qp.is_range else (qt, qp)
    pad = _pad(rng.lo, rng.hi, tol_mode, tol)
    inside = (rng.lo - pad) <= pt.lo <= (rng.hi + pad)
    return MATCH_RANGE if inside else MATCH_MISS


# ── Self-test ───────────────────────────────────────────────────────────────
_CASES = [
    # (pred, truth, dimension, tol_mode, tol, expected)
    # -- pressure canonicalisation (the capability the old code lacked) --
    ("10 Mpa",   "10 MPa",   "pressure",    "rel", 0.05, MATCH_EXACT),
    ("1 bar",    "101 kPa",  "pressure",    "rel", 0.05, MATCH_EXACT),
    ("1 atm",    "101 kPa",  "pressure",    "rel", 0.05, MATCH_EXACT),
    ("0.1 MPa",  "100 kPa",  "pressure",    "rel", 0.05, MATCH_EXACT),
    ("2 MPa",    "10 MPa",   "pressure",    "rel", 0.05, MATCH_MISS),
    ("39 kPa",   "101 kPa",  "pressure",    "rel", 0.05, MATCH_MISS),
    # -- temperature: absolute ±10 K, and the K/degC bridge --
    ("623",      "623",      "temperature", "abs", 10.0, MATCH_EXACT),
    ("623",      "653",      "temperature", "abs", 10.0, MATCH_MISS),   # 5% rel would have passed
    ("350 degC", "623",      "temperature", "abs", 10.0, MATCH_EXACT),  # 623.15 K
    ("300 C",    "573",      "temperature", "abs", 10.0, MATCH_EXACT),
    ("613",      "573",      "temperature", "abs", 10.0, MATCH_MISS),
    ("623 - 723", "623 - 723", "temperature", "abs", 10.0, MATCH_EXACT),
    ("673",      "623 - 723", "temperature", "abs", 10.0, MATCH_RANGE),
    ("523",      "623 - 723", "temperature", "abs", 10.0, MATCH_MISS),
    # -- percentage points: absolute, so near-zero behaves sanely --
    ("0",        "0.2",      None,          "abs", 2.0,  MATCH_EXACT),
    ("68",       "70",       None,          "abs", 2.0,  MATCH_EXACT),
    ("60",       "70",       None,          "abs", 2.0,  MATCH_MISS),
    ("29-46",    "46",       None,          "abs", 2.0,  MATCH_RANGE),
    ("26-80",    "80",       None,          "abs", 2.0,  MATCH_RANGE),
    ("15-30",    "15-30",    None,          "abs", 2.0,  MATCH_EXACT),
    ("29-46",    "10",       None,          "abs", 2.0,  MATCH_MISS),
    # -- dimensionless columns: unit text after the number is descriptive --
    ("15",       "15 mL g-1 h-1", None,     "rel", 0.10, MATCH_EXACT),
    ("4.8",      "4.5",      None,          "rel", 0.10, MATCH_EXACT),   # within 10%
    ("4.8",      "4.5",      None,          "rel", 0.05, MATCH_MISS),    # outside 5%
    ("0.3-1",    "0.3 - 1",  None,          "rel", 0.05, MATCH_EXACT),
    # -- non-numeric input must be refused, not scored --
    ("Cu/Mg-Al", "Cu-Mg-Al", None,          "rel", 0.05, MATCH_NO_PARSE),
    (None,       "101 kPa",  "pressure",    "rel", 0.05, MATCH_NO_PARSE),
    ("101 kPa",  "",         "pressure",    "rel", 0.05, MATCH_NO_PARSE),
]


def _self_test() -> bool:
    ok = fail = 0
    print(f"{'pred':<12} {'truth':<16} {'dim':<12} {'tol':<10} {'exp':<9} {'got':<9}")
    print("-" * 74)
    for pred, truth, dim, mode, tol, expect in _CASES:
        got = values_match(pred, truth, dimension=dim, tol_mode=mode, tol=tol)
        mark = "✓" if got == expect else "✗ FAIL"
        ok, fail = (ok + 1, fail) if got == expect else (ok, fail + 1)
        print(f"{str(pred):<12} {str(truth):<16} {str(dim):<12} "
              f"{mode + ' ' + str(tol):<10} {expect:<9} {got:<9}  {mark}")
    print("-" * 74)
    print(f"{ok} passed, {fail} failed")
    return fail == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)

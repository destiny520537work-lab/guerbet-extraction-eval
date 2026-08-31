"""Build patent_ground_truth.csv from Callum's patent spreadsheet.

Source: ../1.2.4 邮件7.24专利/MDS Patent Data Extraction.xlsx (3 US patents).
EP2889280A1 is EXCLUDED from cell scoring: its reference has no catalyst
identity (92/92 rows say "Metal oxide and Pd") and splits selectivity across
three product-specific columns that do not map onto the journal schema.

Conversions applied (documented for the thesis):
  Temperature / deg C  ->  temperature_K   (K = round(C + 273.15))
  Pressure / atm       ->  pressure        ("<kPa> kPa", kPa = round(atm*101.325))
Unmapped reference columns (recorded, not scored): Time on Stream, Contact Time, W/F.
Everything else is copied verbatim -- no editorial cleanup of curator strings.
"""
import csv, openpyxl
from pathlib import Path

SRC = Path("../1.2.4 邮件7.24专利/MDS Patent Data Extraction.xlsx")
OUT = Path("patent_ground_truth.csv")
PAPER_MAP = {
    "US 2010/0160692 A1": "US20100160692A1",
    "US 9,024,090 B2": "US9024090",
    "US 9,056,811 B2": "US9056811",
}
ws = openpyxl.load_workbook(SRC, data_only=True).active
rows = list(ws.values)
hdr_i = next(i for i, r in enumerate(rows) if r and "Patent" in [str(c).strip() for c in r if c])
hdr = [str(c).strip() if c is not None else "" for c in rows[hdr_i]]
col = {name: i for i, name in enumerate(hdr) if name}

def cell(r, name):
    i = col.get(name)
    v = r[i] if i is not None and i < len(r) else None
    return "" if v is None else str(v).strip()

out, skipped, unmapped_seen = [], 0, {"tos": 0, "wf": 0, "ct": 0}
for r in rows[hdr_i + 1:]:
    patent = cell(r, "Patent")
    if patent not in PAPER_MAP:
        if patent: skipped += 1
        continue
    tK = cell(r, "Temperature / deg C")
    try: tK = str(round(float(tK) + 273.15)) if tK else ""
    except ValueError: pass  # keep curator string (e.g. a range) untouched
    p = cell(r, "Pressure /atm")
    try: p = f"{round(float(p) * 101.325)} kPa" if p else ""
    except ValueError: pass
    if cell(r, "Time on Stream / h"): unmapped_seen["tos"] += 1
    if cell(r, "W/F"): unmapped_seen["wf"] += 1
    out.append({
        "open_access_repo": "", "open_access": "",
        "paper": PAPER_MAP[patent],
        "catalyst": cell(r, "Catalyst"),
        "loading_g": cell(r, "Loading / g"),
        "temperature_K": tK,
        "gas_mix": cell(r, "Gas Mix"),
        "flow_rate_mL_min": cell(r, "Flow rate / mL min-1"),
        "LHSV_h": cell(r, "LHSV / h-1"),
        "WHSV_h": cell(r, "WHSV / h-1"),
        "GHSV_h": cell(r, "GHSV / h-1"),
        "pressure": p,
        "conversion_pct": cell(r, "Conversion / %"),
        "selectivity_pct": cell(r, "Selectivity / %"),
    })

fields = ["open_access_repo","open_access","paper","catalyst","loading_g","temperature_K",
          "gas_mix","flow_rate_mL_min","LHSV_h","WHSV_h","GHSV_h","pressure",
          "conversion_pct","selectivity_pct"]
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
from collections import Counter
print(f"rows written: {len(out)}  per patent: {dict(Counter(r['paper'] for r in out))}")
print(f"skipped (non-US/header): {skipped}   unmapped fields seen: {unmapped_seen}")

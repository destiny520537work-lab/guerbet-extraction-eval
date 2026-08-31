"""Automated staged-condition comparison under strict and reference-restricted scoring."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figstyle as S

S.apply_style()

RUN = (Path(__file__).resolve().parents[2] / "extraction_pipeline" / "runs"
       / "grid_gateway_20260727_r4")
strict = json.loads((RUN / "scores_string.json").read_text())["table"]
restricted = json.loads((RUN / "scores_ref_string.json").read_text())["table"]

versions = ["B0", "V1", "V2", "V3", "V4"]
x = np.arange(len(versions))
strict_y = [strict[v]["f1_mean"] for v in versions]
strict_sd = [strict[v]["f1_sd"] for v in versions]
ref_y = [restricted[v]["f1_mean"] for v in versions]
ref_sd = [restricted[v]["f1_sd"] for v in versions]

fig, ax = plt.subplots(figsize=(S.FULL_W, 3.8))
ax.errorbar(x, strict_y, yerr=strict_sd, marker="o", linewidth=1.6,
            capsize=3, color=S.ACCENT, label="Strict against current reference")
ax.errorbar(x, ref_y, yerr=ref_sd, marker="s", linewidth=1.6,
            capsize=3, color=S.PRIMARY, label="Reference-restricted")

ax.set_xticks(x, versions)
ax.set_ylim(0, 0.8)
ax.set_ylabel("Pooled cell-level F1")
ax.set_xlabel("Extraction condition")
ax.yaxis.grid(True, color=S.RULE, linewidth=0.6)
ax.set_axisbelow(True)
S.strip_spines(ax)
ax.legend(loc="upper left", frameon=False)

for xi, y in zip(x, strict_y):
    ax.text(xi, y - 0.035, f"{y:.3f}", ha="center", va="top",
            fontsize=8, color=S.ACCENT)
for xi, y in zip(x, ref_y):
    ax.text(xi, y + 0.035, f"{y:.3f}", ha="center", va="bottom",
            fontsize=8, color=S.PRIMARY)

fig.subplots_adjust(left=0.11, right=0.97, top=0.95, bottom=0.13)

png, pdf = S.save(fig, "fig02_automated_ablation")
print(f"wrote {png}\nwrote {pdf}")

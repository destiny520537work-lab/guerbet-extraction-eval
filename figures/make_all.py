"""Rebuild every figure.

    ../.venv/bin/python make_all.py

Each script is run in a fresh interpreter so that one failure cannot leave
another script's rcParams or open figures behind.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCRIPTS = [
    "fig01_per_column_f1.py",
    "fig02_automated_ablation.py",
    "fig03_ocr_repair.py",
    "fig05_pipeline.py",
    "fig07_licence_flow.py",
]

failed = []
for script in SCRIPTS:
    print(f"── {script}")
    result = subprocess.run([sys.executable, script], cwd=HERE)
    if result.returncode != 0:
        failed.append(script)

if failed:
    print(f"\nFAILED: {', '.join(failed)}")
    sys.exit(1)
print(f"\n{len(SCRIPTS)} figures rebuilt into png/ and pdf/.")

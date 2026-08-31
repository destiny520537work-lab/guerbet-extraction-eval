"""Small deterministic regression tests for the evaluation contract."""

import unittest

import evaluate
import pipeline


class EvaluationContractTests(unittest.TestCase):
    def test_zero_f1_is_defined(self):
        self.assertEqual(evaluate.prf(0, 1, 1)[2], 0.0)

    def test_empty_precision_or_recall_remains_missing(self):
        self.assertIsNone(evaluate.prf(0, 0, 1)[0])
        self.assertIsNone(evaluate.prf(0, 1, 0)[1])

    def test_uncertainty_marker_is_metadata_not_numeric_text(self):
        row = pipeline.harmonise(
            {"catalyst": "Mg-Al", "temperature_K": "~573 K", "conversion_pct": "~20-80"},
            "Test 2026",
        )
        self.assertEqual(row["temperature_K"], "573 K")
        self.assertEqual(row["conversion_pct"], "20-80")
        self.assertEqual(row["_uncertain"], {"temperature_K": True, "conversion_pct": True})
        self.assertEqual(row["_raw_values"]["temperature_K"], "~573 K")

    def test_dedupe_preserves_distinct_conditions(self):
        base = {col: None for col in pipeline.SCHEMA_COLS}
        base.update({"paper": "Test 2026", "catalyst": "Mg-Al", "temperature_K": "573", "conversion_pct": "20"})
        a = dict(base, gas_mix="ethanol/Ar")
        b = dict(base, gas_mix="ethanol/Ar + CO2")
        self.assertEqual(len(pipeline.dedupe([a, b, a])), 2)

    def test_c3_cell_comparator_does_not_change_alignment_score(self):
        p = evaluate.Protocol(c3_match=True, c3_gate=False)
        pred = {"catalyst": "Cu/MgAlOx", "temperature_K": "553", "conversion_pct": "43.1"}
        gt = {"catalyst": "Cu/Mg-Al", "temperature_K": "553", "conversion_pct": "43.1"}
        self.assertEqual(evaluate._overlap_score(pred, gt, p),
                         evaluate._overlap_score(pred, gt, p.copy(c3_match=False)))


if __name__ == "__main__":
    unittest.main()

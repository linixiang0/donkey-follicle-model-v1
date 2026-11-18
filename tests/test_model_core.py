from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from model_core import PredictionInput, load_model, predict


class ModelCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model()
        cls.now = datetime(2026, 9, 10, 8, 0)

    def test_30mm_not_mature(self):
        result = predict(PredictionInput(self.now, 30.0), self.model)
        self.assertFalse(result.mature_now)
        self.assertGreater(result.maturity_center_hours, 30)
        self.assertLess(result.maturity_center_hours, 50)
        self.assertLess(result.ovulation_lower_hours, result.ovulation_upper_hours)

    def test_40mm_is_mature_and_ovulation_within_24_hours(self):
        result = predict(PredictionInput(self.now, 40.0), self.model)
        self.assertTrue(result.mature_now)
        self.assertEqual(result.maturity_center_hours, 0)
        self.assertLessEqual(result.ovulation_prediction_hours, 24)

    def test_individual_growth_changes_rate(self):
        result = predict(
            PredictionInput(
                current_time=self.now,
                current_max_mm=35,
                previous_max_mm=30,
                previous_time=self.now - timedelta(days=1),
            ),
            self.model,
        )
        self.assertEqual(result.growth_rate_source, "个体增长与品种先验融合")
        self.assertGreater(result.growth_rate_mm_day, 4.32)

    def test_invalid_decline_falls_back_to_prior(self):
        result = predict(
            PredictionInput(
                current_time=self.now,
                current_max_mm=35,
                previous_max_mm=40,
                previous_time=self.now - timedelta(days=1),
            ),
            self.model,
        )
        self.assertEqual(result.growth_rate_source, "品种文献先验")
        self.assertTrue(result.warnings)

    def test_induction_uses_time_window(self):
        result = predict(
            PredictionInput(
                current_time=self.now,
                current_max_mm=40,
                induced_ovulation=True,
                injection_time=self.now - timedelta(hours=12),
            ),
            self.model,
        )
        self.assertGreaterEqual(result.ovulation_lower_hours, 12)
        self.assertLessEqual(result.ovulation_upper_hours, 36)

    def test_early_output_35mm_regression(self):
        result = predict(PredictionInput(self.now, 35.0), self.model)
        self.assertEqual(result.maturity_prediction_hours, 8.1)
        self.assertEqual(result.ovulation_prediction_hours, 40.2)
        # Preserve the old centers for integrations and the mean-growth curve.
        self.assertEqual(result.maturity_center_hours, 11.1)
        self.assertEqual(result.ovulation_center_hours, 55.5)
        self.assertEqual(result.to_dict()["ovulation_prediction_hours"], 40.2)

    def test_early_output_uses_final_induction_window(self):
        for diameter in (30.0, 35.0, 40.0, 45.0, 60.0):
            for since_injection in (0, 12, 36, 60):
                with self.subTest(diameter=diameter, since_injection=since_injection):
                    result = predict(
                        PredictionInput(
                            self.now, diameter,
                            induced_ovulation=True,
                            injection_time=self.now - timedelta(hours=since_injection),
                        ),
                        self.model,
                    )
                    self.assertEqual(result.ovulation_prediction_hours, result.ovulation_lower_hours)
                    self.assertLessEqual(result.ovulation_prediction_hours, result.ovulation_center_hours)
        # 45 mm starts at zero naturally; recent induction moves the lower bound.
        adjusted = predict(
            PredictionInput(self.now, 45.0, induced_ovulation=True, injection_time=self.now - timedelta(hours=12)),
            self.model,
        )
        self.assertEqual(adjusted.ovulation_prediction_hours, 12.0)

    def test_out_of_range_current_diameter_is_rejected(self):
        # 1 mm曾产生177.1～168小时的倒置窗口，必须在计算前拒绝。
        for diameter in (1.0, 29.9, 60.1, float("nan"), float("inf")):
            with self.subTest(diameter=diameter):
                with self.assertRaisesRegex(ValueError, "30～60"):
                    predict(PredictionInput(self.now, diameter), self.model)

    def test_out_of_range_history_is_rejected(self):
        for diameter in (1.0, 29.9, 60.1, float("nan"), float("inf")):
            with self.subTest(diameter=diameter):
                with self.assertRaisesRegex(ValueError, "上次最大卵泡"):
                    predict(
                        PredictionInput(
                            self.now, 35.0,
                            previous_max_mm=diameter,
                            previous_time=self.now - timedelta(days=1),
                        ),
                        self.model,
                    )

    def test_allowed_range_keeps_ordered_windows(self):
        for profile in ("dezhou", "generic"):
            for index in range(300, 601):
                diameter = index / 10
                for previous_mm in (None, 30.0, 35.0, 60.0):
                    with self.subTest(profile=profile, diameter=diameter, previous=previous_mm):
                        result = predict(
                            PredictionInput(
                                self.now, diameter, profile,
                                previous_max_mm=previous_mm,
                                previous_time=self.now - timedelta(days=1) if previous_mm is not None else None,
                            ),
                            self.model,
                        )
                        self.assertLessEqual(result.maturity_lower_hours, result.maturity_center_hours)
                        self.assertLessEqual(result.maturity_center_hours, result.maturity_upper_hours)
                        self.assertLessEqual(result.ovulation_lower_hours, result.ovulation_center_hours)
                        self.assertLessEqual(result.ovulation_center_hours, result.ovulation_upper_hours)
                        self.assertEqual(result.maturity_prediction_hours, result.maturity_lower_hours)
                        self.assertEqual(result.ovulation_prediction_hours, result.ovulation_lower_hours)
                        self.assertGreaterEqual(result.maturity_prediction_hours, 0)
                        self.assertGreaterEqual(result.ovulation_prediction_hours, 0)


if __name__ == "__main__":
    unittest.main()


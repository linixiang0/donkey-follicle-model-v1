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

    def test_40mm_is_mature_and_high_risk(self):
        result = predict(PredictionInput(self.now, 40.0), self.model)
        self.assertTrue(result.mature_now)
        self.assertEqual(result.maturity_center_hours, 0)
        self.assertLessEqual(result.next_check_hours[1], 12)

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


if __name__ == "__main__":
    unittest.main()


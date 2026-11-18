from __future__ import annotations

import unittest
from datetime import datetime

from breeding_core import recommend_breeding
from model_core import PredictionInput, load_model, predict


class BreedingCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model()
        cls.now = datetime(2026, 11, 18, 8)

    def result(self, diameter=35.0, profile="dezhou"):
        return predict(PredictionInput(self.now, diameter, profile), self.model)

    def test_natural_and_fresh_default_plan(self):
        for method, hours in (("natural", 16.2), ("fresh", 16.2)):
            recommendation = recommend_breeding(self.result(), method)
            self.assertEqual(recommendation.hours, hours)
            self.assertEqual(recommendation.lead_source, "initial_default")

    def test_no_method_needed_for_default_fresh_workflow(self):
        recommendation = recommend_breeding(self.result())
        self.assertEqual(recommendation.method, "fresh")
        self.assertEqual(recommendation.hours, 16.2)

    def test_fresh_boundary_does_not_add_maturity_wait_or_grid_rounding(self):
        from dataclasses import replace
        for ovulation, expected in ((0,0), (23.9,0), (24,0), (29.9,5.9), (30,6), (35.9,11.9), (36,12), (40.2,16.2), (60.4,36.4)):
            with self.subTest(ovulation=ovulation):
                result = replace(self.result(), ovulation_prediction_hours=ovulation)
                recommendation = recommend_breeding(result)
                self.assertEqual(recommendation.hours, expected)

    def test_no_wait_once_early_ovulation_is_within_24_hours(self):
        self.assertEqual(recommend_breeding(self.result(40), "fresh").hours, 0)

    def test_37mm_reference_does_not_delay_first_service(self):
        result = self.result(profile="generic")
        recommendation = recommend_breeding(result, "fresh")
        self.assertEqual(recommendation.hours, 12.5)
        self.assertLess(recommendation.hours, result.maturity_prediction_hours)

    def test_cooled_and_frozen_need_confirmed_lead(self):
        for method in ("cooled", "frozen"):
            blocked = recommend_breeding(self.result(), method)
            self.assertEqual(blocked.status, "protocol_required")
            self.assertIsNone(blocked.hours)
            planned = recommend_breeding(self.result(), method, confirmed_lead_hours=6)
            self.assertEqual(planned.hours, 34.2)
            self.assertEqual(planned.lead_source, "site_confirmed")

    def test_induction_is_not_replaced_by_relative_ovulation_lead(self):
        for method in ("natural", "fresh", "cooled", "frozen"):
            recommendation = recommend_breeding(self.result(), method, induced_ovulation=True, confirmed_lead_hours=6)
            self.assertEqual(recommendation.status, "protocol_required")
            self.assertIsNone(recommendation.hours)

    def test_missing_method_does_not_assume_a_semen_type(self):
        recommendation = recommend_breeding(self.result(), "unspecified", confirmed_lead_hours=6)
        self.assertEqual(recommendation.status, "method_required")
        self.assertIsNone(recommendation.hours)

    def test_zero_lead_is_valid_but_invalid_leads_are_rejected(self):
        self.assertEqual(recommend_breeding(self.result(), "fresh", confirmed_lead_hours=0).hours, 40.2)
        for invalid in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                recommend_breeding(self.result(), "fresh", confirmed_lead_hours=invalid)


if __name__ == "__main__":
    unittest.main()

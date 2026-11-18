from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import api


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def setUp(self):
        api.API_KEY = ""

    def sample_payload(self):
        return {
            "client_request_id": "case-1",
            "animal_id": "370001",
            "current_time": "2026-09-23T10:00:00+08:00",
            "breed_profile": "dezhou",
            "left": {"status": "mm", "value_mm": 35.0},
            "right": {"status": "SF", "value_mm": None},
            "induction": {"used": False, "injection_time": None},
        }

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_web_page_and_assets(self):
        page = self.client.get("/")
        script = self.client.get("/static/app.js")
        stylesheet = self.client.get("/static/styles.css")
        self.assertEqual(page.status_code, 200)
        self.assertIn("排卵模型第一版", page.text)
        self.assertNotIn("API 密钥", page.text)
        self.assertNotIn("母驴编号", page.text)
        self.assertNotIn("品种参数", page.text)
        self.assertNotIn('id="breedingMethod"', page.text)
        self.assertNotIn('id="breedingLead"', page.text)
        self.assertNotIn('id="inductionUsed"', page.text)
        self.assertIn("建议配种时间（鲜精）", page.text)
        self.assertNotIn("建议下次超声", page.text)
        self.assertNotIn("Countdown", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("drawGrowthChart", script.text)
        self.assertEqual(stylesheet.status_code, 200)

    def test_predict_numeric(self):
        response = self.client.post("/v1/predict", json=self.sample_payload())
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["prediction_status"], "predicted")
        self.assertEqual(body["current_max_mm"], 35.0)
        self.assertFalse(body["mature_now"])
        self.assertEqual(body["breeding_status"], "planned")
        self.assertEqual(body["breeding_method"], "fresh")
        self.assertEqual(body["breeding_recommendation"]["hours"], 16.2)
        self.assertNotIn("next_ultrasound_window", body)
        self.assertLess(body["growth_rate_lower_mm_day"], body["growth_rate_mm_day"])
        self.assertGreater(body["growth_rate_upper_mm_day"], body["growth_rate_mm_day"])
        self.assertEqual(body["maturity_reference_mm"], 37.0)
        self.assertLess(body["ovulation_window"]["lower_hours"], body["ovulation_window"]["upper_hours"])
        self.assertEqual(body["point_estimate_policy"], "window_lower_bound")
        current_time = datetime.fromisoformat(self.sample_payload()["current_time"])
        for name in ("maturity", "ovulation"):
            point = body[f"{name}_prediction"]
            window = body[f"{name}_window"]
            self.assertEqual(point["hours"], window["lower_hours"])
            self.assertEqual(point["time"], window["lower_time"])
            self.assertEqual(datetime.fromisoformat(point["time"]), current_time + timedelta(hours=point["hours"]))
            self.assertLess(point["hours"], body[f"{name}_center"]["hours"])

    def test_cl_returns_post_ovulation(self):
        payload = self.sample_payload()
        payload["right"] = {"status": "CL", "value_mm": None}
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prediction_status"], "post_ovulation")
        self.assertEqual(response.json()["breeding_status"], "post_ovulation")
        self.assertIsNone(response.json()["breeding_recommendation"])
        for field in ("maturity_prediction", "ovulation_prediction", "point_estimate_policy"):
            self.assertIsNone(response.json()[field])

    def test_early_output_with_history_and_induction(self):
        payload = self.sample_payload()
        payload["left"]["value_mm"] = 45.0
        payload["previous"] = {"time": "2026-09-22T10:00:00+08:00", "max_follicle_mm": 40.0}
        payload["induction"] = {"used": True, "injection_time": "2026-09-22T22:00:00+08:00"}
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["ovulation_prediction"]["hours"], 12.0)
        self.assertEqual(body["ovulation_prediction"]["time"], body["ovulation_window"]["lower_time"])
        self.assertEqual(body["maturity_prediction"]["hours"], 0.0)

    def test_already_at_reference_uses_current_time(self):
        payload = self.sample_payload()
        payload["left"]["value_mm"] = 45.0
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["prediction_status"], "predicted")
        for name in ("maturity", "ovulation"):
            self.assertEqual(body[f"{name}_prediction"]["hours"], 0.0)
            self.assertEqual(datetime.fromisoformat(body[f"{name}_prediction"]["time"]), datetime.fromisoformat(payload["current_time"]))

    def test_breeding_plan_and_timezone(self):
        for method, hours in (("natural", 16.2), ("fresh", 16.2)):
            payload = self.sample_payload()
            payload["breeding_method"] = method
            response = self.client.post("/v1/predict", json=payload)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["breeding_status"], "planned")
            self.assertEqual(body["breeding_recommendation"]["hours"], hours)
            self.assertEqual(body["breeding_lead_hours"], 24)
            point_time = datetime.fromisoformat(body["breeding_recommendation"]["time"])
            self.assertEqual(point_time, datetime.fromisoformat(payload["current_time"]) + timedelta(hours=hours))

    def test_breeding_protocol_and_post_ovulation(self):
        for method in ("cooled", "frozen"):
            payload = self.sample_payload()
            payload["breeding_method"] = method
            body = self.client.post("/v1/predict", json=payload).json()
            self.assertEqual(body["breeding_status"], "protocol_required")
            self.assertIsNone(body["breeding_recommendation"])
            payload["breeding_lead_hours"] = 6.0
            body = self.client.post("/v1/predict", json=payload).json()
            self.assertEqual(body["breeding_recommendation"]["hours"], 34.2)
            self.assertEqual(body["breeding_lead_source"], "site_confirmed")
            payload["right"] = {"status": "CL", "value_mm": None}
            body = self.client.post("/v1/predict", json=payload).json()
            self.assertIsNone(body["breeding_recommendation"])

    def test_induction_does_not_automatically_choose_a_breeding_protocol(self):
        payload = self.sample_payload()
        payload["breeding_method"] = "fresh"
        payload["breeding_lead_hours"] = 6.0
        payload["induction"] = {"used": True, "injection_time": "2026-09-22T22:00:00+08:00"}
        body = self.client.post("/v1/predict", json=payload).json()
        self.assertEqual(body["breeding_status"], "protocol_required")
        self.assertIsNone(body["breeding_recommendation"])

    def test_breeding_invalid_method_and_negative_lead_are_rejected(self):
        for extra in ({"breeding_method": "other"}, {"breeding_lead_hours": -1}):
            payload = self.sample_payload() | extra
            self.assertEqual(self.client.post("/v1/predict", json=payload).status_code, 422)

    def test_both_sf_is_rejected(self):
        payload = self.sample_payload()
        payload["left"] = {"status": "SF", "value_mm": None}
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_naive_datetime_is_rejected(self):
        payload = self.sample_payload()
        payload["current_time"] = "2026-09-23T10:00:00"
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_api_key(self):
        api.API_KEY = "secret-key"
        denied = self.client.post("/v1/predict", json=self.sample_payload())
        allowed = self.client.post(
            "/v1/predict",
            json=self.sample_payload(),
            headers={"X-API-Key": "secret-key"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(allowed.status_code, 200)

    def test_current_numeric_range_on_each_side(self):
        for side in ("left", "right"):
            for diameter, expected_status in ((1.0, 422), (29.9, 422), (30.0, 200), (60.0, 200), (60.1, 422)):
                with self.subTest(side=side, diameter=diameter):
                    payload = self.sample_payload()
                    payload[side] = {"status": "mm", "value_mm": diameter}
                    response = self.client.post("/v1/predict", json=payload)
                    self.assertEqual(response.status_code, expected_status)
                    if expected_status == 422:
                        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
                    else:
                        self.assertEqual(response.json()["current_max_mm"], max(35.0, diameter) if side == "right" else diameter)

    def test_history_numeric_range(self):
        for diameter, expected_status in ((1.0, 422), (29.9, 422), (30.0, 200), (60.0, 200), (60.1, 422)):
            with self.subTest(diameter=diameter):
                payload = self.sample_payload()
                payload["previous"] = {
                    "time": "2026-09-22T10:00:00+08:00",
                    "max_follicle_mm": diameter,
                }
                response = self.client.post("/v1/predict", json=payload)
                self.assertEqual(response.status_code, expected_status)

    def test_sf_and_cl_still_require_null_numeric_value(self):
        for status in ("SF", "CL"):
            for value, expected_status in ((None, 200), (30.0, 422)):
                with self.subTest(status=status, value=value):
                    payload = self.sample_payload()
                    payload["right"] = {"status": status, "value_mm": value}
                    response = self.client.post("/v1/predict", json=payload)
                    self.assertEqual(response.status_code, expected_status)

    def test_openapi_advertises_numeric_limits(self):
        schemas = self.client.get("/openapi.json").json()["components"]["schemas"]
        numeric = next(item for item in schemas["FollicleMeasurement"]["properties"]["value_mm"]["anyOf"] if item["type"] == "number")
        previous = schemas["PreviousMeasurement"]["properties"]["max_follicle_mm"]
        for field in (numeric, previous):
            self.assertEqual(field["minimum"], 30.0)
            self.assertEqual(field["maximum"], 60.0)


if __name__ == "__main__":
    unittest.main()

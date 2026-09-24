from __future__ import annotations

import unittest

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
        self.assertNotIn("促排信息", page.text)
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
        self.assertLess(body["growth_rate_lower_mm_day"], body["growth_rate_mm_day"])
        self.assertGreater(body["growth_rate_upper_mm_day"], body["growth_rate_mm_day"])
        self.assertEqual(body["maturity_reference_mm"], 37.0)
        self.assertLess(body["ovulation_window"]["lower_hours"], body["ovulation_window"]["upper_hours"])

    def test_cl_returns_post_ovulation(self):
        payload = self.sample_payload()
        payload["right"] = {"status": "CL", "value_mm": None}
        response = self.client.post("/v1/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prediction_status"], "post_ovulation")

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


if __name__ == "__main__":
    unittest.main()

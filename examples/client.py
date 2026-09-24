from __future__ import annotations

import json
import os
import urllib.request


API_BASE_URL = os.getenv("DONKEY_API_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("DONKEY_API_KEY", "")

payload = {
    "client_request_id": "EO-20260923-0001",
    "animal_id": "370001",
    "current_time": "2026-09-23T10:00:00+08:00",
    "breed_profile": "dezhou",
    "left": {"status": "mm", "value_mm": 35.0},
    "right": {"status": "SF", "value_mm": None},
    "previous": {
        "time": "2026-09-22T10:00:00+08:00",
        "max_follicle_mm": 31.0,
    },
    "induction": {"used": False, "injection_time": None},
}

request = urllib.request.Request(
    f"{API_BASE_URL}/v1/predict",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
    method="POST",
)

with urllib.request.urlopen(request, timeout=30) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))


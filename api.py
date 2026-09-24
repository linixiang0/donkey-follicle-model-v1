from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from model_core import DEFAULT_MODEL_PATH, PredictionInput, at_time, load_model, predict


logging.basicConfig(
    level=os.getenv("DONKEY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("donkey_follicle_api")
API_KEY = os.getenv("DONKEY_API_KEY", "").strip()
ENABLE_DOCS = os.getenv("DONKEY_ENABLE_DOCS", "true").lower() in {"1", "true", "yes"}
WEB_DIR = Path(__file__).resolve().parent / "web"


class FollicleStatus(str, Enum):
    MM = "mm"
    SF = "SF"
    CL = "CL"


class FollicleMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: FollicleStatus = Field(description="mm=有毫米数值，SF=小卵泡，CL=已观察到黄体")
    value_mm: float | None = Field(default=None, gt=0, le=60)

    @model_validator(mode="after")
    def check_value(self) -> "FollicleMeasurement":
        if self.status == FollicleStatus.MM and self.value_mm is None:
            raise ValueError("status为mm时必须提供value_mm")
        if self.status != FollicleStatus.MM and self.value_mm is not None:
            raise ValueError("status为SF或CL时value_mm必须为空")
        return self


class PreviousMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    max_follicle_mm: float = Field(gt=0, le=60)


class InductionInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used: bool = False
    injection_time: datetime | None = None

    @model_validator(mode="after")
    def check_injection_time(self) -> "InductionInformation":
        if self.used and self.injection_time is None:
            raise ValueError("used为true时必须提供injection_time")
        return self


class PredictRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
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
        },
    )

    client_request_id: str | None = Field(default=None, max_length=100)
    animal_id: str | None = Field(default=None, max_length=100)
    current_time: datetime
    breed_profile: Literal["dezhou", "generic"] = "dezhou"
    left: FollicleMeasurement
    right: FollicleMeasurement
    previous: PreviousMeasurement | None = None
    induction: InductionInformation = Field(default_factory=InductionInformation)

    @model_validator(mode="after")
    def check_times_and_measurements(self) -> "PredictRequest":
        times = [("current_time", self.current_time)]
        if self.previous is not None:
            times.append(("previous.time", self.previous.time))
        if self.induction.injection_time is not None:
            times.append(("induction.injection_time", self.induction.injection_time))
        for name, value in times:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name}必须包含时区，例如+08:00")

        if self.previous is not None and self.previous.time >= self.current_time:
            raise ValueError("previous.time必须早于current_time")
        if self.induction.injection_time is not None and self.induction.injection_time > self.current_time:
            raise ValueError("injection_time不能晚于current_time")

        statuses = {self.left.status, self.right.status}
        has_numeric = FollicleStatus.MM in statuses
        has_cl = FollicleStatus.CL in statuses
        if not has_numeric and not has_cl:
            raise ValueError("左右侧均为SF时无法预测，至少需要一个毫米数值或CL")
        return self


class TimeWindow(BaseModel):
    lower_hours: float
    upper_hours: float
    lower_time: datetime
    upper_time: datetime


class PointEstimate(BaseModel):
    hours: float
    time: datetime


class PredictResponse(BaseModel):
    success: bool = True
    request_id: str
    client_request_id: str | None
    model_version: str
    prediction_status: Literal["predicted", "post_ovulation"]
    animal_id: str | None
    current_max_mm: float | None = None
    mature_now: bool | None = None
    maturity_center: PointEstimate | None = None
    maturity_window: TimeWindow | None = None
    ovulation_center: PointEstimate | None = None
    ovulation_window: TimeWindow | None = None
    next_ultrasound_window: TimeWindow | None = None
    cl_confirmation_median: PointEstimate | None = None
    cl_confirmation_iqr: TimeWindow | None = None
    growth_rate_mm_day: float | None = None
    growth_rate_lower_mm_day: float | None = None
    growth_rate_upper_mm_day: float | None = None
    growth_rate_source: str | None = None
    maturity_reference_mm: float | None = None
    preovulatory_reference_mm: float | None = None
    support_level: str | None = None
    support_note: str | None = None
    observed_post_ovulation_interval: TimeWindow | None = None
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str


@lru_cache(maxsize=1)
def get_model() -> dict[str, Any]:
    return load_model(DEFAULT_MODEL_PATH)


@asynccontextmanager
async def lifespan(_: FastAPI):
    model = get_model()
    LOGGER.info("model_loaded version=%s", model["model_version"])
    yield


app = FastAPI(
    title="东阿驴卵泡成熟与排卵预测接口",
    description=(
        "根据当前卵泡直径、可选历史摸排和促排时间，返回成熟时间、排卵窗口和建议复查时间。"
        "该接口为生产决策支持，不替代兽医超声判断。"
    ),
    version="1.0.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


cors_origins = [item.strip() for item in os.getenv("DONKEY_CORS_ORIGINS", "").split(",") if item.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    details = []
    for error in exc.errors():
        details.append(
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "details": details,
            },
        },
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    code = {
        401: "UNAUTHORIZED",
        404: "NOT_FOUND",
        422: "PREDICTION_INPUT_ERROR",
    }.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "success": False,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "error": {"code": code, "message": str(exc.detail), "details": []},
        },
    )


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if API_KEY and (x_api_key is None or not secrets.compare_digest(x_api_key, API_KEY)):
        raise HTTPException(status_code=401, detail="无效或缺少X-API-Key")


def make_window(base: datetime, lower: float, upper: float) -> TimeWindow:
    return TimeWindow(
        lower_hours=round(lower, 1),
        upper_hours=round(upper, 1),
        lower_time=at_time(base, lower),
        upper_time=at_time(base, upper),
    )


def make_point(base: datetime, hours: float) -> PointEstimate:
    return PointEstimate(hours=round(hours, 1), time=at_time(base, hours))


@app.get("/health", tags=["system"])
async def health() -> dict[str, Any]:
    model = get_model()
    return {"status": "ok", "service_version": app.version, "model_version": model["model_version"]}


@app.get("/", include_in_schema=False)
async def web_home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/v1/model/info", tags=["model"], dependencies=[Depends(verify_api_key)])
async def model_info() -> dict[str, Any]:
    model = get_model()
    calibration = model["local_calibration"]
    profiles = model["literature_priors"]["breed_profiles"]
    return {
        "success": True,
        "model_version": model["model_version"],
        "model_type": model["model_type"],
        "supported_breed_profiles": {
            key: {
                "display_name": value["display_name"],
                "maturity_reference_mm": value["maturity_reference_mm"],
                "preovulatory_reference_mm": value["preovulatory_reference_mm"],
            }
            for key, value in profiles.items()
        },
        "local_calibration": {
            "valid_interval_cycles": calibration["valid_interval_cycles"],
            "unique_donkeys": calibration["unique_donkeys"],
            "validation": calibration["validation"],
        },
        "important_limit": "验证误差针对首次观察到CL的时间，不是真实排卵时刻误差。",
    }


@app.post(
    "/v1/predict",
    response_model=PredictResponse,
    tags=["prediction"],
    dependencies=[Depends(verify_api_key)],
)
async def predict_follicle(payload: PredictRequest, request: Request) -> PredictResponse:
    model = get_model()
    request_id = request.state.request_id
    measurements = (payload.left, payload.right)

    if any(item.status == FollicleStatus.CL for item in measurements):
        observed_interval = None
        if payload.previous is not None:
            interval_hours = (payload.current_time - payload.previous.time).total_seconds() / 3600
            observed_interval = make_window(payload.previous.time, 0.0, interval_hours)
        LOGGER.info("prediction_completed request_id=%s status=post_ovulation", request_id)
        return PredictResponse(
            request_id=request_id,
            client_request_id=payload.client_request_id,
            model_version=model["model_version"],
            prediction_status="post_ovulation",
            animal_id=payload.animal_id,
            observed_post_ovulation_interval=observed_interval,
            warnings=["当前已观察到CL，只能确认已经进入排卵后状态，不能反推出精确排卵时刻。"],
            disclaimer="结果用于安排复查和配种，不替代兽医超声判断。",
        )

    numeric_values = [item.value_mm for item in measurements if item.status == FollicleStatus.MM]
    if not numeric_values:
        raise HTTPException(status_code=422, detail="至少需要一侧提供毫米数值")

    current_max_mm = max(value for value in numeric_values if value is not None)
    previous = payload.previous
    item = PredictionInput(
        current_time=payload.current_time,
        current_max_mm=current_max_mm,
        breed_profile=payload.breed_profile,
        previous_max_mm=previous.max_follicle_mm if previous else None,
        previous_time=previous.time if previous else None,
        induced_ovulation=payload.induction.used,
        injection_time=payload.induction.injection_time,
    )

    try:
        result = predict(item, model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    LOGGER.info("prediction_completed request_id=%s status=predicted", request_id)
    return PredictResponse(
        request_id=request_id,
        client_request_id=payload.client_request_id,
        model_version=model["model_version"],
        prediction_status="predicted",
        animal_id=payload.animal_id,
        current_max_mm=result.current_max_mm,
        mature_now=result.mature_now,
        maturity_center=make_point(payload.current_time, result.maturity_center_hours),
        maturity_window=make_window(
            payload.current_time,
            result.maturity_lower_hours,
            result.maturity_upper_hours,
        ),
        ovulation_center=make_point(payload.current_time, result.ovulation_center_hours),
        ovulation_window=make_window(
            payload.current_time,
            result.ovulation_lower_hours,
            result.ovulation_upper_hours,
        ),
        next_ultrasound_window=make_window(
            payload.current_time,
            result.next_check_hours[0],
            result.next_check_hours[1],
        ),
        cl_confirmation_median=make_point(
            payload.current_time,
            result.cl_confirmation_median_hours,
        ),
        cl_confirmation_iqr=make_window(
            payload.current_time,
            result.cl_confirmation_iqr_hours[0],
            result.cl_confirmation_iqr_hours[1],
        ),
        growth_rate_mm_day=result.growth_rate_mm_day,
        growth_rate_lower_mm_day=result.growth_rate_lower_mm_day,
        growth_rate_upper_mm_day=result.growth_rate_upper_mm_day,
        growth_rate_source=result.growth_rate_source,
        maturity_reference_mm=result.maturity_reference_mm,
        preovulatory_reference_mm=result.preovulatory_reference_mm,
        support_level=result.support_level,
        support_note=result.support_note,
        warnings=list(result.warnings),
        disclaimer=(
            "排卵时间是结合文献先验和本场CL记录得到的风险窗口；"
            "首次确认CL时间不等于真实排卵时刻。本结果不替代兽医判断。"
        ),
    )

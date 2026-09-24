from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "v1_model.json"


@dataclass(frozen=True)
class PredictionInput:
    current_time: datetime
    current_max_mm: float
    breed_profile: str = "dezhou"
    previous_max_mm: Optional[float] = None
    previous_time: Optional[datetime] = None
    induced_ovulation: bool = False
    injection_time: Optional[datetime] = None


@dataclass(frozen=True)
class PredictionOutput:
    current_max_mm: float
    growth_rate_mm_day: float
    growth_rate_lower_mm_day: float
    growth_rate_upper_mm_day: float
    growth_rate_source: str
    maturity_reference_mm: float
    preovulatory_reference_mm: float
    mature_now: bool
    maturity_center_hours: float
    maturity_lower_hours: float
    maturity_upper_hours: float
    ovulation_center_hours: float
    ovulation_lower_hours: float
    ovulation_upper_hours: float
    cl_confirmation_median_hours: float
    cl_confirmation_iqr_hours: tuple[float, float]
    next_check_hours: tuple[float, float]
    support_level: str
    support_note: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_model(path: Path | str = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    model_path = Path(path)
    with model_path.open("r", encoding="utf-8") as handle:
        model = json.load(handle)
    if model.get("model_version") != "1.0.0":
        raise ValueError(f"不支持的模型版本: {model.get('model_version')}")
    return model


def _linear_value(x: float, anchors: list[dict[str, Any]], key: str) -> float:
    points = sorted((float(a["diameter_mm"]), float(a[key])) for a in anchors)
    if not points:
        raise ValueError("模型中没有可用的本场直径锚点")
    if len(points) == 1:
        return points[0][1]

    if x <= points[0][0]:
        p0, p1 = points[0], points[1]
    elif x >= points[-1][0]:
        p0, p1 = points[-2], points[-1]
    else:
        for left, right in zip(points, points[1:]):
            if left[0] <= x <= right[0]:
                p0, p1 = left, right
                break
    slope = (p1[1] - p0[1]) / (p1[0] - p0[0])
    return p0[1] + slope * (x - p0[0])


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _estimate_growth(
    item: PredictionInput,
    profile: dict[str, Any],
) -> tuple[float, float, str, list[str]]:
    prior_mean = float(profile["growth_rate_mm_day_mean"])
    prior_sd = float(profile["growth_rate_mm_day_sd"])
    warnings: list[str] = []

    if item.previous_max_mm is None or item.previous_time is None:
        return prior_mean, prior_sd, "品种文献先验", warnings

    delta_hours = (item.current_time - item.previous_time).total_seconds() / 3600
    if delta_hours < 6:
        warnings.append("前后两次摸排间隔不足6小时，未使用个体生长速度。")
        return prior_mean, prior_sd, "品种文献先验", warnings

    observed = (item.current_max_mm - item.previous_max_mm) / (delta_hours / 24)
    if observed <= 0 or observed > 10:
        warnings.append("个体生长速度为非正值或超过10 mm/天，可能存在测量差异，未用于修正。")
        return prior_mean, prior_sd, "品种文献先验", warnings

    individual_weight = float(profile.get("single_interval_weight", 0.35))
    combined = individual_weight * observed + (1 - individual_weight) * prior_mean
    adjusted_sd = max(0.5, prior_sd * 0.85)
    return combined, adjusted_sd, "个体增长与品种先验融合", warnings


def _support(current_mm: float, anchors: list[dict[str, Any]]) -> tuple[str, str]:
    nearest = min(anchors, key=lambda a: abs(float(a["diameter_mm"]) - current_mm))
    distance = abs(float(nearest["diameter_mm"]) - current_mm)
    n = int(nearest["n"])
    if distance <= 1 and n >= 30:
        return "较强", f"接近本场 {nearest['diameter_mm']:g} mm 锚点，样本 {n} 个。"
    if distance <= 3 and n >= 10:
        return "中等", f"由邻近本场锚点插值得到，最近样本点为 {nearest['diameter_mm']:g} mm。"
    return "较弱", "输入直径远离本场主要的30、35、40 mm记录，结果包含外推。"


def _recommend_check(current_mm: float, ovulation_lower: float) -> tuple[float, float]:
    if ovulation_lower <= 12 or current_mm >= 40:
        return (6.0, 12.0)
    if current_mm >= 35:
        return (12.0, 18.0)
    if current_mm >= 30:
        return (18.0, 24.0)
    return (24.0, 48.0)


def predict(item: PredictionInput, model: dict[str, Any]) -> PredictionOutput:
    if not math.isfinite(item.current_max_mm) or item.current_max_mm <= 0:
        raise ValueError("当前最大卵泡必须是大于0的毫米数值")
    if item.current_max_mm > 60:
        raise ValueError("当前最大卵泡超过60 mm，请核对录入值或交由兽医判断")

    profiles = model["literature_priors"]["breed_profiles"]
    if item.breed_profile not in profiles:
        raise ValueError(f"未知品种参数: {item.breed_profile}")
    profile = profiles[item.breed_profile]
    anchors = model["local_calibration"]["anchors"]
    if not anchors:
        raise ValueError("模型没有本场校准锚点")

    growth, growth_sd, growth_source, warnings = _estimate_growth(item, profile)
    growth_fast = _bounded(growth + growth_sd, 1.0, 10.0)
    growth_slow = _bounded(growth - growth_sd, 1.0, 10.0)

    maturity_mm = float(profile["maturity_reference_mm"])
    maturity_delta = max(0.0, maturity_mm - item.current_max_mm)
    maturity_center = maturity_delta / growth * 24
    maturity_lower = maturity_delta / growth_fast * 24
    maturity_upper = maturity_delta / growth_slow * 24

    preovulatory_mm = float(profile["preovulatory_reference_mm"])
    ov_delta = max(0.0, preovulatory_mm - item.current_max_mm)
    biological_center = ov_delta / growth * 24
    biological_lower = ov_delta / growth_fast * 24
    biological_upper = ov_delta / growth_slow * 24 + 12

    cl_median = _bounded(_linear_value(item.current_max_mm, anchors, "median_hours"), 12, 168)
    cl_q25 = _bounded(_linear_value(item.current_max_mm, anchors, "q25_hours"), 6, 168)
    cl_q75 = _bounded(_linear_value(item.current_max_mm, anchors, "q75_hours"), 12, 168)
    if cl_q25 > cl_q75:
        cl_q25, cl_q75 = cl_q75, cl_q25

    ov_lower = max(maturity_lower, biological_lower)
    ov_upper = min(cl_median, biological_upper)
    if ov_upper < ov_lower + 6:
        ov_upper = min(168.0, max(ov_lower + 6, cl_median))
    ov_center = _bounded(biological_center, ov_lower, ov_upper)

    if item.induced_ovulation:
        if item.injection_time is None:
            warnings.append("选择了促排但未填写注射时间，仍按自然周期计算。")
        else:
            since_injection = (item.current_time - item.injection_time).total_seconds() / 3600
            if since_injection < 0:
                warnings.append("促排注射时间晚于当前摸排时间，未使用促排窗口。")
            else:
                drug_lower = max(0.0, 24.0 - since_injection)
                drug_upper = max(0.0, 48.0 - since_injection)
                intersect_lower = max(ov_lower, drug_lower)
                intersect_upper = min(ov_upper, drug_upper)
                if intersect_lower <= intersect_upper:
                    ov_lower, ov_upper = intersect_lower, intersect_upper
                    ov_center = (ov_lower + ov_upper) / 2
                else:
                    warnings.append("卵泡大小窗口与促排时间窗口不重叠，已扩大区间并降低可信度。")
                    ov_lower = min(ov_lower, drug_lower)
                    ov_upper = max(ov_upper, drug_upper)
                    ov_center = (ov_lower + ov_upper) / 2

    support_level, support_note = _support(item.current_max_mm, anchors)
    if item.induced_ovulation and warnings and "不重叠" in warnings[-1]:
        support_level = "较弱"

    next_check = _recommend_check(item.current_max_mm, ov_lower)
    return PredictionOutput(
        current_max_mm=round(item.current_max_mm, 2),
        growth_rate_mm_day=round(growth, 2),
        growth_rate_lower_mm_day=round(growth_slow, 2),
        growth_rate_upper_mm_day=round(growth_fast, 2),
        growth_rate_source=growth_source,
        maturity_reference_mm=round(maturity_mm, 2),
        preovulatory_reference_mm=round(preovulatory_mm, 2),
        mature_now=item.current_max_mm >= maturity_mm,
        maturity_center_hours=round(maturity_center, 1),
        maturity_lower_hours=round(maturity_lower, 1),
        maturity_upper_hours=round(maturity_upper, 1),
        ovulation_center_hours=round(ov_center, 1),
        ovulation_lower_hours=round(ov_lower, 1),
        ovulation_upper_hours=round(ov_upper, 1),
        cl_confirmation_median_hours=round(cl_median, 1),
        cl_confirmation_iqr_hours=(round(cl_q25, 1), round(cl_q75, 1)),
        next_check_hours=next_check,
        support_level=support_level,
        support_note=support_note,
        warnings=tuple(warnings),
    )


def at_time(base: datetime, hours: float) -> datetime:
    return base + timedelta(hours=hours)

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from model_core import PredictionOutput


class BreedingMethod(str, Enum):
    UNSPECIFIED = "unspecified"
    NATURAL = "natural"
    FRESH = "fresh"
    COOLED = "cooled"
    FROZEN = "frozen"


BREEDING_METHOD_LABELS = {
    BreedingMethod.UNSPECIFIED: "请选择配种方式",
    BreedingMethod.NATURAL: "自然交配",
    BreedingMethod.FRESH: "鲜精人工授精",
    BreedingMethod.COOLED: "冷藏精液人工授精",
    BreedingMethod.FROZEN: "冻精人工授精",
}
BREEDING_LEAD_HOURS = 24.0


@dataclass(frozen=True)
class BreedingRecommendation:
    status: Literal["planned", "method_required", "protocol_required"]
    method: BreedingMethod
    hours: float | None
    lead_hours: float | None
    lead_source: Literal["initial_default", "site_confirmed"] | None
    policy: Literal["early_ovulation_minus_lead"] | None
    note: str


def recommend_breeding(
    result: PredictionOutput,
    method: BreedingMethod | str = BreedingMethod.FRESH,
    *,
    induced_ovulation: bool = False,
    confirmed_lead_hours: float | None = None,
) -> BreedingRecommendation:
    """Return a conditional first-service plan, not a fertility prediction.

    Fresh semen is the site's main workflow. The default first-service plan
    uses the early ovulation estimate minus 24h. Potentially date-only registrations are
    not used to fit an hour-level offset or a scheduling grid.
    """
    method = BreedingMethod(method)
    if confirmed_lead_hours is not None and (not math.isfinite(confirmed_lead_hours) or confirmed_lead_hours < 0):
        raise ValueError("现场确认的配种提前量必须是非负有限小时数。")
    if method == BreedingMethod.UNSPECIFIED:
        return BreedingRecommendation(
            "method_required", method, None, None, None, None,
            "请选择配种方式后计算首次配种计划；不同精液类型不能统一套用排卵前24小时。",
        )
    if induced_ovulation:
        return BreedingRecommendation(
            "protocol_required", method, None, None, None, None,
            "已促排时需按具体药物、注射后时间及超声监测方案安排，当前信息不足，不自动套用排卵前提前量。",
        )
    if method in (BreedingMethod.COOLED, BreedingMethod.FROZEN) and confirmed_lead_hours is None:
        return BreedingRecommendation(
            "protocol_required", method, None, None, None, None,
            "冷藏精液/冻精没有通用的提前24小时规则；请按具体精液质量、保存时间和监测方案填写现场已确认的排卵前提前量，再计算首次配种计划。",
        )
    lead_hours = confirmed_lead_hours if confirmed_lead_hours is not None else BREEDING_LEAD_HOURS
    lead_source = "site_confirmed" if confirmed_lead_hours is not None else "initial_default"
    hours = round(max(0.0, result.ovulation_prediction_hours - lead_hours), 1)
    parameter_note = "提前量来自现场方案，未由本模型验证。" if confirmed_lead_hours is not None else "24小时为待本场校准的初始参数，并非已证实的最佳时机。"
    return BreedingRecommendation(
        "planned", method, hours, lead_hours, lead_source, "early_ovulation_minus_lead",
        f"{'按鲜精人工授精常规场景估计。' if method == BreedingMethod.FRESH else ''}建议配种计划，按偏早排卵时间提前{lead_hours:g}小时安排。{parameter_note}执行前确认发情、卵泡发育和子宫状态，复查后决定是否追配。",
    )

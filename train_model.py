from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REFERENCE_LINKS = [
    {
        "title": "Unraveling the Transcriptomic Profiles of Large and Small Donkey Follicles",
        "url": "https://pubmed.ncbi.nlm.nih.gov/40428424/",
        "use": "37 mm成熟参考；排卵卵泡最大直径约40.7 mm（新疆驴研究）",
    },
    {
        "title": "Estrus synchronization and follicular development patterns in jennies",
        "url": "https://pubmed.ncbi.nlm.nih.gov/41654244/",
        "use": "德州驴生长4.32±1.64 mm/天；排卵前直径44.99±1.70 mm",
    },
    {
        "title": "Efficacy of hCG and GnRH for inducing ovulation in the jenny",
        "url": "https://pubmed.ncbi.nlm.nih.gov/17716724/",
        "use": "促排后24-48小时风险窗口；卵泡越大，距排卵越短",
    },
]


def _as_flag(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).eq(1)


def _clean_id(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def load_observations(path: Path) -> pd.DataFrame:
    data = pd.read_excel(path, sheet_name=0)
    required = {
        "donkey_id", "estrus_id", "mapping_time", "left_follicle_size",
        "right_follicle_size", "left_follicle_value", "right_follicle_value",
        "pl_or_not",
    }
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Excel缺少字段: {', '.join(sorted(missing))}")

    data = data.dropna(how="all").copy()
    data["donkey_id"] = data["donkey_id"].map(_clean_id)
    data["estrus_id"] = data["estrus_id"].map(_clean_id)
    data["mapping_time"] = pd.to_datetime(data["mapping_time"], errors="coerce")
    data = data[data["donkey_id"].notna() & data["estrus_id"].notna() & data["mapping_time"].notna()].copy()

    data["left_mm"] = pd.to_numeric(data["left_follicle_value"], errors="coerce")
    data["right_mm"] = pd.to_numeric(data["right_follicle_value"], errors="coerce")
    data["max_mm"] = data[["left_mm", "right_mm"]].max(axis=1, skipna=True)
    data.loc[data[["left_mm", "right_mm"]].isna().all(axis=1), "max_mm"] = np.nan
    data["has_cl"] = data[["left_follicle_size", "right_follicle_size"]].eq("CL").any(axis=1)
    data["verified_ovulation"] = _as_flag(data["pl_or_not"]) & data["has_cl"]
    return data.sort_values(["donkey_id", "estrus_id", "mapping_time"])


def build_intervals(data: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (donkey_id, estrus_id), group in data.groupby(["donkey_id", "estrus_id"], sort=False):
        group = group.sort_values("mapping_time")
        event_rows = group[group["verified_ovulation"]]
        if event_rows.empty:
            continue
        event_time = event_rows["mapping_time"].min()
        prior = group[(group["mapping_time"] < event_time) & group["max_mm"].notna()]
        if prior.empty:
            continue
        baseline = prior.iloc[-1]
        hours = (event_time - baseline["mapping_time"]).total_seconds() / 3600
        rows.append({
            "donkey_id": donkey_id,
            "estrus_id": estrus_id,
            "baseline_time": baseline["mapping_time"],
            "diameter_mm": float(baseline["max_mm"]),
            "upper_hours": float(hours),
        })
    intervals = pd.DataFrame(rows)
    if intervals.empty:
        raise ValueError("没有找到‘数值卵泡→已确认CL’的可训练周期")
    return intervals


def filter_intervals(intervals: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    stats = {"raw_interval_cycles": int(len(intervals))}
    valid = intervals[
        intervals["diameter_mm"].between(10, 60)
        & intervals["upper_hours"].between(0.01, 168)
    ].copy()
    valid["diameter_mm"] = valid["diameter_mm"].round(0)
    stats["valid_interval_cycles"] = int(len(valid))
    stats["excluded_cycles"] = int(len(intervals) - len(valid))
    return valid, stats


def build_anchors(valid: pd.DataFrame, min_n: int = 5) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for diameter, group in valid.groupby("diameter_mm"):
        if len(group) < min_n:
            continue
        q = group["upper_hours"].quantile([0.25, 0.5, 0.75, 0.9])
        anchors.append({
            "diameter_mm": float(diameter),
            "n": int(len(group)),
            "median_hours": round(float(q.loc[0.5]), 2),
            "q25_hours": round(float(q.loc[0.25]), 2),
            "q75_hours": round(float(q.loc[0.75]), 2),
            "p90_hours": round(float(q.loc[0.9]), 2),
        })
    return sorted(anchors, key=lambda x: x["diameter_mm"])


def _interpolate(x: float, anchors: list[dict[str, Any]], key: str) -> float:
    points = sorted((float(a["diameter_mm"]), float(a[key])) for a in anchors)
    if len(points) == 1:
        return points[0][1]
    if x <= points[0][0]:
        left, right = points[0], points[1]
    elif x >= points[-1][0]:
        left, right = points[-2], points[-1]
    else:
        left, right = next((a, b) for a, b in zip(points, points[1:]) if a[0] <= x <= b[0])
    return left[1] + (right[1] - left[1]) * (x - left[0]) / (right[0] - left[0])


def grouped_validation(valid: pd.DataFrame) -> dict[str, Any]:
    fold = valid["donkey_id"].map(lambda value: int(hashlib.sha1(value.encode("utf-8")).hexdigest(), 16) % 5)
    predictions = np.full(len(valid), np.nan)
    y = valid["upper_hours"].to_numpy(float)
    for fold_id in range(5):
        train = valid[fold != fold_id]
        test_positions = np.flatnonzero((fold == fold_id).to_numpy())
        anchors = build_anchors(train, min_n=3)
        if not anchors:
            predictions[test_positions] = float(train["upper_hours"].median())
            continue
        for position in test_positions:
            predictions[position] = np.clip(_interpolate(float(valid.iloc[position]["diameter_mm"]), anchors, "median_hours"), 12, 168)
    error = np.abs(predictions - y)
    return {
        "scheme": "5-fold split by donkey_id",
        "n": int(len(valid)),
        "unique_donkeys": int(valid["donkey_id"].nunique()),
        "mae_hours": round(float(error.mean()), 2),
        "median_absolute_error_hours": round(float(np.median(error)), 2),
        "within_12_hours_pct": round(float((error <= 12).mean() * 100), 2),
        "within_24_hours_pct": round(float((error <= 24).mean() * 100), 2),
        "target": "first verified CL observation time; not true ovulation time",
    }


def make_model(input_path: Path) -> dict[str, Any]:
    data = load_observations(input_path)
    intervals = build_intervals(data)
    valid, interval_stats = filter_intervals(intervals)
    anchors = build_anchors(valid)
    if len(anchors) < 2:
        raise ValueError("有效直径锚点不足2个，无法建立插值模型")

    model = {
        "model_version": "1.0.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_type": "local interval calibration plus literature-informed growth model",
        "local_calibration": {
            "source_file": input_path.name,
            "usable_rows": int(len(data)),
            "unique_donkeys": int(data["donkey_id"].nunique()),
            "unique_estrus_cycles": int(data[["donkey_id", "estrus_id"]].drop_duplicates().shape[0]),
            **interval_stats,
            "anchors": anchors,
            "validation": grouped_validation(valid),
            "label_definition": "last numeric scan before first row with pl_or_not=1 and CL; event is interval-censored",
        },
        "literature_priors": {
            "breed_profiles": {
                "dezhou": {
                    "display_name": "东阿/德州驴（默认）",
                    "growth_rate_mm_day_mean": 4.32,
                    "growth_rate_mm_day_sd": 1.64,
                    "maturity_reference_mm": 37.0,
                    "preovulatory_reference_mm": 44.99,
                    "single_interval_weight": 0.35,
                    "note": "37 mm为可修改的成熟参考；生长和排卵前直径使用德州驴研究先验。",
                },
                "generic": {
                    "display_name": "品种不确定/其他驴",
                    "growth_rate_mm_day_mean": 3.15,
                    "growth_rate_mm_day_sd": 0.60,
                    "maturity_reference_mm": 37.0,
                    "preovulatory_reference_mm": 40.70,
                    "single_interval_weight": 0.35,
                    "note": "沿用上一版汇总表的保守生长速度和37 mm成熟参考。",
                },
            },
            "induction_window_hours": [24.0, 48.0],
            "references": REFERENCE_LINKS,
        },
        "disabled_features": {
            "weather": "No visit-level weather linkage or transferable hour-level coefficient in V1.",
            "temperature": "Recorded for future calibration only; not used in prediction.",
            "humidity": "Recorded for future calibration only; not used in prediction.",
        },
    }
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="训练东阿驴卵泡成熟与排卵预测V1参数")
    parser.add_argument("--input", required=True, type=Path, help="原始摸排Excel")
    parser.add_argument("--output", default=Path("models/v1_model.json"), type=Path)
    args = parser.parse_args()

    model = make_model(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(model, handle, ensure_ascii=False, indent=2)
    print(json.dumps(model["local_calibration"], ensure_ascii=False, indent=2))
    print(f"模型参数已保存: {args.output.resolve()}")


if __name__ == "__main__":
    main()


from __future__ import annotations

from datetime import date, datetime, time

import streamlit as st

from breeding_core import recommend_breeding
from model_core import DEFAULT_MODEL_PATH, MAX_FOLLICLE_MM, MIN_FOLLICLE_MM, PredictionInput, at_time, load_model, predict


st.set_page_config(page_title="东阿驴卵泡成熟与排卵预测", layout="wide")


@st.cache_resource
def get_model():
    return load_model(DEFAULT_MODEL_PATH)


def combined_datetime(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock)


def fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def follicle_input(label: str, key: str):
    status = st.selectbox(label, ["毫米数值", "SF", "CL"], key=f"{key}_status")
    if status == "毫米数值":
        size = st.number_input(f"{label}直径（mm）", min_value=MIN_FOLLICLE_MM, max_value=MAX_FOLLICLE_MM, value=35.0, step=0.5, key=f"{key}_mm")
        return status, float(size)
    return status, None


model = get_model()
profiles = model["literature_priors"]["breed_profiles"]
display_to_key = {value["display_name"]: key for key, value in profiles.items()}

st.title("东阿驴卵泡成熟与排卵预测 V1")
st.caption("本场历史数据校准＋驴繁殖文献先验。输出用于安排复查和配种，不替代兽医超声判断。")
st.caption("毫米数值限定为30～60 mm（含边界）；小于30 mm请选择SF，SF和CL不填写直径。")

with st.sidebar:
    st.subheader("模型状态")
    calibration = model["local_calibration"]
    st.write(f"有效校准周期：{calibration['valid_interval_cycles']}")
    st.write(f"母驴数量：{calibration['unique_donkeys']}")
    validation = calibration["validation"]
    st.write(f"CL确认时间组外MAE：{validation['mae_hours']}小时")
    st.caption("该误差针对首次确认CL的记录时间，不是真实排卵时刻误差。")
    st.divider()
    st.write("天气、温度、湿度：V1未启用")
    st.caption("缺少逐次摸排对应气象数据和可迁移的小时级系数。")

st.subheader("当前摸排")
time_col1, time_col2, breed_col = st.columns([1, 1, 1.4])
with time_col1:
    current_day = st.date_input("摸排日期", value=date.today())
with time_col2:
    current_clock = st.time_input("摸排时间", value=datetime.now().replace(second=0, microsecond=0).time())
with breed_col:
    breed_display = st.selectbox("品种参数", list(display_to_key.keys()), index=0)

left_col, right_col = st.columns(2)
with left_col:
    left_status, left_mm = follicle_input("左侧卵泡", "left")
with right_col:
    right_status, right_mm = follicle_input("右侧卵泡", "right")

with st.expander("可选：上一次摸排，用于估计个体生长速度"):
    use_previous = st.checkbox("填写上一次记录")
    previous_day = st.date_input("上次日期", value=date.today(), disabled=not use_previous)
    previous_clock = st.time_input("上次时间", value=time(8, 0), disabled=not use_previous)
    previous_mm = st.number_input("上次最大卵泡（mm）", min_value=MIN_FOLLICLE_MM, max_value=MAX_FOLLICLE_MM, value=30.0, step=0.5, disabled=not use_previous, help="上次仅有SF时，不勾选填写上一次记录。")

with st.expander("可选：促排信息"):
    induced = st.checkbox("已使用hCG或GnRH等促排方案")
    injection_day = st.date_input("注射日期", value=date.today(), disabled=not induced)
    injection_clock = st.time_input("注射时间", value=time(8, 0), disabled=not induced)

if st.button("开始预测", type="primary"):
    current_time = combined_datetime(current_day, current_clock)
    statuses = {left_status, right_status}

    if "CL" in statuses:
        st.success("当前已观察到CL，系统判断已经进入排卵后状态。")
        if use_previous:
            previous_time = combined_datetime(previous_day, previous_clock)
            if previous_time < current_time:
                st.write(f"排卵发生区间：{fmt_dt(previous_time)} 至 {fmt_dt(current_time)}")
        st.stop()

    numeric_values = [value for value in (left_mm, right_mm) if value is not None]
    if not numeric_values:
        st.error("左右侧均没有毫米数值，只有SF时无法预测成熟和排卵时间。")
        st.stop()

    previous_time = combined_datetime(previous_day, previous_clock) if use_previous else None
    injection_time = combined_datetime(injection_day, injection_clock) if induced else None
    item = PredictionInput(
        current_time=current_time,
        current_max_mm=max(numeric_values),
        breed_profile=display_to_key[breed_display],
        previous_max_mm=float(previous_mm) if use_previous else None,
        previous_time=previous_time,
        induced_ovulation=induced,
        injection_time=injection_time,
    )

    try:
        result = predict(item, model)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.subheader("预测结果")
    st.caption("以下日期时刻均为北京时间。")
    a, d = st.columns(2)
    a.metric("当前最大卵泡", f"{result.current_max_mm:g} mm")
    a.caption(f"生长速度：{result.growth_rate_mm_day:g} mm/天，{result.growth_rate_source}")

    st.metric("成熟预测时间（偏早）", fmt_dt(at_time(current_time, result.maturity_prediction_hours)))
    st.caption("当前已达到成熟参考" if result.mature_now else "按成熟窗口最早时刻预测")

    st.metric("排卵预测时间（偏早）", fmt_dt(at_time(current_time, result.ovulation_prediction_hours)))
    st.caption("从该时刻起重点关注" if result.ovulation_prediction_hours <= 0 else "按排卵窗口最早时刻预测")

    d.metric("模型支持", result.support_level)
    d.caption(result.support_note)

    st.caption("成熟和排卵的单点预测均采用对应窗口的最早时间，优先提前安排；仍可能晚于真实时间。")

    breeding = recommend_breeding(result, induced_ovulation=induced)
    st.subheader("建议配种时间（鲜精）")
    if breeding.hours is not None:
        st.metric("建议配种时间", fmt_dt(at_time(current_time, breeding.hours)))
    st.info(breeding.note)

    for warning in result.warnings:
        st.warning(warning)

    with st.expander("计算依据与限制"):
        st.write(f"首次CL确认参考时刻：{fmt_dt(at_time(current_time, result.cl_confirmation_median_hours))}")
        st.caption("CL确认时间通常晚于或等于真实排卵时间，不能把它直接当作排卵发生时刻。")
        profile = profiles[item.breed_profile]
        st.write(f"成熟参考直径：{profile['maturity_reference_mm']} mm")
        st.write(f"排卵前参考直径：{profile['preovulatory_reference_mm']} mm")
        st.write(f"品种生长速度先验：{profile['growth_rate_mm_day_mean']}±{profile['growth_rate_mm_day_sd']} mm/天")
        st.write("排卵窗口的下界来自卵泡达到排卵前参考直径的最快生长估计；上界受本场首次CL确认中位时间约束。")
        st.write("现有数据不能量化天气和温度的独立作用，因此V1未使用这些变量。")

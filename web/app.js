const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  result: null,
  payload: null,
  playTimer: null,
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  setDefaultTimes();
  bindEvents();
  checkService();
});

function setDefaultTimes() {
  const now = new Date();
  $("currentTime").value = chinaDatetimeLocal(now);
  $("previousTime").value = chinaDatetimeLocal(new Date(now.getTime() - 24 * 3600 * 1000));
}

function chinaDatetimeLocal(date) {
  return new Date(date.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 16);
}

function toChinaIso(value) {
  if (!value) return null;
  return `${value.length === 16 ? value + ":00" : value}+08:00`;
}

function bindEvents() {
  document.querySelectorAll(".segmented").forEach((group) => {
    group.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (!button) return;
      const side = group.dataset.side;
      group.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
      $(`${side}Status`).value = button.dataset.value;
      $(`${side}MeasureRow`).classList.toggle("hidden", button.dataset.value !== "mm");
    });
  });

  $("fillDemo").addEventListener("click", fillDemo);
  $("predictionForm").addEventListener("submit", submitPrediction);
  $("timelineSlider").addEventListener("input", () => {
    stopTimeline();
    if (state.result) drawGrowthChart(state.result, state.payload);
  });
  $("playTimeline").addEventListener("click", toggleTimeline);
  window.addEventListener("resize", () => {
    if (state.result && state.result.prediction_status === "predicted") drawGrowthChart(state.result, state.payload);
  });
}

async function checkService() {
  const status = $("serviceStatus");
  try {
    const response = await fetch("/health", { cache: "no-store" });
    if (!response.ok) throw new Error("服务异常");
    const data = await response.json();
    status.className = "service-status online";
    status.querySelector("span:last-child").textContent = `模型 ${data.model_version} 已连接`;
  } catch (_) {
    status.className = "service-status offline";
    status.querySelector("span:last-child").textContent = "模型连接失败";
  }
}

function fillDemo() {
  const now = new Date();
  $("currentTime").value = chinaDatetimeLocal(now);
  setSegment("left", "mm");
  setSegment("right", "SF");
  $("leftMm").value = "35.0";
  $("previousBlock").open = true;
  $("previousTime").value = chinaDatetimeLocal(new Date(now.getTime() - 24 * 3600 * 1000));
  $("previousMm").value = "31.0";
  hideError();
}

function setSegment(side, value) {
  const group = document.querySelector(`.segmented[data-side="${side}"]`);
  group.querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.dataset.value === value));
  $(`${side}Status`).value = value;
  $(`${side}MeasureRow`).classList.toggle("hidden", value !== "mm");
}

function measurement(side) {
  const status = $(`${side}Status`).value;
  if (status !== "mm") return { status, value_mm: null };
  const value = Number($(`${side}Mm`).value);
  if (!Number.isFinite(value) || value <= 0 || value > 60) throw new Error(`${side === "left" ? "左" : "右"}侧卵泡直径应在 1–60 mm 之间`);
  return { status: "mm", value_mm: value };
}

function buildPayload() {
  const currentTime = toChinaIso($("currentTime").value);
  if (!currentTime) throw new Error("请填写当前摸排时间");

  const left = measurement("left");
  const right = measurement("right");
  if (left.status === "SF" && right.status === "SF") throw new Error("左右侧均为 SF 时无法预测，请至少提供一个直径或 CL");

  let previous = null;
  if ($("previousBlock").open) {
    const time = toChinaIso($("previousTime").value);
    const maxMm = Number($("previousMm").value);
    if (!time || !Number.isFinite(maxMm) || maxMm <= 0 || maxMm > 60) throw new Error("请完整填写有效的上一次摸排时间和最大卵泡");
    if (new Date(time) >= new Date(currentTime)) throw new Error("上一次摸排时间必须早于当前摸排时间");
    previous = { time, max_follicle_mm: maxMm };
  }

  return {
    client_request_id: `WEB-${Date.now()}`,
    animal_id: null,
    current_time: currentTime,
    breed_profile: "dezhou",
    left,
    right,
    previous,
    induction: { used: false, injection_time: null },
  };
}

async function submitPrediction(event) {
  event.preventDefault();
  hideError();
  stopTimeline();

  let payload;
  try {
    payload = buildPayload();
  } catch (error) {
    return showError(error.message);
  }

  const button = $("predictButton");
  button.disabled = true;
  button.querySelector("span:first-child").textContent = "模型正在计算…";

  try {
    const headers = { "Content-Type": "application/json" };
    const response = await fetch("/v1/predict", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(apiErrorMessage(data, response.status));
    state.result = data;
    state.payload = payload;
    renderResult(data, payload);
  } catch (error) {
    showError(error.message || "请求失败，请确认接口已经启动");
  } finally {
    button.disabled = false;
    button.querySelector("span:first-child").textContent = "生成成熟与排卵预测";
  }
}

function apiErrorMessage(data, status) {
  if (status === 401) return "当前接口开启了密钥验证。本地测试时请不要使用 --env-file .env，重新启动服务后再试。";
  const base = data?.error?.message || data?.detail || `接口返回错误（${status}）`;
  const details = data?.error?.details;
  if (!Array.isArray(details) || !details.length) return base;
  return `${base}：${details.map((item) => `${item.field || "参数"} ${item.message}`).join("；")}`;
}

function renderResult(result, payload) {
  $("emptyState").hidden = true;
  $("resultContent").hidden = false;
  $("resultTitle").textContent = "预测结果";
  $("resultSubtitle").textContent = `${formatDateTime(payload.current_time)} · 模型 ${result.model_version}`;

  if (result.prediction_status === "post_ovulation") {
    $("postOvulation").hidden = false;
    $("predictionResults").hidden = true;
    $("supportBadge").textContent = "已观察到 CL";
    $("supportBadge").className = "support-badge";
    const interval = result.observed_post_ovulation_interval;
    $("postOvulationText").textContent = interval
      ? `只能判断排卵发生在 ${formatDateTime(interval.lower_time)} 至 ${formatDateTime(interval.upper_time)} 之间，无法反推出精确时刻。`
      : "已经进入排卵后状态；缺少上一次记录，无法进一步缩小排卵区间。";
    return;
  }

  $("postOvulation").hidden = true;
  $("predictionResults").hidden = false;
  const support = result.support_level || "未知";
  $("supportBadge").textContent = `数据支持度：${support}`;
  $("supportBadge").className = `support-badge${support === "较弱" ? " weak" : ""}`;

  $("maturityCountdown").textContent = result.mature_now ? "当前已达参考" : formatDuration(result.maturity_center.hours);
  $("maturityTime").textContent = formatDateTime(result.maturity_center.time);
  $("maturityWindow").textContent = `可能范围：${formatWindow(result.maturity_window)}`;
  $("ovulationCountdown").textContent = formatDuration(result.ovulation_center.hours);
  $("ovulationTime").textContent = formatDateTime(result.ovulation_center.time);
  $("ovulationWindow").textContent = `风险窗口：${formatWindow(result.ovulation_window)}`;
  $("checkCountdown").textContent = `${formatShortHours(result.next_ultrasound_window.lower_hours)}–${formatShortHours(result.next_ultrasound_window.upper_hours)}`;
  $("checkTime").textContent = formatWindow(result.next_ultrasound_window);

  $("currentMax").textContent = `${formatNumber(result.current_max_mm)} mm`;
  $("growthRate").textContent = `${formatNumber(result.growth_rate_mm_day)} mm/天（${result.growth_rate_source}）`;
  $("maturityReference").textContent = `${formatNumber(result.maturity_reference_mm)} mm`;
  $("ovulationReference").textContent = `${formatNumber(result.preovulatory_reference_mm)} mm`;
  $("supportNote").textContent = result.support_note || "";

  const warnings = [...(result.warnings || [])];
  warnings.push(result.disclaimer);
  const list = $("warningList");
  list.replaceChildren(...warnings.map((warning) => {
    const li = document.createElement("li");
    li.textContent = warning;
    return li;
  }));

  $("timelineSlider").value = "0";
  drawGrowthChart(result, payload);
  $("resultContent").scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatNumber(value) {
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatDuration(hours) {
  const value = Number(hours);
  if (value <= 0.05) return "当前已达到";
  if (value < 24) return `约 ${value.toFixed(1)} 小时`;
  return `约 ${(value / 24).toFixed(1)} 天`;
}

function formatShortHours(hours) {
  const value = Number(hours);
  return value >= 24 ? `${(value / 24).toFixed(1)}天` : `${value.toFixed(0)}小时`;
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value)).replaceAll("/", "-");
}

function formatWindow(windowValue) {
  return `${formatDateTime(windowValue.lower_time)} — ${formatDateTime(windowValue.upper_time)}`;
}

function showError(message) {
  const box = $("formError");
  box.textContent = message;
  box.hidden = false;
}

function hideError() {
  $("formError").hidden = true;
  $("formError").textContent = "";
}

function svgElement(tag, attrs = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== null) node.textContent = text;
  return node;
}

function drawGrowthChart(result, payload) {
  const svg = $("growthChart");
  svg.replaceChildren();

  const width = 960;
  const height = 430;
  const margin = { top: 38, right: 56, bottom: 70, left: 64 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const currentTime = new Date(payload.current_time);
  const currentMm = Number(result.current_max_mm);
  const previous = payload.previous;
  const previousHours = previous ? (new Date(previous.time) - currentTime) / 3600000 : -12;
  const ovLower = Number(result.ovulation_window.lower_hours);
  const ovCenter = Math.max(0.1, Number(result.ovulation_center.hours));
  const ovUpper = Number(result.ovulation_window.upper_hours);
  const maturityCenter = Number(result.maturity_center.hours);
  const xMin = Math.min(-6, previousHours - 3);
  const xMax = Math.max(24, ovUpper + 4, maturityCenter + 6);
  const centerRate = Number(result.growth_rate_mm_day);
  const slowRate = Number(result.growth_rate_lower_mm_day);
  const fastRate = Number(result.growth_rate_upper_mm_day);
  const maturityMm = Number(result.maturity_reference_mm);
  const preovMm = Number(result.preovulatory_reference_mm);
  const previousMm = previous ? Number(previous.max_follicle_mm) : currentMm;
  const yMin = Math.max(0, Math.floor((Math.min(previousMm, currentMm, maturityMm) - 5) / 5) * 5);
  const yMax = Math.min(65, Math.ceil((Math.max(preovMm, currentMm, previousMm) + 5) / 5) * 5);
  const x = (hours) => margin.left + ((hours - xMin) / (xMax - xMin)) * plotW;
  const y = (mm) => margin.top + (1 - (mm - yMin) / (yMax - yMin)) * plotH;
  const cap = (value) => Math.min(preovMm, value);
  const diameter = (hours, rate) => cap(currentMm + rate * Math.max(0, hours) / 24);

  svg.appendChild(svgElement("rect", { x: margin.left, y: margin.top, width: plotW, height: plotH, rx: 12, fill: "#fbfdfb" }));

  const ovX1 = x(Math.max(xMin, ovLower));
  const ovX2 = x(Math.min(xMax, ovUpper));
  svg.appendChild(svgElement("rect", { x: ovX1, y: margin.top, width: Math.max(2, ovX2 - ovX1), height: plotH, fill: "rgba(229,139,67,.10)" }));
  svg.appendChild(svgElement("text", { x: (ovX1 + ovX2) / 2, y: margin.top + 17, "text-anchor": "middle", fill: "#b36a31", "font-size": 11, "font-weight": 700 }, "排卵风险窗口"));

  const yTicks = 5;
  for (let i = 0; i <= yTicks; i += 1) {
    const value = yMin + ((yMax - yMin) * i) / yTicks;
    const py = y(value);
    svg.appendChild(svgElement("line", { x1: margin.left, y1: py, x2: width - margin.right, y2: py, stroke: "#e6ede9", "stroke-width": 1 }));
    svg.appendChild(svgElement("text", { x: margin.left - 12, y: py + 4, "text-anchor": "end", fill: "#71877f", "font-size": 11 }, `${value.toFixed(0)}`));
  }
  svg.appendChild(svgElement("text", { x: 18, y: margin.top + plotH / 2, transform: `rotate(-90 18 ${margin.top + plotH / 2})`, "text-anchor": "middle", fill: "#71877f", "font-size": 11 }, "最大卵泡直径（mm）"));

  const xTicks = 6;
  for (let i = 0; i <= xTicks; i += 1) {
    const hour = xMin + ((xMax - xMin) * i) / xTicks;
    const px = x(hour);
    const date = new Date(currentTime.getTime() + hour * 3600000);
    svg.appendChild(svgElement("line", { x1: px, y1: margin.top, x2: px, y2: height - margin.bottom, stroke: "#edf2ef", "stroke-width": 1 }));
    svg.appendChild(svgElement("text", { x: px, y: height - margin.bottom + 23, "text-anchor": "middle", fill: "#71877f", "font-size": 10 }, dateLabel(date)));
    svg.appendChild(svgElement("text", { x: px, y: height - margin.bottom + 39, "text-anchor": "middle", fill: "#91a19c", "font-size": 10 }, timeLabel(date)));
  }

  drawReferenceLine(svg, y(maturityMm), margin, width, `成熟参考 ${formatNumber(maturityMm)} mm`, "#3c9276");
  drawReferenceLine(svg, y(preovMm), margin, width, `排卵前参考 ${formatNumber(preovMm)} mm`, "#d78340");

  const points = 44;
  const horizon = Math.max(ovCenter, 1);
  const upperPoints = [];
  const lowerPoints = [];
  const centerPoints = [];
  for (let i = 0; i <= points; i += 1) {
    const hour = (horizon * i) / points;
    upperPoints.push([x(hour), y(diameter(hour, fastRate))]);
    lowerPoints.push([x(hour), y(diameter(hour, slowRate))]);
    centerPoints.push([x(hour), y(diameter(hour, centerRate))]);
  }
  const bandPoints = [...upperPoints, ...lowerPoints.reverse()].map(([px, py]) => `${px},${py}`).join(" ");
  svg.appendChild(svgElement("polygon", { points: bandPoints, fill: "rgba(30,118,94,.13)", stroke: "none" }));
  svg.appendChild(svgElement("polyline", { points: centerPoints.map(([px, py]) => `${px},${py}`).join(" "), fill: "none", stroke: "#1e765e", "stroke-width": 4, "stroke-linecap": "round", "stroke-linejoin": "round" }));

  if (previous) {
    const actualPoints = `${x(previousHours)},${y(previousMm)} ${x(0)},${y(currentMm)}`;
    svg.appendChild(svgElement("polyline", { points: actualPoints, fill: "none", stroke: "#17342d", "stroke-width": 3, "stroke-linecap": "round" }));
    drawPoint(svg, x(previousHours), y(previousMm), "#17342d", `上次实测 ${formatNumber(previousMm)} mm`);
  }
  drawPoint(svg, x(0), y(currentMm), "#17342d", `当前实测 ${formatNumber(currentMm)} mm`);

  if (maturityCenter >= 0 && maturityCenter <= xMax) {
    drawVerticalMarker(svg, x(maturityCenter), margin, height, "成熟中心", "#3c9276");
  }
  drawVerticalMarker(svg, x(ovCenter), margin, height, "排卵中心", "#d78340");

  const sliderPct = Number($("timelineSlider").value) / 100;
  const simulatedHour = ovCenter * sliderPct;
  const simulatedMm = diameter(simulatedHour, centerRate);
  drawSimulationMarker(svg, x(simulatedHour), y(simulatedMm), margin, height, `${formatNumber(simulatedMm)} mm`);
  $("timelineReadout").textContent = `${formatDateTime(new Date(currentTime.getTime() + simulatedHour * 3600000).toISOString())} · ${formatNumber(simulatedMm)} mm`;
}

function drawReferenceLine(svg, py, margin, width, label, color) {
  svg.appendChild(svgElement("line", { x1: margin.left, y1: py, x2: width - margin.right, y2: py, stroke: color, "stroke-width": 1.2, "stroke-dasharray": "6 5", opacity: .75 }));
  svg.appendChild(svgElement("text", { x: width - margin.right - 5, y: py - 7, "text-anchor": "end", fill: color, "font-size": 10, "font-weight": 700 }, label));
}

function drawPoint(svg, px, py, color, label) {
  const point = svgElement("circle", { cx: px, cy: py, r: 6, fill: "#fff", stroke: color, "stroke-width": 3 });
  point.appendChild(svgElement("title", {}, label));
  svg.appendChild(point);
}

function drawVerticalMarker(svg, px, margin, height, label, color) {
  svg.appendChild(svgElement("line", { x1: px, y1: margin.top + 25, x2: px, y2: height - margin.bottom, stroke: color, "stroke-width": 1.5, "stroke-dasharray": "4 5", opacity: .75 }));
  svg.appendChild(svgElement("text", { x: px + 6, y: margin.top + 35, fill: color, "font-size": 10, "font-weight": 800 }, label));
}

function drawSimulationMarker(svg, px, py, margin, height, label) {
  svg.appendChild(svgElement("line", { x1: px, y1: margin.top, x2: px, y2: height - margin.bottom, stroke: "#315f52", "stroke-width": 1, "stroke-dasharray": "3 4", opacity: .55 }));
  svg.appendChild(svgElement("circle", { cx: px, cy: py, r: 9, fill: "rgba(30,118,94,.15)" }));
  svg.appendChild(svgElement("circle", { cx: px, cy: py, r: 4.5, fill: "#1e765e", stroke: "#fff", "stroke-width": 2 }));
  const bubbleX = Math.min(890, Math.max(70, px));
  const bubbleY = Math.max(margin.top + 15, py - 22);
  svg.appendChild(svgElement("rect", { x: bubbleX - 30, y: bubbleY - 14, width: 60, height: 21, rx: 10, fill: "#17342d" }));
  svg.appendChild(svgElement("text", { x: bubbleX, y: bubbleY + 1, "text-anchor": "middle", fill: "#fff", "font-size": 10, "font-weight": 700 }, label));
}

function dateLabel(date) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit" }).format(date).replace("/", "-");
}

function timeLabel(date) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function toggleTimeline() {
  if (state.playTimer) {
    stopTimeline();
    return;
  }
  if (!state.result) return;
  if (Number($("timelineSlider").value) >= 100) $("timelineSlider").value = "0";
  $("playTimeline").textContent = "Ⅱ";
  state.playTimer = window.setInterval(() => {
    const next = Number($("timelineSlider").value) + 1;
    $("timelineSlider").value = String(Math.min(100, next));
    drawGrowthChart(state.result, state.payload);
    if (next >= 100) stopTimeline();
  }, 45);
}

function stopTimeline() {
  if (state.playTimer) window.clearInterval(state.playTimer);
  state.playTimer = null;
  $("playTimeline").textContent = "▶";
}

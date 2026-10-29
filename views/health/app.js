const bridge = window.AstrBotPluginView;
const $ = (id) => document.getElementById(id);
const LABELS = {
  heart_rate: ["心率", "bpm"],
  resting_heart_rate: ["静息心率", "bpm"],
  hrv: ["HRV", "ms"],
  respiratory_rate: ["呼吸频率", "次/分"],
  oxygen_saturation: ["血氧", "%"],
  wrist_temperature: ["手腕温度", "°C"],
  steps: ["步数", "步"],
  active_energy: ["活动能量", "kcal"],
  distance: ["距离", "km"],
  exercise_minutes: ["运动时长", "分钟"],
  stand_minutes: ["站立时长", "分钟"],
};
let overview = null;
let displayTimezone = "UTC";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (s) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[s]));
const secondsText = (v) => {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const m = Math.round(Number(v) / 60);
  if (m < 60) return `${m} 分钟`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
};
const clock = (v) => {
  if (!v) return "—";
  try { return new Date(v).toLocaleTimeString("zh-CN", { timeZone: displayTimezone, hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
};
const dateTime = (v) => {
  if (!v) return "—";
  try { return new Date(v).toLocaleString("zh-CN", { timeZone: displayTimezone, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
};

function latestRecord(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  return [...rows].sort((a, b) => (Number(b.updated_at || 0) - Number(a.updated_at || 0)) || String(b.end || b.start || "").localeCompare(String(a.end || a.start || "")))[0];
}

function renderOverview(data) {
  overview = data;
  displayTimezone = data.timezone || "UTC";
  const latest = data.latest;
  if (!latest) {
    $("empty").classList.remove("hidden");
    $("content").classList.add("hidden");
    $("freshness").textContent = "无数据";
    return;
  }
  $("empty").classList.add("hidden");
  $("content").classList.remove("hidden");
  $("sleepValue").textContent = secondsText(latest.sleep_seconds);
  $("sleepSub").textContent = latest.bedtime ? `${clock(latest.bedtime)} → ${clock(latest.wake_time)}` : "未记录入睡/起床";
  $("stepsValue").textContent = latest.steps == null ? "—" : `${Math.round(latest.steps).toLocaleString("zh-CN")} 步`;
  $("stepsSub").textContent = latest.date;
  $("metricValue").textContent = latest.metric_count ?? 0;
  $("metricSub").textContent = `${latest.workout_count ?? 0} 条训练`;
  $("updatedValue").textContent = latest.updated_at ? dateTime(new Date(Number(latest.updated_at) * 1000).toISOString()) : "—";
  $("updatedSub").textContent = `最近日期 ${latest.date}`;
  const days = (data.today && latest.date) ? Math.round((new Date(data.today) - new Date(latest.date)) / 86400000) : null;
  $("freshness").textContent = days == null ? "数据" : days <= 0 ? "今天有数据" : days === 1 ? "昨天有数据" : `${days} 天前`;
  $("freshness").className = "pill" + (days != null && days > 1 ? " warn" : " ok");
  const sel = $("dateSelect");
  sel.innerHTML = (data.data_dates || []).map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  drawTrend(data.history || []);
  $("historyList").innerHTML = (data.history || []).map((h) => `
    <button class="history-row" data-date="${esc(h.date)}">
      <span class="history-date">${esc(h.date)}</span>
      <span>${h.sleep_seconds == null ? "无睡眠" : secondsText(h.sleep_seconds)}</span>
      <span>${h.steps == null ? "无步数" : `${Math.round(h.steps).toLocaleString("zh-CN")} 步`}</span>
      <span class="muted">${h.metric_count || 0} 指标</span>
    </button>`).join("");
  document.querySelectorAll(".history-row").forEach((el) => el.addEventListener("click", () => loadDay(el.dataset.date)));
  if (data.latest_date) loadDay(data.latest_date);
}

function drawTrend(history) {
  const canvas = $("trend");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 700;
  const height = 180;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const rows = [...history].reverse().slice(-14);
  if (!rows.length) {
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted") || "#99a";
    ctx.font = "13px system-ui";
    ctx.fillText("暂无趋势", 18, 32);
    return;
  }
  const pad = { l: 34, r: 12, t: 16, b: 26 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const maxSleep = Math.max(8, ...rows.map((r) => (Number(r.sleep_seconds) || 0) / 3600));
  const maxSteps = Math.max(1000, ...rows.map((r) => Number(r.steps) || 0));
  ctx.strokeStyle = "rgba(150,170,210,.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 2; i++) {
    const y = pad.t + h * i / 2;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke();
  }
  const bw = w / rows.length;
  rows.forEach((r, i) => {
    const x = pad.l + i * bw;
    const sh = ((Number(r.sleep_seconds) || 0) / 3600) / maxSleep * h;
    ctx.fillStyle = "rgba(124,199,255,.62)";
    ctx.fillRect(x + bw * .12, pad.t + h - sh, bw * .34, sh);
    const st = ((Number(r.steps) || 0) / maxSteps) * h;
    ctx.fillStyle = "rgba(105,226,163,.58)";
    ctx.fillRect(x + bw * .52, pad.t + h - st, bw * .34, st);
  });
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted") || "#99a";
  ctx.font = "10px system-ui";
  rows.forEach((r, i) => {
    if (i % 2 === 0) ctx.fillText(String(r.date).slice(5), pad.l + i * bw, height - 8);
  });
}

function renderDay(payload) {
  const data = payload?.data ?? payload ?? {};
  const sleep = data.sleep || {};
  const date = data.date || payload.date;
  $("dayTitle").textContent = `${date} 的记录`;
  const stale = date !== overview?.latest_date;
  $("dayBadge").textContent = stale ? "历史日期" : "最新";
  $("dayBadge").className = "pill " + (stale ? "muted" : "ok");
  $("daySub").textContent = data.updated_at ? `最后更新 ${dateTime(new Date(Number(data.updated_at) * 1000).toISOString())}` : "—";
  const knownStages = [sleep.deep_seconds, sleep.rem_seconds, sleep.core_seconds];
  const knownTotal = knownStages.reduce((sum, value) => sum + (Number(value) || 0), 0);
  const agg = {
    asleep_deep: Number(sleep.deep_seconds) || 0,
    asleep_rem: Number(sleep.rem_seconds) || 0,
    asleep_core: Number(sleep.core_seconds) || 0,
    asleep: Math.max(0, (Number(sleep.asleep_seconds) || 0) - knownTotal),
    awake: Number(sleep.awake_seconds) || 0,
  };
  const total = Math.max(1, Object.values(agg).reduce((a, b) => a + b, 0));
  $("stageBar").innerHTML = ["asleep_deep", "asleep_rem", "asleep_core", "asleep", "awake"].map((k) => {
    if (!agg[k]) return "";
    const cls = k.replace("asleep_", "");
    return `<span class="stage ${cls}" style="width:${(agg[k] / total * 100).toFixed(2)}%"></span>`;
  }).join("");
  $("bedtime").textContent = clock(sleep.bedtime);
  $("wakeTime").textContent = clock(sleep.wake_time);
  $("inBed").textContent = secondsText(sleep.in_bed_seconds);
  $("awake").textContent = sleep.awake_seconds == null ? "—" : secondsText(sleep.awake_seconds) + (sleep.awake_count == null ? "" : ` / ${sleep.awake_count} 次`);
  $("samples").textContent = sleep.sample_count ?? "—";
  const metrics = Array.isArray(data.metrics) ? data.metrics : [];
  const grouped = {};
  metrics.forEach((m) => { (grouped[m.metric] ||= []).push(m); });
  $("metricsGrid").innerHTML = Object.entries(grouped).map(([key, rows]) => {
    const m = data.metric_summaries?.[key] ?? latestRecord(rows);
    const [label, defUnit] = LABELS[key] || [key, ""];
    if (!m || m.value == null) return "";
    const value = typeof m.value === "number" && !Number.isInteger(m.value) ? Number(m.value).toFixed(1) : m.value;
    const unit = m.unit === "count" ? "步" : m.unit === "min" ? "分钟" : m.unit === "breaths/min" ? "次/分" : (m.unit || defUnit);
    return `<article class="metric"><span>${esc(label)}</span><strong>${esc(value)} <small>${esc(unit)}</small></strong><small>${esc(clock(m.end || m.start))}${m.source ? " · " + esc(m.source) : ""}</small></article>`;
  }).join("") || `<p class="muted">这一天没有额外指标。</p>`;
  const workouts = Array.isArray(data.workouts) ? data.workouts : [];
  $("workouts").innerHTML = workouts.length
    ? `<h3>训练</h3>${workouts.slice(-5).map((w) => `<p>${esc(w.activity || "训练")} · ${esc(clock(w.start))}–${esc(clock(w.end))}${w.energy_kcal ? ` · ${esc(w.energy_kcal)} kcal` : ""}</p>`).join("")}`
    : "";
}

async function loadDay(date) {
  if (!date) return;
  try {
    const payload = await bridge.apiGet("day", { date });
    renderDay(payload);
    $("dateSelect").value = date;
  } catch (err) {
    $("daySub").textContent = err.message || String(err);
  }
}

async function loadOverview() {
  try {
    const data = await bridge.apiGet("overview");
    renderOverview(data);
  } catch (err) {
    $("freshness").textContent = "读取失败";
    $("freshness").className = "pill err";
    $("empty").classList.remove("hidden");
    $("empty").querySelector("p").textContent = err.message || String(err);
  }
}

await bridge.ready();
document.title = bridge.t("views.health.title", "Apple Health");
$("refresh").addEventListener("click", loadOverview);
$("dateSelect").addEventListener("change", (e) => loadDay(e.target.value));
window.addEventListener("resize", () => overview && drawTrend(overview.history || []));
await loadOverview();

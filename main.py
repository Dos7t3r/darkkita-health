from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_apple_health"
DEFAULT_ROOT = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
DATA_ROOT = Path(os.environ.get("HEALTH_DATA_ROOT", str(DEFAULT_ROOT)))
HISTORY_DIR = DATA_ROOT / "history"
LEGACY_FILE = DATA_ROOT / "health.json"
TZ = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))

METRIC_LABELS = {
    "heart_rate": ("心率", "bpm"),
    "resting_heart_rate": ("静息心率", "bpm"),
    "hrv": ("HRV", "ms"),
    "respiratory_rate": ("呼吸频率", "次/分"),
    "oxygen_saturation": ("血氧", "%"),
    "wrist_temperature": ("手腕温度", "°C"),
    "steps": ("步数", "步"),
    "active_energy": ("活动能量", "kcal"),
    "distance": ("距离", "km"),
    "exercise_minutes": ("运动时长", "分钟"),
    "stand_minutes": ("站立时长", "分钟"),
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, type(default)) else default
    except Exception:
        pass
    return default


def _dates() -> list[str]:
    out: list[str] = []
    try:
        for p in HISTORY_DIR.glob("*.json"):
            try:
                date.fromisoformat(p.stem)
                out.append(p.stem)
            except Exception:
                continue
    except Exception:
        pass
    return sorted(set(out), reverse=True)


def _load_date(date_str: str = "") -> dict[str, Any]:
    if date_str:
        try:
            date.fromisoformat(date_str)
        except Exception:
            return {}
        return _read_json(HISTORY_DIR / f"{date_str}.json", {})
    dates = _dates()
    if dates:
        return _read_json(HISTORY_DIR / f"{dates[0]}.json", {})
    # Backward-compatible legacy snapshot.
    legacy = _read_json(LEGACY_FILE, {})
    if legacy:
        return legacy
    return {}


def _seconds(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(round(float(value))))
    except Exception:
        return None


def _hm(value: Any, unit: str = "seconds") -> str:
    sec = _seconds(value)
    if sec is None:
        return "未记录"
    if unit == "minutes":
        sec *= 60
    if sec < 60:
        return f"{sec} 秒"
    minutes = sec // 60
    return f"{minutes // 60} 小时 {minutes % 60} 分" if minutes >= 60 else f"{minutes} 分钟"


def _local_time(value: Any) -> str:
    if not value:
        return "?"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%H:%M")
    except Exception:
        return "?"


def _local_iso(value: Any) -> str:
    if not value:
        return "?"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%m-%d %H:%M")
    except Exception:
        return "?"


def _legacy_to_data(legacy: dict[str, Any]) -> dict[str, Any]:
    sleep = legacy.get("sleep") if isinstance(legacy.get("sleep"), dict) else {}
    steps = legacy.get("steps") if isinstance(legacy.get("steps"), dict) else {}
    out = dict(legacy)
    if sleep:
        out["sleep"] = {
            **sleep,
            "asleep_seconds": sleep.get("asleep_seconds", (sleep.get("asleep_minutes") or 0) * 60),
            "deep_seconds": sleep.get("deep_seconds", (sleep.get("deep_minutes") or 0) * 60),
            "rem_seconds": sleep.get("rem_seconds", (sleep.get("rem_minutes") or 0) * 60),
            "core_seconds": sleep.get("core_seconds", (sleep.get("core_minutes") or 0) * 60),
            "awake_seconds": sleep.get("awake_seconds", (sleep.get("awake_minutes") * 60) if sleep.get("awake_minutes") is not None else None),
            "in_bed_seconds": sleep.get("in_bed_seconds", (sleep.get("in_bed_minutes") * 60) if sleep.get("in_bed_minutes") is not None else None),
        }
    if steps and not out.get("metrics"):
        d = legacy.get("date") or steps.get("date") or ""
        out["metrics"] = [
            {
                "metric": "steps",
                "value": steps.get("steps") or 0,
                "unit": "count",
                "start": f"{d}T00:00:00-04:00" if d else "",
                "end": f"{d}T23:59:59-04:00" if d else "",
            }
        ]
    return out


def _latest_metric(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [r for r in records if isinstance(r, dict)]
    if not rows:
        return None
    return max(
        rows,
        key=lambda r: (
            str(r.get("end") or r.get("start") or ""),
            int(r.get("updated_at") or 0),
        ),
    )


def _format_sleep(data: dict[str, Any], data_date: str, today: date) -> list[str]:
    sleep = data.get("sleep") if isinstance(data.get("sleep"), dict) else {}
    if not sleep:
        return []
    try:
        d = date.fromisoformat(data.get("date") or data_date)
    except Exception:
        d = None
    age = (today - d).days if d else 99
    if age == 0:
        prefix = "昨晚"
    elif age < 0:
        prefix = f"未来日期 {data_date}（请检查时间设置）"
    elif age == 1:
        prefix = "前一晚"
    else:
        prefix = f"{data.get('date') or data_date} 那晚（不是昨晚）"
    asleep = _seconds(sleep.get("asleep_seconds", sleep.get("asleep_minutes")))
    if asleep is None:
        return []
    lines = [f"{prefix}睡眠：{_hm(asleep)}"]
    deep = _seconds(sleep.get("deep_seconds"))
    rem = _seconds(sleep.get("rem_seconds"))
    core = _seconds(sleep.get("core_seconds"))
    stages = []
    if deep is not None:
        stages.append(f"深睡 {_hm(deep)}")
    if rem is not None:
        stages.append(f"REM {_hm(rem)}")
    if core is not None:
        stages.append(f"核心 {_hm(core)}")
    if stages:
        lines.append("分期：" + "，".join(stages))
    awake_sec = _seconds(sleep.get("awake_seconds"))
    awake_count = sleep.get("awake_count")
    if awake_sec is not None:
        text = f"夜醒 {_hm(awake_sec)}"
        if isinstance(awake_count, int) and awake_count >= 0:
            text += f"，{awake_count} 次"
        lines.append(text)
    in_bed = _seconds(sleep.get("in_bed_seconds"))
    if in_bed is not None:
        lines.append(f"卧床 {_hm(in_bed)}")
    if sleep.get("bedtime") or sleep.get("wake_time"):
        lines.append(f"入睡 {_local_time(sleep.get('bedtime'))}，起床 {_local_time(sleep.get('wake_time'))}")
    return lines


CUMULATIVE = {"steps", "active_energy", "distance", "exercise_minutes", "stand_minutes"}

def _metric_summary(rows):
    if not rows:
        return None
    if rows[0].get("metric") not in CUMULATIVE:
        return _latest_metric(rows)
    # Source streams are alternatives, never add iPhone and Watch together.
    sources = {}
    for row in rows:
        sources.setdefault(str(row.get("source") or ""), []).append(row)
    def source_value(records):
        totals = []
        for row in records:
            try:
                start = datetime.fromisoformat(row["start"]).astimezone(TZ)
                end = datetime.fromisoformat(row["end"]).astimezone(TZ)
                if start.hour == start.minute == start.second == 0 and end.hour == 23 and end.minute == 59 and end.second >= 59:
                    totals.append(row)
            except (KeyError, ValueError):
                pass
        if totals:
            return _latest_metric(totals)
        latest = _latest_metric(records)
        intervals = sorted((r.get("start", ""), r.get("end", "")) for r in records)
        last_end = ""
        for start, end in intervals:
            if start < last_end and end > start:
                return {**latest, "value": None, "aggregation": "overlap"}
            last_end = max(last_end, end)
        return {**latest, "value": sum(float(r["value"]) for r in records), "aggregation": "source_sum"}
    choices = [source_value(records) for records in sources.values()]
    # Without HealthKit source priority metadata, choose one stream conservatively.
    # This avoids double counting; it is not Apple's merged Health total.
    valid = [r for r in choices if r.get("value") is not None]
    result = max(valid, key=lambda r: float(r["value"])) if valid else choices[0]
    return {**result, "source_policy": "single_source_max", "source_count": len(sources)}

def _load_section(section, date_str=""):
    if date_str or section in {"history", "all"}:
        return _load_date(date_str)
    for d in _dates():
        data = _load_date(d)
        if section == "sleep" and data.get("sleep"):
            return data
        if section == "workouts" and data.get("workouts"):
            return data
        if section in {"metrics", "steps"} and any(
            section == "metrics" or r.get("metric") == "steps" for r in data.get("metrics", [])):
            return data
    return _load_date()


def _format_metrics(data: dict[str, Any], section: str) -> list[str]:
    records = data.get("metrics") if isinstance(data.get("metrics"), list) else []
    if not records:
        return []
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if isinstance(r, dict) and r.get("metric"):
            by_metric.setdefault(str(r["metric"]), []).append(r)
    lines: list[str] = []
    unit_display = {
        "count": "步",
        "min": "分钟",
        "minutes": "分钟",
        "breaths/min": "次/分",
        "rpm": "次/分",
        "percent": "%",
    }
    for metric, rows in by_metric.items():
        if section == "steps" and metric != "steps":
            continue
        latest = _metric_summary(rows)
        if not latest:
            continue
        label, default_unit = METRIC_LABELS.get(metric, (metric, ""))
        value = latest.get("value")
        if value is None:
            lines.append(f"{label}：存在重叠样本，暂不能可靠汇总")
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, float):
            value = round(value, 1)
        unit = latest.get("unit") or default_unit
        unit = unit_display.get(str(unit), unit)
        when = _local_time(latest.get("end") or latest.get("start"))
        note = "；单来源累计，非健康 App 合并总量" if metric in CUMULATIVE and latest.get("aggregation") else ""
        extra = f"（{when}" + (f" · {latest.get('source')}" if latest.get("source") else "") + "）"
        lines.append(f"{label}：{value} {unit}{extra}{note}")
    return lines


def _format_workouts(data: dict[str, Any]) -> list[str]:
    rows = data.get("workouts") if isinstance(data.get("workouts"), list) else []
    out = []
    for w in rows[-3:]:
        if not isinstance(w, dict):
            continue
        activity = w.get("activity") or "训练"
        dur = w.get("duration_seconds")
        minutes = round(float(dur) / 60) if dur is not None else None
        extra = f"，{minutes} 分钟" if minutes is not None else ""
        out.append(f"{activity} {_local_iso(w.get('start'))}{extra}")
    return out


def format_health(data: dict[str, Any], section: str = "all", date_str: str = "", history_days: int = 0) -> str:
    data = _legacy_to_data(data or {})
    data_date = str(data.get("date") or date_str or "")
    today = datetime.now(TZ).date()
    if section == "history" or history_days > 0:
        dates = _dates()
        if not dates:
            return "还没有收到 Apple Health 历史数据。"
        lines = [f"最近 {min(max(history_days or 7, 1), 30)} 天健康记录："]
        for d in dates[: min(max(history_days or 7, 1), 30)]:
            item = _load_date(d)
            sleep = item.get("sleep") if isinstance(item.get("sleep"), dict) else {}
            metrics = item.get("metrics") if isinstance(item.get("metrics"), list) else []
            steps = _metric_summary([m for m in metrics if isinstance(m, dict) and m.get("metric") == "steps"])
            asleep = _seconds(sleep.get("asleep_seconds")) if sleep else None
            parts = []
            if asleep is not None:
                parts.append(f"睡眠 {_hm(asleep)}")
            if steps:
                parts.append(f"步数 {steps.get('value')}" if steps.get("value") is not None else "步数存在重叠，未汇总")
            lines.append(f"- {d}" + ("：" + "，".join(parts) if parts else "：无摘要"))
        return "\n".join(lines)
    if not data:
        return "还没有收到 Apple Health 数据。请先让 iPhone 快捷指令上传一次。"
    try:
        d = date.fromisoformat(data_date)
    except Exception:
        d = None
    age = (today - d).days if d else 99
    lines: list[str] = []
    if data_date:
        freshness = (f"未来日期 {data_date}" if age < 0 else "今天") if age <= 0 else ("昨天" if age == 1 else f"数据日期 {data_date}")
        lines.append(f"健康数据：{freshness}")
    if section in ("all", "sleep"):
        sleep_data = data
        if section == "all" and not date_str and not data.get("sleep"):
            sleep_data = _load_section("sleep")
        sleep_lines = _format_sleep(sleep_data, str(sleep_data.get("date") or data_date), today)
        lines.extend(sleep_lines)
    if section in ("all", "steps", "metrics"):
        metrics_lines = _format_metrics(data, section)
        lines.extend(metrics_lines)
    if section in ("all", "workouts"):
        workouts = _format_workouts(data)
        if workouts:
            lines.append("训练：" + "；".join(workouts))
    if not lines:
        return "这个日期没有可用的健康记录。"
    return "\n".join(lines)


def _metric_value(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    records = data.get("metrics") if isinstance(data.get("metrics"), list) else []
    return _metric_summary([r for r in records if isinstance(r, dict) and r.get("metric") == name])


def _summary_for(date_str: str) -> dict[str, Any]:
    data = _load_date(date_str)
    data = _legacy_to_data(data or {})
    sleep = data.get("sleep") if isinstance(data.get("sleep"), dict) else {}
    steps = _metric_value(data, "steps")
    metrics = data.get("metrics") if isinstance(data.get("metrics"), list) else []
    workouts = data.get("workouts") if isinstance(data.get("workouts"), list) else []
    return {
        "date": date_str,
        "updated_at": data.get("updated_at") or 0,
        "sleep_seconds": sleep.get("asleep_seconds"),
        "deep_seconds": sleep.get("deep_seconds"),
        "rem_seconds": sleep.get("rem_seconds"),
        "core_seconds": sleep.get("core_seconds"),
        "awake_seconds": sleep.get("awake_seconds"),
        "awake_count": sleep.get("awake_count"),
        "bedtime": sleep.get("bedtime") or "",
        "wake_time": sleep.get("wake_time") or "",
        "steps": steps.get("value") if steps else None,
        "metric_count": len(metrics),
        "workout_count": len(workouts),
    }


def _history_summaries(days: int = 30) -> list[dict[str, Any]]:
    return [_summary_for(d) for d in _dates()[: max(1, min(int(days or 30), 60))]]


@dataclass
class HealthTool(FunctionTool[AstrAgentContext]):
    __pydantic_config__ = {"arbitrary_types_allowed": True}

    plugin: Any = None
    name: str = "get_health_summary"
    description: str = (
        "Read the user's latest Apple Health summary from their iPhone. Supports sleep "
        "(stages, awake time/count), steps, heart rate, resting heart rate, HRV, "
        "respiratory rate, oxygen saturation, wrist temperature, energy, distance, "
        "exercise/stand minutes, workouts, and historical dates. Use only recorded data; "
        "never invent missing metrics or give medical diagnoses."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["all", "sleep", "steps", "metrics", "workouts", "history"],
                    "default": "all",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD; empty means latest.",
                    "default": "",
                },
                "history_days": {
                    "type": "integer",
                    "description": "If >0, list recent history summaries.",
                    "default": 0,
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        section: str = "all",
        date: str = "",
        history_days: int = 0,
    ) -> ToolExecResult:
        return await self.plugin.health_tool(section, date, history_days)


@register(
    PLUGIN_NAME,
    "Dos7t3r",
    "Apple Health：睡眠、步数与多指标历史",
    "1.0.0",
)
class AppleHealthPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.context.add_llm_tools(HealthTool(plugin=self))
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/overview", self.page_overview, ["GET"], "Apple Health 总览"
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/day", self.page_day, ["GET"], "Apple Health 某天数据"
        )
        logger.info("apple_health: tool + view ready, data=%s", DATA_ROOT)
        self._wake_worker_task = None

    async def initialize(self):
        self._wake_worker_task = asyncio.create_task(self._wake_worker())

    async def terminate(self):
        task = getattr(self, "_wake_worker_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _wake_settings(self):
        legacy = _read_json(DATA_ROOT / "wake_settings.json", {})
        return {
            "enabled": bool(self.config.get("wake_enabled", legacy.get("enabled", False))),
            "session": self.config.get("wake_session", legacy.get("session", "")),
            "replaces_job_id": self.config.get("wake_replaces_job_id", legacy.get("replaces_job_id", "")),
        }

    async def _wake_worker(self):
        # Initial delay allows the platform, tools, and cron manager to finish loading.
        await asyncio.sleep(20)
        while True:
            try:
                await self._process_wake_events()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("apple_health: wake worker failed: %s", type(exc).__name__)
            await asyncio.sleep(15)

    async def _process_wake_events(self):
        settings = self._wake_settings()
        if not settings.get("enabled"):
            return
        session = settings.get("session", "")
        if not session:
            return
        manager = self.context.cron_manager
        jobs = await manager.list_jobs()
        # Disable only an optional, explicitly configured replacement job via the live scheduler API.
        for job in jobs:
            if job.job_id == settings.get("replaces_job_id") and job.enabled:
                await manager.update_job(job.job_id, enabled=False)
                logger.info("apple_health: fixed-time morning job disabled; waiting for explicit wake events")
        now = datetime.now(TZ)
        for path in sorted((DATA_ROOT / "wake_events").glob("????-??-??.json")):
            event = _read_json(path, {})
            if event.get("status") not in {"pending", "staged"}:
                continue
            occurred = datetime.fromisoformat(event["occurred_at"])
            if (now - occurred).total_seconds() > 7200:
                event["status"] = "expired"
                self._save_wake_event(path, event)
                continue
            name = f"Darkkita起床问候:{event['date']}"
            existing = next((j for j in jobs if j.name == name), None)
            if event.get("status") == "staged" and event.get("job_id") and existing is None:
                # It may have completed and been removed before a restart. Never send a second greeting.
                event["status"] = "consumed"
                self._save_wake_event(path, event)
                continue
            if existing:
                job = existing
            else:
                due = max(datetime.fromisoformat(event["due_at"]), now + timedelta(seconds=5))
                note = self._wake_prompt(event)
                job = await manager.add_active_job(name=name, cron_expression=None,
                    timezone=str(TZ), run_once=True, persistent=True, run_at=due,
                    payload={"session": session, "note": note, "origin": "api", "wake_date": event["date"]},
                    description=note, enabled=False)
                jobs.append(job)
            # Persist the claim before enabling: a crash cannot duplicate an already delivered job.
            event["status"] = "staged"
            event["job_id"] = job.job_id
            self._save_wake_event(path, event)
            if not job.enabled:
                await manager.update_job(job.job_id, enabled=True)
            event["status"] = "scheduled"
            event["job_id"] = job.job_id
            self._save_wake_event(path, event)
            logger.info("apple_health: wake greeting queued for %s", event["date"])

    @staticmethod
    def _save_wake_event(path, event):
        tmp = path.with_suffix(".plugin.tmp")
        tmp.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    @staticmethod
    def _wake_prompt(event):
        day = event["date"]
        data = _load_date(day)
        summary = format_health(data, "all", day)[:2200] if data else "本次未找到当天健康摘要，忽略健康判断。"
        holiday = ("用户当前启用了假期/Reading Week模式，不按周几推测上课，不催早课或出门；"
                   "除非有用户明确确认的安排。") if event.get("holiday_mode") else (
                   "只有实际日历确认的安排才能提醒，不按工作日推测上课。如有可用的日历或天气工具，可按需要查询；没有工具就略过。")
        return (f"这是用户授权的起床后主动问候。iPhone 在 {event['occurred_at']} 通过起床快捷指令报告起床，"
                f"且健康上传已完成。现在以当前实际时间问候，不再叫醒用户，也不要叫他继续睡。{holiday}"
                "遵循当前会话的人格与语言偏好，主动发1–3句，自然聊天，不像通知；"
                "健康只在有帮助时自然关心一句，不罗列数值，不诊断，"
                "腕温是绝对腕温，不是体温或相对基线变化；数据不足就忽略，不凭单次血氧异常吓人。"
                "只发送一条问候到指定原会话，不另约定再次唤醒，不调用图片或视频工具。"
                f"以下是 {day} 上传的紧凑摘要（当天活动累计并非昨日全天，不要混淆）：\n{summary}")

    async def page_overview(self):
        try:
            dates = _dates()
            latest = _summary_for(dates[0]) if dates else None
            return json_response(
                {
                    "ok": True,
                    "today": datetime.now(TZ).date().isoformat(),
                    "timezone": str(TZ),
                    "latest_date": dates[0] if dates else "",
                    "data_dates": dates[:60],
                    "latest": latest,
                    "history": _history_summaries(30),
                    "storage": {
                        "history_files": len(dates),
                        "legacy": LEGACY_FILE.exists(),
                    },
                }
            )
        except Exception as exc:
            return error_response(str(exc), status_code=500)

    async def page_day(self):
        try:
            date_str = str(request.query.get("date", "") or "").strip()
            if not date_str:
                dates = _dates()
                date_str = dates[0] if dates else ""
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
                return error_response("date must be YYYY-MM-DD", status_code=400)
            data = _load_date(date_str)
            if not data:
                return error_response("no health data for this date", status_code=404)
            names = {r.get("metric") for r in data.get("metrics", []) if isinstance(r, dict)}
            data = {**data, "metric_summaries": {name: _metric_value(data, name) for name in names if name}}
            return json_response({"ok": True, "date": date_str, "timezone": str(TZ), "data": data})
        except Exception as exc:
            return error_response(str(exc), status_code=500)

    async def health_tool(self, section: str = "all", date_str: str = "", history_days: int = 0) -> ToolExecResult:
        section = section if section in {"all", "sleep", "steps", "metrics", "workouts", "history"} else "all"
        data = _load_section(section, date_str)
        if date_str and not data:
            return f"没有 {date_str} 的健康记录。"
        result = format_health(data, section, date_str, int(history_days or 0))
        today = datetime.now(TZ).date().isoformat()
        wake = _read_json(DATA_ROOT / "wake_events" / f"{today}.json", {})
        if wake and (not date_str or date_str == today):
            result += "\n手机明确报告起床：" + str(wake.get("occurred_at", ""))
            if wake.get("holiday_mode"):
                result += "；用户启用假期/Reading Week模式，不按星期推测上课。"
        return result

    @filter.command("健康")
    async def cmd_health(self, event: AstrMessageEvent):
        text = (event.message_str or "").replace("/健康", "").strip()
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        history_match = re.search(r"历史|最近?(\d+)天", text)
        if history_match:
            days = int(history_match.group(1)) if history_match.group(1) else 7
            yield event.plain_result(format_health({}, "history", "", days))
            return
        date_str = date_match.group(0) if date_match else ""
        section = "sleep" if "睡" in text else "steps" if "步" in text else "all"
        yield event.plain_result(format_health(_load_section(section, date_str), section, date_str))

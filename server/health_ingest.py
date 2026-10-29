#!/usr/bin/env python3
"""Apple Health ingest for Darkkita.

Backward compatible:
  POST /health/ingest, Authorization: Bearer <same token>
  {"type":"sleep_samples","date":"YYYY-MM-DD","samples":[{"value":"Asleep Core","start":...,"end":...}]}
  {"type":"steps","date":"YYYY-MM-DD","steps":1234}

New:
  {"type":"metrics","date":"YYYY-MM-DD","metrics":[{...}],"workouts":[{...}]}
"""
from __future__ import annotations

import json
import math
import os
import secrets
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))
UTC = timezone.utc
BASE = Path(os.environ.get("HEALTH_INGEST_BASE", "/opt/health-ingest"))
TOKEN_FILE = Path(os.environ.get("HEALTH_INGEST_TOKEN", str(BASE / "token")))
DATA_ROOT = Path(
    os.environ.get(
        "HEALTH_DATA_ROOT",
        "/opt/astrbot/data/plugin_data/astrbot_plugin_apple_health",
    )
)
LEGACY_FILE = DATA_ROOT / "health.json"
HISTORY_DIR = DATA_ROOT / "history"
RAW_DIR = DATA_ROOT / "raw"
LOCK = threading.RLock()

MAX_BODY = 3_000_000
MAX_SLEEP_SAMPLES = 5000
MAX_METRICS = 2000
MAX_WORKOUTS = 300


class ValidationError(ValueError):
    pass


def read_token() -> str:
    BASE.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, type(default)) else default
    except Exception:
        pass
    return default


def parse_dt(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be an ISO-8601 date-time")
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except Exception as exc:
        raise ValidationError(f"{field} is not a valid ISO-8601 date-time") from exc
    if dt.tzinfo is None:
        # Shortcuts sometimes emits a local wall-clock time without an offset.
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(UTC)


def parse_date(value: Any, field: str = "date") -> date:
    if value is None or value == "":
        raise ValidationError(f"{field} is required (YYYY-MM-DD)")
    try:
        return date.fromisoformat(str(value))
    except Exception as exc:
        raise ValidationError(f"{field} must be YYYY-MM-DD") from exc


def local_date(dt: datetime) -> date:
    return dt.astimezone(TZ).date()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _num(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} must be a number")
    try:
        n = float(value)
    except Exception as exc:
        raise ValidationError(f"{field} must be a number") from exc
    if not math.isfinite(n):
        raise ValidationError(f"{field} must be finite")
    return n


def _int(value: Any, field: str) -> int:
    n = _num(value, field)
    if n < 0 or abs(n - round(n)) > 1e-9:
        raise ValidationError(f"{field} must be a non-negative integer")
    return int(round(n))


# ---------- sleep ----------
SLEEP_NUMERIC = {
    0: "in_bed",
    1: "asleep",
    2: "awake",
    3: "asleep_core",
    4: "asleep_deep",
    5: "asleep_rem",
    6: "asleep",
}


def sleep_stage(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValidationError("sleep sample value is required")
    if isinstance(value, (int, float)):
        key = int(value) if math.isfinite(value) and float(value).is_integer() else -1
        if key in SLEEP_NUMERIC:
            return SLEEP_NUMERIC[key]
        raise ValidationError(f"unknown numeric sleep stage enum: {value}")
    raw = str(value).strip()
    if raw == "":
        raise ValidationError("sleep sample value is empty")
    # Accept numeric strings from Shortcuts.
    try:
        if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
            key = int(raw)
            if key in SLEEP_NUMERIC:
                return SLEEP_NUMERIC[key]
            raise ValidationError(f"unknown numeric sleep stage enum: {raw}")
    except ValidationError:
        raise
    except Exception:
        pass
    normalized = (
        raw.lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("hkcategoryvaluesleepanalysis", "")
    )
    if "deep" in normalized or "深睡" in raw or "深度" in raw:
        return "asleep_deep"
    if "rem" in normalized or "快速眼动" in raw or "异相" in raw:
        return "asleep_rem"
    if "core" in normalized or "核心" in raw or "浅睡" in raw:
        return "asleep_core"
    if "inbed" in normalized or "卧床" in raw or "in bed" in raw.lower():
        return "in_bed"
    if "awake" in normalized or "清醒" in raw or "醒来" in raw:
        return "awake"
    if "asleep" in normalized or "sleeping" in normalized or raw in ("睡眠", "睡着"):
        return "asleep"
    raise ValidationError(f"unknown sleep stage value: {raw[:80]}")


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    clean = sorted((a, b) for a, b in intervals if b > a)
    merged: list[list[int]] = []
    for start, end in clean:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(a, b) for a, b in merged]


def _seconds(intervals: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in _merge_intervals(intervals))


def summarize_sleep(segments: list[dict[str, Any]], date_str: str) -> dict[str, Any]:
    by_stage: dict[str, list[tuple[int, int]]] = {}
    for seg in segments:
        stage = str(seg.get("stage") or "")
        start = parse_dt(seg.get("start"), "start")
        end = parse_dt(seg.get("end"), "end")
        by_stage.setdefault(stage, []).append((int(start.timestamp()), int(end.timestamp())))
    # In-bed is a separate observation; resolve competing sleep stages on a timeline.
    priority = {"asleep": 0, "asleep_core": 1, "asleep_rem": 2, "asleep_deep": 3, "awake": 4}
    events = {}
    for stage, intervals in by_stage.items():
        if stage not in priority:
            continue
        for a, b in intervals:
            events.setdefault(a, []).append((stage, 1))
            events.setdefault(b, []).append((stage, -1))
    active = {}
    resolved = {stage: [] for stage in priority}
    previous = None
    for point in sorted(events):
        if previous is not None and point > previous:
            candidates = [stage for stage, count in active.items() if count > 0]
            if candidates:
                stage = max(candidates, key=priority.get)
                resolved[stage].append((previous, point))
        for stage, delta in events[point]:
            active[stage] = active.get(stage, 0) + delta
        previous = point
    asleep_intervals = [interval for stage, intervals in resolved.items() if stage != "awake" for interval in intervals]
    asleep_sec = _seconds(asleep_intervals)
    deep_sec = _seconds(resolved["asleep_deep"]) if by_stage.get("asleep_deep") else None
    rem_sec = _seconds(resolved["asleep_rem"]) if by_stage.get("asleep_rem") else None
    core_sec = _seconds(resolved["asleep_core"]) if by_stage.get("asleep_core") else None
    in_bed_list = by_stage.get("in_bed", [])
    awake_list = resolved["awake"]
    in_bed_sec = _seconds(in_bed_list) if in_bed_list else None
    awake_sec = _seconds(awake_list) if by_stage.get("awake") else None
    awake_count = len(_merge_intervals(awake_list)) if by_stage.get("awake") else None
    sleep_starts = [datetime.fromtimestamp(a, UTC) for a, b in asleep_intervals]
    sleep_ends = [datetime.fromtimestamp(b, UTC) for a, b in asleep_intervals]
    sources = sorted({str(s.get("source")).strip() for s in segments if s.get("source")})
    return {
        "date": date_str,
        "segments": sorted(
            segments,
            key=lambda s: s.get("start") or "",
        ),
        "asleep_seconds": asleep_sec,
        "deep_seconds": deep_sec,
        "rem_seconds": rem_sec,
        "core_seconds": core_sec,
        "in_bed_seconds": in_bed_sec,
        "awake_seconds": awake_sec,
        "awake_count": awake_count,
        "bedtime": iso(min(sleep_starts)) if sleep_starts else "",
        "wake_time": iso(max(sleep_ends)) if sleep_ends else "",
        "sources": sources,
        "sample_count": len(segments),
        "updated_at": int(time.time()),
    }


def _history_path(date_str: str) -> Path:
    return HISTORY_DIR / f"{date_str}.json"


def _load_history(date_str: str) -> dict[str, Any]:
    return read_json(_history_path(date_str), {})


def _save_history(date_str: str, history: dict[str, Any]) -> None:
    history["date"] = date_str
    history["updated_at"] = int(time.time())
    atomic_write(_history_path(date_str), history)
    _write_legacy(date_str, history)


def _write_legacy(date_str: str, history: dict[str, Any]) -> None:
    """Keep the old latest-snapshot file for older clients/tools."""
    current = read_json(LEGACY_FILE, {})
    if str(current.get("date") or "") > date_str:
        return
    sleep = history.get("sleep") if isinstance(history.get("sleep"), dict) else {}
    metrics = history.get("metrics") if isinstance(history.get("metrics"), list) else []
    step_sources = {}
    for metric in metrics:
        if metric.get("metric") == "steps":
            step_sources.setdefault(str(metric.get("source") or ""), []).append(metric)
    step_values = []
    for rows in step_sources.values():
        totals = [r for r in rows if parse_dt(r["start"], "start").astimezone(TZ).time() == dtime.min
                  and parse_dt(r["end"], "end").astimezone(TZ).time() >= dtime(23, 59, 59)]
        if totals:
            step_values.append(max(totals, key=lambda r: int(r.get("updated_at") or 0))["value"])
            continue
        ordered = sorted(rows, key=lambda r: r["start"])
        previous_end = ""
        valid = True
        for row in ordered:
            if row["start"] < previous_end and row["end"] > row["start"]:
                valid = False
                break
            previous_end = max(previous_end, row["end"])
        if valid:
            step_values.append(sum(float(r["value"]) for r in rows))
    steps = {"value": max(step_values)} if step_values else None
    legacy = {
        "date": date_str,
        "sleep": {
            "date": sleep.get("date") or date_str,
            "asleep_minutes": round((sleep.get("asleep_seconds") or 0) / 60),
            "deep_minutes": round((sleep.get("deep_seconds") or 0) / 60),
            "rem_minutes": round((sleep.get("rem_seconds") or 0) / 60),
            "core_minutes": round((sleep.get("core_seconds") or 0) / 60),
            "awake_minutes": round((sleep.get("awake_seconds") or 0) / 60) if sleep.get("awake_seconds") is not None else None,
            "in_bed_minutes": round((sleep.get("in_bed_seconds") or 0) / 60) if sleep.get("in_bed_seconds") is not None else None,
            "bedtime": sleep.get("bedtime") or "",
            "wake_time": sleep.get("wake_time") or "",
            "sample_count": sleep.get("sample_count") or 0,
        }
        if sleep
        else {},
        "steps": {"date": date_str, "steps": int(float(steps.get("value") or 0))} if steps else {},
        "updated_at": int(time.time()),
    }
    atomic_write(LEGACY_FILE, legacy)


# ---------- metrics ----------
METRIC_ALIASES = {
    "heart_rate": "heart_rate",
    "heartrate": "heart_rate",
    "心率": "heart_rate",
    "resting_heart_rate": "resting_heart_rate",
    "restingheartrate": "resting_heart_rate",
    "静息心率": "resting_heart_rate",
    "hrv": "hrv",
    "heart_rate_variability": "hrv",
    "心率变异性": "hrv",
    "respiratory_rate": "respiratory_rate",
    "respiratoryrate": "respiratory_rate",
    "呼吸频率": "respiratory_rate",
    "oxygen_saturation": "oxygen_saturation",
    "oxygensaturation": "oxygen_saturation",
    "spo2": "oxygen_saturation",
    "血氧": "oxygen_saturation",
    "wrist_temperature": "wrist_temperature",
    "wristtemperature": "wrist_temperature",
    "手腕温度": "wrist_temperature",
    "steps": "steps",
    "步数": "steps",
    "active_energy": "active_energy",
    "activeenergy": "active_energy",
    "活动能量": "active_energy",
    "distance": "distance",
    "距离": "distance",
    "exercise_minutes": "exercise_minutes",
    "exerciseminutes": "exercise_minutes",
    "运动时长": "exercise_minutes",
    "stand_minutes": "stand_minutes",
    "standminutes": "stand_minutes",
    "站立时长": "stand_minutes",
}

METRIC_SPECS = {
    "heart_rate": ({"bpm", "count/min", "次/分"}, "bpm", False),
    "resting_heart_rate": ({"bpm", "count/min", "次/分"}, "bpm", False),
    "hrv": ({"ms", "毫秒"}, "ms", False),
    "respiratory_rate": ({"breaths/min", "次/分", "rpm"}, "breaths/min", False),
    "oxygen_saturation": ({"%", "percent", "百分比"}, "%", False),
    "wrist_temperature": ({"°c", "c", "degc", "摄氏度"}, "°C", False),
    "steps": ({"count", "steps", "步"}, "count", True),
    "active_energy": ({"kcal", "千卡"}, "kcal", False),
    "distance": ({"km", "mi", "公里", "英里"}, "km", False),
    "exercise_minutes": ({"min", "minutes", "分钟"}, "min", False),
    "stand_minutes": ({"min", "minutes", "分钟"}, "min", False),
}


def normalize_metric_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("metric name is required")
    raw = value.strip()
    key = raw.lower().replace(" ", "_").replace("-", "_")
    if key in METRIC_ALIASES:
        return METRIC_ALIASES[key]
    if raw in METRIC_ALIASES:
        return METRIC_ALIASES[raw]
    raise ValidationError(f"unknown metric: {raw[:80]}")


def metric_unit(metric: str, unit: Any) -> str:
    allowed, default, _ = METRIC_SPECS[metric]
    if unit is None or str(unit).strip() == "":
        return default
    raw = str(unit).strip()
    if raw.lower() in allowed:
        return raw
    raise ValidationError(f"invalid unit for {metric}: {raw[:40]}")


def normalize_metric(item: Any, date_str: str, source_default: str = "") -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValidationError("each metric must be an object")
    metric = normalize_metric_name(item.get("metric"))
    value = _num(item.get("value"), f"{metric}.value")
    unit = metric_unit(metric, item.get("unit"))
    if metric in {"heart_rate", "resting_heart_rate", "hrv", "respiratory_rate", "active_energy", "distance"} and value < 0:
        raise ValidationError(f"{metric} must be non-negative")
    if metric == "distance":
        if unit.lower() in {"mi", "英里"}:
            value *= 1.609344
        unit = "km"
    else:
        unit = METRIC_SPECS[metric][1]
    source = str(item.get("source") or source_default).strip()[:120]
    start_raw = item.get("start")
    end_raw = item.get("end")
    if start_raw is None or start_raw == "":
        start = datetime.combine(date.fromisoformat(date_str), dtime.min, tzinfo=TZ).astimezone(UTC)
    else:
        start = parse_dt(start_raw, f"{metric}.start")
    if end_raw is None or end_raw == "":
        end = datetime.combine(date.fromisoformat(date_str), dtime(23, 59, 59), tzinfo=TZ).astimezone(UTC)
    else:
        end = parse_dt(end_raw, f"{metric}.end")
    if local_date(start).isoformat() != date_str or local_date(end).isoformat() != date_str:
        raise ValidationError(f"{metric} timestamps must fall on date in the configured timezone; split cross-day samples")
    if end < start:
        raise ValidationError(f"{metric}.end must not be before start")
    if metric == "steps":
        value = _int(value, "steps.value")
    if metric == "oxygen_saturation" and not (0 <= value <= 100):
        raise ValidationError("oxygen_saturation must be between 0 and 100")
    if metric in {"exercise_minutes", "stand_minutes"} and value < 0:
        raise ValidationError(f"{metric} must be non-negative")
    return {
        "metric": metric,
        "value": value,
        "unit": unit,
        "start": iso(start),
        "end": iso(end),
        "source": source,
        "date": date_str,
    }


def _metric_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("metric")),
        str(item.get("source") or ""),
        str(item.get("start")),
        str(item.get("end")),
    )


# ---------- workouts ----------
def normalize_workout(item: Any, date_str: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValidationError("each workout must be an object")
    activity = str(item.get("activity") or item.get("type") or "workout").strip()[:80]
    start = parse_dt(item.get("start"), "workout.start")
    end = parse_dt(item.get("end"), "workout.end")
    if end <= start:
        raise ValidationError("workout.end must be after start")
    if local_date(start).isoformat() != date_str:
        raise ValidationError("workout start must fall on date in the configured timezone")
    duration = int((end - start).total_seconds())
    source = str(item.get("source") or "").strip()[:120]
    out: dict[str, Any] = {
        "activity": activity,
        "start": iso(start),
        "end": iso(end),
        "duration_seconds": duration,
        "date": date_str,
        "source": source,
    }
    for field in ("energy_kcal", "distance_km"):
        if item.get(field) is not None:
            n = _num(item.get(field), field)
            if n < 0:
                raise ValidationError(f"{field} must be non-negative")
            out[field] = n
    return out


# ---------- ingest ----------
def merge_sleep(payload: dict[str, Any]) -> dict[str, Any]:
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValidationError("sleep_samples samples must be a non-empty list")
    if len(raw_samples) > MAX_SLEEP_SAMPLES:
        raise ValidationError(f"too many sleep samples (max {MAX_SLEEP_SAMPLES})")
    normalized = []
    for i, sample in enumerate(raw_samples):
        if not isinstance(sample, dict):
            raise ValidationError(f"samples[{i}] must be an object")
        stage = sleep_stage(sample.get("value"))
        start = parse_dt(sample.get("start"), f"samples[{i}].start")
        end = parse_dt(sample.get("end"), f"samples[{i}].end")
        if end <= start:
            raise ValidationError(f"samples[{i}].end must be after start")
        if (end - start).total_seconds() > 48 * 3600:
            raise ValidationError(f"samples[{i}] is longer than 48 hours")
        normalized.append({
            "stage": stage,
            "start": iso(start),
            "end": iso(end),
            "source": str(sample.get("source") or "").strip()[:120],
        })
    if payload.get("date"):
        parse_date(payload["date"])
    # A sleep night runs noon-to-noon in the configured timezone, labelled by its ending date.
    grouped = {}
    for seg in normalized:
        start, end = parse_dt(seg["start"], "start"), parse_dt(seg["end"], "end")
        while start < end:
            local = start.astimezone(TZ)
            day = local.date() + timedelta(days=1) if local.hour >= 12 else local.date()
            boundary = datetime.combine(day, dtime(12), tzinfo=TZ).astimezone(UTC)
            stop = min(end, boundary)
            grouped.setdefault(day.isoformat(), []).append({**seg, "start": iso(start), "end": iso(stop)})
            start = stop
    changed = False
    counts = {}
    for date_str, segments in sorted(grouped.items()):
        history = _load_history(date_str)
        existing = history.get("sleep", {}).get("segments", [])
        merged = {}
        def key(seg):
            # Preserve InBed alongside sleep, and preserve distinct sources.
            family = "in_bed" if seg["stage"] == "in_bed" else "sleep"
            return (seg["start"], seg["end"], str(seg.get("source") or ""), family)
        for seg in existing:
            merged[key(seg)] = seg
        for seg in segments:
            k = key(seg)
            changed = changed or merged.get(k) != seg
            merged[k] = seg
        sleep = summarize_sleep(list(merged.values()), date_str)
        history["sleep"] = sleep
        _save_history(date_str, history)
        counts[date_str] = sleep["sample_count"]
    latest = max(counts)
    return {"kind": "sleep", "date": latest, "dates": sorted(counts), "changed": changed,
            "sample_count": sum(counts.values())}


def merge_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    raw_metrics = payload.get("metrics")
    if raw_metrics is None:
        raw_metrics = []
    if not isinstance(raw_metrics, list):
        raise ValidationError("metrics must be a list")
    if len(raw_metrics) > MAX_METRICS:
        raise ValidationError(f"too many metrics (max {MAX_METRICS})")
    if payload.get("date"):
        date_str = parse_date(payload.get("date")).isoformat()
    else:
        date_str = local_date(datetime.now(UTC)).isoformat()
    normalized = [normalize_metric(item, date_str) for item in raw_metrics]
    workouts_raw = payload.get("workouts", [])
    if not isinstance(workouts_raw, list) or len(workouts_raw) > MAX_WORKOUTS:
        raise ValidationError(f"workouts must be a list (max {MAX_WORKOUTS})")
    if not raw_metrics and not workouts_raw:
        raise ValidationError("provide metrics or workouts")
    workouts = [normalize_workout(w, date_str) for w in workouts_raw]
    history = _load_history(date_str)
    existing = history.get("metrics") if isinstance(history.get("metrics"), list) else []
    merged = {_metric_key(m): m for m in existing if isinstance(m, dict)}
    changed = False
    for item in normalized:
        key = _metric_key(item)
        old = merged.get(key)
        comparable = ("metric", "value", "unit", "start", "end", "source", "date")
        if old is not None and all(old.get(k) == item.get(k) for k in comparable):
            continue
        item["updated_at"] = time.time_ns()
        merged[key] = item
        changed = True
    existing_workouts = history.get("workouts") if isinstance(history.get("workouts"), list) else []
    workout_map = {
        (str(w.get("start")), str(w.get("end")), str(w.get("activity")), str(w.get("source") or "")): w
        for w in existing_workouts
        if isinstance(w, dict)
    }
    for w in workouts:
        key = (w["start"], w["end"], w["activity"], w["source"])
        if workout_map.get(key) != w:
            workout_map[key] = w
            changed = True
    history["metrics"] = sorted(merged.values(), key=lambda m: (str(m.get("start")), str(m.get("metric"))))
    history["workouts"] = sorted(workout_map.values(), key=lambda w: str(w.get("start")))
    _save_history(date_str, history)
    return {
        "kind": "metrics",
        "date": date_str,
        "changed": changed,
        "metrics": len(normalized),
        "workouts": len(workouts),
    }


def legacy_steps(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("date"):
        date_str = parse_date(payload.get("date")).isoformat()
    else:
        date_str = local_date(datetime.now(UTC)).isoformat()
    steps = _int(payload.get("steps"), "steps")
    metric = {
        "metric": "steps",
        "value": steps,
        "unit": "count",
        "start": iso(datetime.combine(date.fromisoformat(date_str), dtime.min, tzinfo=TZ).astimezone(UTC)),
        "end": iso(datetime.combine(date.fromisoformat(date_str), dtime(23, 59, 59), tzinfo=TZ).astimezone(UTC)),
        "source": str(payload.get("source") or "").strip()[:120],
        "date": date_str,
    }
    history = _load_history(date_str)
    existing = history.get("metrics") if isinstance(history.get("metrics"), list) else []
    merged = {_metric_key(m): m for m in existing if isinstance(m, dict)}
    old = merged.get(_metric_key(metric))
    comparable = ("metric", "value", "unit", "start", "end", "source", "date")
    changed = old is None or any(old.get(k) != metric.get(k) for k in comparable)
    if changed:
        metric["updated_at"] = time.time_ns()
    else:
        metric = old
    merged[_metric_key(metric)] = metric
    history["metrics"] = sorted(merged.values(), key=lambda m: (str(m.get("start")), str(m.get("metric"))))
    _save_history(date_str, history)
    return {"kind": "steps", "date": date_str, "changed": changed, "steps": steps}


def accept_wake_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticated, day-deduplicated wake signal; never trigger on ordinary uploads."""
    occurred = parse_dt(payload.get("occurred_at"), "occurred_at")
    now = datetime.now(UTC)
    if abs((now - occurred).total_seconds()) > 900:
        raise ValidationError("wake event must be within 15 minutes of server time")
    holiday = payload.get("holiday_mode", False)
    if not isinstance(holiday, bool):
        raise ValidationError("holiday_mode must be boolean")
    day = occurred.astimezone(TZ).date().isoformat()
    directory = DATA_ROOT / "wake_events"
    path = directory / f"{day}.json"
    if path.exists():
        return {"kind": "wake_event", "date": day, "queued": False, "duplicate": True,
                "message": "今天已记录起床事件，不重复问候"}
    # A successful upload must precede a wake event. We don't accept a stale app event after a failed upload.
    recent = [p for p in HISTORY_DIR.glob("*.json") if now.timestamp() - p.stat().st_mtime <= 900]
    if not recent:
        raise ValidationError("upload health data successfully before sending wake event")
    event = {"date": day, "occurred_at": occurred.isoformat(), "received_at": now.isoformat(),
             "due_at": (now + timedelta(seconds=180)).isoformat(), "holiday_mode": holiday,
             "status": "pending", "origin": "ios_wake_action"}
    atomic_write(path, event)
    return {"kind": "wake_event", "date": day, "queued": True, "duplicate": False,
            "delay_seconds": 180, "message": "已记录起床，约3分钟后主动问候"}


class Handler(BaseHTTPRequestHandler):
    server_version = "DarkkitaHealth/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Request line only; never log Authorization or payload.
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_json(self, code: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health/ping":
            self.send_json(200, {"ok": True, "service": "darkkita-health", "version": 3, "timezone": str(TZ)})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/health/ingest":
            self.send_json(404, {"error": "not found"})
            return
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {read_token()}"
        if not secrets.compare_digest(auth, expected):
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValidationError("empty request body")
            if length > MAX_BODY:
                self.send_json(413, {"error": f"body too large (max {MAX_BODY} bytes)"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValidationError("JSON object required")
        except ValidationError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            self.send_json(400, {"error": f"bad json: {exc}"})
            return

        try:
            kind = str(payload.get("type") or "").strip().lower()
            with LOCK:
                if kind in ("sleep_samples", "sleep"):
                    result = merge_sleep(payload)
                elif kind == "steps":
                    result = legacy_steps(payload)
                elif kind == "metrics":
                    result = merge_metrics(payload)
                elif kind == "wake_event":
                    result = accept_wake_event(payload)
                else:
                    raise ValidationError("unknown type; use sleep_samples, steps, or metrics")
            self.send_json(200, {"ok": True, "saved": True, **result})
        except ValidationError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            # Keep details out of logs; return a generic message.
            self.send_json(500, {"error": f"ingest failed: {type(exc).__name__}"})


if __name__ == "__main__":
    token = read_token()
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_ROOT, 0o700)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("HEALTH_INGEST_PORT", "8799"))
    host = os.environ.get("HEALTH_INGEST_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"health-ingest listening on {host}:{port}; data={DATA_ROOT}", flush=True)
    server.serve_forever()

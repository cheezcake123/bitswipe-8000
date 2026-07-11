from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as runtime_config
from notifier.korean_alerts import display_alert_value
from notifier.telegram_notifier import send_telegram_message


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_PATH = DATA_DIR / "watch_alert_state.json"
LOG_PATH = DATA_DIR / "watch_alert_log.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_state() -> dict[str, Any]:
    try:
        if not STATE_PATH.exists():
            return {}
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2))


def _append_log(row: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        safe = dict(row)
        safe["ts"] = _now_iso()
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(safe, ensure_ascii=True) + "\n")
        _trim_log()
    except Exception:
        pass


def _trim_log() -> None:
    try:
        max_lines = runtime_config.WATCH_LOG_MAX_LINES
        if max_lines <= 0 or not LOG_PATH.exists():
            return
        with LOG_PATH.open("r") as f:
            lines = deque(f, maxlen=max_lines)
        with LOG_PATH.open("w") as f:
            f.writelines(lines)
    except Exception:
        pass


def get_recent_watch_alert_logs(limit: int = 20) -> list[dict[str, Any]]:
    try:
        if not LOG_PATH.exists():
            return []
        safe_limit = max(1, min(int(limit), 500))
        with LOG_PATH.open("r") as f:
            lines = deque(f, maxlen=safe_limit)
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows
    except Exception:
        return []


def _num(value: Any, digits: int = 2) -> str:
    try:
        if value is None:
            return "N/A"
        return f"{float(value):.{digits}f}"
    except Exception:
        return "N/A"


def _price(value: Any) -> str:
    try:
        if value is None:
            return "N/A"
        return f"${float(value):,.2f}"
    except Exception:
        return "N/A"


def _signature(event: dict[str, Any]) -> str:
    parts = [
        str(event.get("asset_class") or "unknown").upper(),
        str(event.get("symbol") or "UNKNOWN").upper(),
        str(event.get("event_type") or "watch"),
        str(event.get("direction") or "two_way"),
    ]
    return "|".join(parts)


def _daily_count(state: dict[str, Any]) -> int:
    return int(state.get(f"daily:{_day_key()}", 0) or 0)


def _increment_daily_count(state: dict[str, Any]) -> None:
    key = f"daily:{_day_key()}"
    state[key] = int(state.get(key, 0) or 0) + 1


def _clean_state(state: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    day = _day_key()
    for key, value in state.items():
        if key == f"daily:{day}" or key.startswith("sent:"):
            cleaned[key] = value
    sent_keys = [key for key in cleaned if key.startswith("sent:")]
    if len(sent_keys) > 300:
        newest = sorted(sent_keys, key=lambda key: cleaned.get(key, 0), reverse=True)[:300]
        cleaned = {key: cleaned[key] for key in cleaned if not key.startswith("sent:") or key in newest}
    return cleaned


def _format_reasons(event: dict[str, Any]) -> str:
    reasons = event.get("reasons") or []
    if not isinstance(reasons, list) or not reasons:
        return "정보 없음"
    return ", ".join(
        display_alert_value("reason", item)
        for item in reasons[:5]
    )


def format_watch_message(event: dict[str, Any]) -> str:
    symbol = str(event.get("symbol") or "UNKNOWN").upper()
    asset_class = display_alert_value("asset_class", event.get("asset_class"))
    event_type = display_alert_value("event_type", event.get("event_type"))
    direction = display_alert_value("direction", event.get("direction"))
    trend = display_alert_value("trend", event.get("trend"))
    confirmation = display_alert_value(
        "confirmation",
        event.get("confirmation"),
        fallback="캔들 종가와 리테스트 확인이 필요합니다.",
    )
    score = event.get("score")
    min_score = event.get("min_score")
    return (
        "[BitSwipe B등급 관찰 알림]\n"
        "진입 신호 아님 (NOT ENTRY) · 추가 확인 필요 (confirmation needed)\n\n"
        f"종목: {symbol}\n"
        f"등급: B-grade WATCH\n"
        f"자산군: {asset_class}\n"
        f"관찰 이벤트: {event_type}\n"
        f"관찰 방향: {direction}\n"
        f"점수: {_num(score, 0)} / 기준 {_num(min_score, 0)}\n\n"
        f"현재가: {_price(event.get('price'))}\n"
        f"지지선: {_price(event.get('support'))}\n"
        f"저항선: {_price(event.get('resistance'))}\n"
        f"지지선 거리: {_num(event.get('distance_support_pct'), 2)}%\n"
        f"저항선 거리: {_num(event.get('distance_resistance_pct'), 2)}%\n"
        f"RSI: {_num(event.get('rsi'), 1)}\n"
        f"추세: {trend}\n"
        f"거래량 비율: {_num(event.get('volume_ratio'), 2)}x\n\n"
        f"관찰 근거: {_format_reasons(event)}\n"
        f"확인 조건: {confirmation}\n\n"
        "이 알림은 관찰 목록 안내일 뿐 진입 신호가 아닙니다. "
        "낮은 레버리지를 유지하고 확인 조건을 기다리세요."
    )

def maybe_send_watch_alert(event: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    symbol = str(event.get("symbol") or "UNKNOWN").upper()
    signature = _signature(event)

    if not event:
        result = {"sent": False, "reason": "no_event"}
        if not dry_run:
            _append_log(result)
        return result

    if event.get("stale"):
        result = {
            "sent": False,
            "reason": "stale_event_suppressed",
            "symbol": symbol,
            "signature": signature,
        }
        if not dry_run:
            _append_log(result)
        return result

    if dry_run:
        return {
            "sent": False,
            "reason": "dry_run",
            "symbol": symbol,
            "signature": signature,
            "would_send": True,
            "message": format_watch_message(event),
        }

    if not runtime_config.WATCH_ENABLED:
        result = {
            "sent": False,
            "reason": "watch_disabled",
            "symbol": symbol,
            "signature": signature,
        }
        _append_log(result)
        return result

    state = _clean_state(_load_state())
    daily_cap = runtime_config.WATCH_DAILY_CAP
    if daily_cap <= 0:
        result = {
            "sent": False,
            "reason": "daily_cap_zero",
            "symbol": symbol,
            "signature": signature,
        }
        _append_log(result)
        return result

    count = _daily_count(state)
    if count >= daily_cap:
        result = {
            "sent": False,
            "reason": "daily_cap_reached",
            "symbol": symbol,
            "signature": signature,
            "daily_count": count,
            "daily_cap": daily_cap,
        }
        _append_log(result)
        return result

    now = time.time()
    sent_key = f"sent:{signature}"
    last_sent = float(state.get(sent_key, 0) or 0)
    elapsed = now - last_sent
    cooldown = runtime_config.WATCH_ALERT_COOLDOWN_SECONDS
    if last_sent > 0 and elapsed < cooldown:
        result = {
            "sent": False,
            "reason": "duplicate_suppressed",
            "symbol": symbol,
            "signature": signature,
            "elapsed_seconds": round(elapsed, 1),
            "cooldown_seconds": cooldown,
        }
        _append_log(result)
        return result

    telegram_result = send_telegram_message(format_watch_message(event))
    sent = bool(telegram_result.get("ok"))
    if sent:
        state[sent_key] = now
        _increment_daily_count(state)
        _save_state(_clean_state(state))

    result = {
        "sent": sent,
        "reason": "sent" if sent else "telegram_send_failed",
        "symbol": symbol,
        "signature": signature,
        "telegram_ok": sent,
        "daily_count": _daily_count(state),
        "daily_cap": daily_cap,
    }
    _append_log(result)
    return {**result, "telegram_result": telegram_result}

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as runtime_config
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


def _ko(value: Any, mapping: dict[str, str], default: str) -> str:

    key = str(value or "").strip()

    return mapping.get(key, key or default)





_REASON_KO = {

    "near_recent_support": "\ucd5c\uadfc \uc9c0\uc9c0\uc120 \uadfc\ucc98",

    "near_recent_resistance": "\ucd5c\uadfc \uc800\ud56d\uc120 \uadfc\ucc98",

    "possible_reversal_near_support": "\uc9c0\uc9c0\uc120 \ubd80\uadfc \ubc18\ub4f1 \uac00\ub2a5\uc131",

    "possible_rejection_or_breakout_near_resistance": "\uc800\ud56d\uc120 \ubd80\uadfc \ub3cc\ud30c\u00b7\uac70\uc808 \ubd84\uae30\uc810",

    "strong_1h_move_pullback_needed": "1\uc2dc\uac04 \uac15\ud55c \uc6c0\uc9c1\uc784 \uc774\ud6c4 \ub20c\ub9bc \ud655\uc778 \ud544\uc694",

    "strong_4h_move": "4\uc2dc\uac04 \uae30\uc900 \uac15\ud55c \uac00\uaca9 \uc6c0\uc9c1\uc784",

    "volume_spike": "\uac70\ub798\ub7c9 \uae09\uc99d",

    "volume_expansion": "\uac70\ub798\ub7c9 \uc99d\uac00",

    "uptrend_context": "\uc0c1\uc2b9 \ucd94\uc138 \ud658\uacbd",

    "downtrend_context": "\ud558\ub77d \ucd94\uc138 \ud658\uacbd",

}





_EVENT_KO = {

    "structure_watch": "\uad6c\uc870 \uad00\ucc30",

    "near_support_watch": "\uc9c0\uc9c0\uc120 \uad00\ucc30",

    "near_resistance_watch": "\uc800\ud56d\uc120 \uad00\ucc30",

    "reversal_watch_near_support": "\uc9c0\uc9c0\uc120 \ubc18\ub4f1 \uad00\ucc30",

    "resistance_decision_watch": "\uc800\ud56d\uc120 \ub3cc\ud30c\u00b7\uac70\uc808 \uad00\ucc30",

    "strong_move_pullback_watch": "\uac15\ud55c \uc6c0\uc9c1\uc784 \uc774\ud6c4 \ub20c\ub9bc \uad00\ucc30",

    "watch": "\uad00\ucc30",

}





_DIRECTION_KO = {

    "two_way": "\uc591\ubc29\ud5a5 \uad00\ucc30",

    "bullish_watch": "\uc0c1\uc2b9 \uad00\ucc30",

    "bearish_watch": "\ud558\ub77d \uad00\ucc30",

    "long": "\ub871",

    "short": "\uc20f",

}





_TREND_KO = {

    "uptrend": "\uc0c1\uc2b9 \ucd94\uc138",

    "downtrend": "\ud558\ub77d \ucd94\uc138",

    "mixed": "\ud63c\uc870",

    "unknown": "\uc54c \uc218 \uc5c6\uc74c",

}





_CONFIRMATION_KO = {

    "Wait for candle close confirmation and a clean retest. NOT ENTRY.":

        "\uce94\ub4e4 \uc885\uac00 \ud655\uc815\uacfc \uc7ac\uc2dc\ud5d8\uc744 \uae30\ub2e4\ub9ac\uc138\uc694. \uc544\uc9c1 \uc9c4\uc785\ud558\uc9c0 \ub9c8\uc138\uc694.",

    "Watch for either support hold or breakdown. Confirmation needed.":

        "\uc9c0\uc9c0\uc120 \ubc29\uc5b4 \ub610\ub294 \ud558\ud5a5 \uc774\ud0c8 \uc5ec\ubd80\ub97c \ud655\uc778\ud558\uc138\uc694.",

    "Watch for breakout close or rejection. Confirmation needed.":

        "\ub3cc\ud30c \uc885\uac00 \ud655\uc815 \ub610\ub294 \uc800\ud56d \uac70\uc808\uc744 \ud655\uc778\ud558\uc138\uc694.",

    "Strong move detected; wait for pullback/retest. NOT ENTRY.":

        "\uac15\ud55c \uc6c0\uc9c1\uc784\uc774 \uac10\uc9c0\ub410\uc2b5\ub2c8\ub2e4. \ub20c\ub9bc\uacfc \uc7ac\uc2dc\ud5d8\uc744 \uae30\ub2e4\ub9ac\uc138\uc694. \uc544\uc9c1 \uc9c4\uc785\ud558\uc9c0 \ub9c8\uc138\uc694.",

}





def _format_reasons(event: dict[str, Any]) -> str:

    reasons = event.get("reasons") or []

    if not isinstance(reasons, list) or not reasons:

        return "\ud655\uc778\ub41c \uad00\ucc30 \uc0ac\uc720 \uc5c6\uc74c"



    translated = [

        _ko(reason, _REASON_KO, "\uae30\ud0c0 \uad00\ucc30 \uc0ac\uc720")

        for reason in reasons[:5]

    ]

    return "\n".join(f"\u2022 {reason}" for reason in translated)





def format_watch_message(event: dict[str, Any]) -> str:

    symbol = str(event.get("symbol") or "UNKNOWN").upper()

    asset_class = str(event.get("asset_class") or "unknown").lower()



    asset_class_ko = {

        "crypto": "\uac00\uc0c1\uc790\uc0b0",

        "tradfi": "\uc804\ud1b5 \uae08\uc735\uc790\uc0b0",

    }.get(asset_class, asset_class.upper())



    score = event.get("score")

    min_score = event.get("min_score")



    event_type = _ko(

        event.get("event_type"),

        _EVENT_KO,

        "\uc77c\ubc18 \uad00\ucc30",

    )

    direction = _ko(

        event.get("direction"),

        _DIRECTION_KO,

        "\uc591\ubc29\ud5a5 \uad00\ucc30",

    )

    trend = _ko(

        event.get("trend"),

        _TREND_KO,

        "\uc54c \uc218 \uc5c6\uc74c",

    )



    raw_confirmation = str(

        event.get("confirmation")

        or "Wait for candle close confirmation and a clean retest. NOT ENTRY."

    )

    confirmation = _CONFIRMATION_KO.get(

        raw_confirmation,

        raw_confirmation,

    )



    return (

        "\U0001f7e1 BitSwipe \uad00\ucc30 \uc54c\ub9bc\n"

        "\U0001f6ab \ud604\uc7ac \uc9c4\uc785 \uae08\uc9c0 \u00b7 \ucd94\uac00 \ud655\uc778 \ud544\uc694\n\n"

        f"\uc885\ubaa9: {symbol}\n"

        f"\uc790\uc0b0: {asset_class_ko}\n"

        f"\uad00\ucc30 \uc720\ud615: {event_type}\n"

        f"\ubc29\ud5a5: {direction}\n"

        f"\uc810\uc218: {_num(score, 0)} / \uae30\uc900 {_num(min_score, 0)}\n\n"

        f"\ud604\uc7ac\uac00: {_price(event.get('price'))}\n"

        f"\uc9c0\uc9c0\uc120: {_price(event.get('support'))}\n"

        f"\uc800\ud56d\uc120: {_price(event.get('resistance'))}\n"

        f"\uc9c0\uc9c0\uc120\uae4c\uc9c0 \uac70\ub9ac: {_num(event.get('distance_support_pct'), 2)}%\n"

        f"\uc800\ud56d\uc120\uae4c\uc9c0 \uac70\ub9ac: {_num(event.get('distance_resistance_pct'), 2)}%\n"

        f"RSI: {_num(event.get('rsi'), 1)}\n"

        f"\ucd94\uc138: {trend}\n"

        f"\uac70\ub798\ub7c9 \ube44\uc728: {_num(event.get('volume_ratio'), 2)}\ubc30\n\n"

        f"\U0001f4cc \uad00\ucc30 \uc774\uc720\n{_format_reasons(event)}\n\n"

        f"\u2705 \ud655\uc778\ud560 \uc870\uac74\n{confirmation}\n\n"

        "\u26a0\ufe0f \uc774 \uc54c\ub9bc\uc740 \uc9c4\uc785 \uc2e0\ud638\uac00 \uc544\ub2d9\ub2c8\ub2e4. "

        "\uc870\uac74\uc774 \ud655\uc778\ub420 \ub54c\uae4c\uc9c0 \ucd94\uaca9 \uc9c4\uc785\ud558\uc9c0 \ub9c8\uc138\uc694."

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

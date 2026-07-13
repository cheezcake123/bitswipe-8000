"""Optional post-send Scenario Ledger registration for BitSwipe trade alerts.

Version 0.2 accepts the exact structured trade plan from ``trade_alert.py``.
Rendered-text parsing remains only as a backwards-compatible fallback.
The module is deliberately fail-open for Telegram: registration is disabled by
default, never creates the operational database, and never raises to callers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

try:
    from scripts.scenario_ledger import DEFAULT_DB_PATH, ScenarioLedger
except ModuleNotFoundError:  # standalone scripts may put scripts/ on sys.path
    from scenario_ledger import DEFAULT_DB_PATH, ScenarioLedger

LOGGER = logging.getLogger(__name__)

FEATURE_FLAG = "SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED"
DB_PATH_ENV = "SCENARIO_LEDGER_DB_PATH"
TRADE_ALERT_MARKER = "[BitSwipe 진입 심사 보고서]"
REGISTRATION_VERSION = "alert_registration_v0.2"


@dataclass(frozen=True)
class PreparedTradeAlert:
    scenario_id: str
    original_text: str
    outbound_text: str
    symbol: str
    direction: str
    entry: float
    stop: float
    target_1: Optional[float]
    target_2: float
    rr: Optional[float]
    confidence: Optional[float]
    source_mode: str
    text_sha256: str
    plan_sha256: Optional[str]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def registration_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    return _truthy(source.get(FEATURE_FLAG, "0"))


def configured_db_path(env: Optional[Mapping[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    raw = str(source.get(DB_PATH_ENV, "") or "").strip()
    return Path(raw) if raw else Path(DEFAULT_DB_PATH)


def _field(text: str, label: str) -> Optional[str]:
    pattern = rf"(?m)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _number(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "-"}:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except (TypeError, ValueError, OverflowError):
        return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_symbol(value: object) -> str:
    return re.sub(r"[\s/_-]+", "", str(value or "").upper())


def _normalize_direction(value: object) -> Optional[str]:
    text = str(value or "").strip().upper()
    if text in {"LONG", "BUY", "롱", "매수"}:
        return "LONG"
    if text in {"SHORT", "SELL", "숏", "매도"}:
        return "SHORT"
    return None


def _new_scenario_id(now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    return f"BS-{current.strftime('%y%m%d')}-{secrets.token_hex(4).upper()}"


def _valid_plan(direction: str, entry: float, stop: float, target: float) -> bool:
    if min(entry, stop, target) <= 0:
        return False
    if direction == "LONG":
        return stop < entry < target
    if direction == "SHORT":
        return target < entry < stop
    return False


def _canonical_plan_hash(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _from_structured_plan(plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    symbol = _normalize_symbol(_first(plan, "symbol", "pair_label"))
    direction = _normalize_direction(_first(plan, "direction", "side", "signal"))
    entry = _number(_first(plan, "entry", "entry_price"))
    stop = _number(_first(plan, "stop", "stop_price"))
    target_1 = _number(_first(plan, "target_1", "first_target"))
    target_2 = _number(_first(plan, "target_2", "target", "target_price"))
    rr = _number(_first(plan, "rr", "risk_reward_ratio"))
    confidence = _number(plan.get("confidence"))

    if not symbol or direction is None or entry is None or stop is None or target_2 is None:
        return None
    if not _valid_plan(direction, entry, stop, target_2):
        return None
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target_1": target_1,
        "target_2": target_2,
        "rr": rr,
        "confidence": confidence,
        "source_mode": "structured_plan",
        "plan_sha256": _canonical_plan_hash(plan),
    }


def _from_rendered_text(text: str) -> Optional[Dict[str, Any]]:
    symbol = _normalize_symbol(_field(text, "종목"))
    direction = _normalize_direction(_field(text, "방향"))
    entry = _number(_field(text, "- 진입가"))
    stop = _number(_field(text, "- 손절가"))
    target_1 = _number(_field(text, "- 1차 목표(1R)"))
    target_2 = _number(_field(text, "- 2차 목표(AI 최종 목표)"))
    rr = _number(_field(text, "- 예상 손익비"))
    confidence = _number(_field(text, "신뢰도"))

    if not symbol or direction is None or entry is None or stop is None or target_2 is None:
        return None
    if not _valid_plan(direction, entry, stop, target_2):
        return None
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target_1": target_1,
        "target_2": target_2,
        "rr": rr,
        "confidence": confidence,
        "source_mode": "rendered_text_fallback",
        "plan_sha256": None,
    }


def prepare_trade_alert_registration(
    text: str,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    scenario_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[PreparedTradeAlert]:
    """Prepare an ID-annotated alert without creating a database.

    Structured ``plan`` values take precedence and preserve sub-cent prices.
    ``None`` means "leave the existing Telegram flow unchanged".
    """

    if not registration_enabled(env):
        return None
    original = str(text or "")
    if TRADE_ALERT_MARKER not in original:
        return None

    try:
        readiness = ScenarioLedger(configured_db_path(env)).inspect_database()
        if not readiness.ok:
            LOGGER.warning(
                "Scenario Ledger registration skipped: database not ready (%s)",
                readiness.code,
            )
            return None

        parsed = None
        if isinstance(plan, Mapping):
            parsed = _from_structured_plan(plan)
            if parsed is None:
                LOGGER.warning("Scenario Ledger registration skipped: invalid structured plan")
                return None
        else:
            parsed = _from_rendered_text(original)
            if parsed is None:
                LOGGER.warning("Scenario Ledger registration skipped: invalid rendered trade alert")
                return None

        sid = str(scenario_id or _new_scenario_id(now)).strip()
        if not sid:
            return None
        outbound = f"{original}\n\n🆔 시나리오 ID: {sid}"
        return PreparedTradeAlert(
            scenario_id=sid,
            original_text=original,
            outbound_text=outbound,
            symbol=parsed["symbol"],
            direction=parsed["direction"],
            entry=parsed["entry"],
            stop=parsed["stop"],
            target_1=parsed["target_1"],
            target_2=parsed["target_2"],
            rr=parsed["rr"],
            confidence=parsed["confidence"],
            source_mode=parsed["source_mode"],
            text_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            plan_sha256=parsed["plan_sha256"],
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        LOGGER.warning("Scenario Ledger registration prepare failed: %s", exc)
        return None


def register_successful_trade_alert(
    prepared: Optional[PreparedTradeAlert],
    telegram_response: Mapping[str, Any],
    *,
    db_path: Optional[str | Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Persist a prepared scenario only after Telegram reports success."""

    if prepared is None:
        return {"ok": True, "registered": False, "code": "NOT_PREPARED"}
    if not bool(telegram_response.get("ok")):
        return {"ok": True, "registered": False, "code": "TELEGRAM_NOT_OK"}

    try:
        result_obj = telegram_response.get("result") or {}
        if not isinstance(result_obj, Mapping):
            result_obj = {}
        message_id = result_obj.get("message_id")
        chat_obj = result_obj.get("chat") or {}
        chat_id = chat_obj.get("id") if isinstance(chat_obj, Mapping) else None
        sent_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

        source_alert_id = (
            f"telegram:{chat_id}:{message_id}"
            if message_id is not None
            else f"telegram:scenario:{prepared.scenario_id}"
        )
        payload = {
            "registration_version": REGISTRATION_VERSION,
            "registration_source_mode": prepared.source_mode,
            "source_alert_id": source_alert_id,
            "telegram_message_id": message_id,
            "entry": prepared.entry,
            "stop": prepared.stop,
            "target_1": prepared.target_1,
            "target_2": prepared.target_2,
            "target": prepared.target_2,
            "rr": prepared.rr,
            "confidence": prepared.confidence,
            "alert_text_sha256": prepared.text_sha256,
            "structured_plan_sha256": prepared.plan_sha256,
            "sent_at": sent_at,
        }
        ledger = ScenarioLedger(db_path or configured_db_path(env))
        ledger_result = ledger.create_scenario(
            scenario_id=prepared.scenario_id,
            source="telegram_trade_alert",
            symbol=prepared.symbol,
            direction=prepared.direction,
            payload=payload,
            event_key=source_alert_id,
            created_at=sent_at,
        )
        return {
            "ok": bool(ledger_result.ok),
            "registered": ledger_result.code in {"CREATED", "ALREADY_EXISTS"},
            "code": ledger_result.code,
            "scenario_id": prepared.scenario_id,
            "source_mode": prepared.source_mode,
            "message": ledger_result.message,
        }
    except Exception as exc:  # pragma: no cover - defensive boundary
        LOGGER.warning("Scenario Ledger registration failed: %s", exc)
        return {
            "ok": False,
            "registered": False,
            "code": "REGISTRATION_ERROR",
            "scenario_id": prepared.scenario_id,
            "message": f"{type(exc).__name__}: {exc}",
        }


__all__ = [
    "DB_PATH_ENV",
    "FEATURE_FLAG",
    "PreparedTradeAlert",
    "REGISTRATION_VERSION",
    "configured_db_path",
    "prepare_trade_alert_registration",
    "register_successful_trade_alert",
    "registration_enabled",
]

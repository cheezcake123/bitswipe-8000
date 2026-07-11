from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from notifier.telegram_notifier import send_telegram_message


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_PATH = DATA_DIR / "telegram_alert_state.json"
LOG_PATH = DATA_DIR / "telegram_alert_log.jsonl"
DEFAULT_COOLDOWN_SECONDS = 3600
MIN_TRADE_ALERT_CONFIDENCE = 75.0
TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        number = _safe_float(value)
        if number is not None:
            return number
    return None


def _positive_number(*values: Any) -> Optional[float]:
    number = _first_number(*values)
    return number if number is not None and number > 0 else None


def _nonnegative_number(*values: Any) -> Optional[float]:
    number = _first_number(*values)
    return number if number is not None and number >= 0 else None


def _clean_text(value: Any, fallback: str = "정보 없음", max_chars: int = 240) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return fallback
    text = " ".join(str(value).split()).strip()
    if not text:
        return fallback
    if len(text) > max_chars:
        return text[: max(1, max_chars - 1)].rstrip() + "…"
    return text


def _text_items(value: Any, *, max_items: int = 3, max_chars: int = 160) -> List[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return []

    items = []
    for item in values:
        text = _clean_text(item, fallback="", max_chars=max_chars)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _side(signal: Any) -> Optional[str]:
    value = str(signal or "").strip().lower()
    if value in ("buy", "long", "maesu", "매수", "롱"):
        return "LONG"
    if value in ("sell", "short", "maedo", "매도", "숏"):
        return "SHORT"
    return None


def _price(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    digits = 6 if abs(number) < 1 else 4 if abs(number) < 100 else 2
    return "$" + f"{number:,.{digits}f}"


def _num(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    return f"{number:.{digits}f}{suffix}"


def _rounded(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "NA" if number is None else f"{number:.{digits}f}"


def calculate_one_r_target(
    side: str,
    entry_price: float,
    stop_price: float,
) -> Optional[float]:
    """Return the deterministic 1R target without performing I/O."""
    normalized_side = _side(side)
    entry = _safe_float(entry_price)
    stop = _safe_float(stop_price)
    if normalized_side not in ("LONG", "SHORT") or entry is None or stop is None:
        return None
    if entry <= 0 or stop <= 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + risk if normalized_side == "LONG" else entry - risk
    return target if target > 0 else None


def _calculated_risk_reward(
    entry: Optional[float],
    stop: Optional[float],
    target: Optional[float],
) -> Optional[float]:
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def _stop_direction_valid(
    side: Optional[str], entry: Optional[float], stop: Optional[float]
) -> Optional[bool]:
    if side not in ("LONG", "SHORT") or entry is None or stop is None:
        return None
    return stop < entry if side == "LONG" else stop > entry


def _target_direction_valid(
    side: Optional[str], entry: Optional[float], target: Optional[float]
) -> Optional[bool]:
    if side not in ("LONG", "SHORT") or entry is None or target is None:
        return None
    return target > entry if side == "LONG" else target < entry


def normalize_trade_alert_payload(payload: Any) -> Dict[str, Any]:
    """Normalize the real server payload without mutating it or performing I/O."""
    payload_present = isinstance(payload, Mapping) and bool(payload)
    raw = _as_mapping(payload)
    guard = _as_mapping(raw.get("risk_guard"))
    trade_levels = _as_mapping(raw.get("trade_levels"))
    analysis_json = _as_mapping(raw.get("analysis_json"))
    report_sections = _as_mapping(raw.get("report_sections"))
    structured_trade = _as_mapping(analysis_json.get("trade"))
    actions = _as_mapping(analysis_json.get("actions"))
    levels = _as_mapping(analysis_json.get("levels"))

    side = _side(raw.get("signal"))
    guard_side = _side(guard.get("side"))
    confidence = _safe_float(raw.get("confidence"))
    if confidence is None or not 0.0 <= confidence <= 100.0:
        confidence = 0.0

    symbol = _clean_text(
        raw.get("symbol") or raw.get("pair_label") or "UNKNOWN",
        fallback="UNKNOWN",
        max_chars=80,
    ).upper()
    display_symbol = _clean_text(
        raw.get("pair_label") or raw.get("symbol") or "UNKNOWN",
        fallback="UNKNOWN",
        max_chars=80,
    )

    entry = _positive_number(
        guard.get("entry_price"), trade_levels.get("entry"), structured_trade.get("entry")
    )
    stop = _positive_number(
        guard.get("stop_price"), trade_levels.get("stop"), structured_trade.get("stop")
    )
    target = _positive_number(
        guard.get("target_price"), trade_levels.get("target"), structured_trade.get("target")
    )
    current_price = _positive_number(raw.get("price"))
    risk_reward = _positive_number(
        guard.get("risk_reward_ratio"), raw.get("risk_reward_ratio")
    )
    if risk_reward is None:
        risk_reward = _calculated_risk_reward(entry, stop, target)

    facts = _text_items(
        report_sections.get("facts") or analysis_json.get("key_facts"),
        max_items=3,
        max_chars=150,
    )
    inferences = _text_items(
        report_sections.get("interpretation") or analysis_json.get("inferences"),
        max_items=3,
        max_chars=150,
    )
    counter_scenario = _text_items(
        report_sections.get("counter_scenario") or analysis_json.get("counter_scenario"),
        max_items=2,
        max_chars=170,
    )
    responses = _text_items(report_sections.get("response"), max_items=3, max_chars=170)
    trigger_key = "bull_trigger" if side == "LONG" else "bear_trigger"
    trigger = raw.get("entry_trigger") or levels.get(trigger_key)
    if trigger in (None, "") and responses:
        trigger = responses[0]

    leverage = _positive_number(
        guard.get("leverage"), structured_trade.get("leverage"), raw.get("leverage")
    )
    max_margin_percent = _nonnegative_number(
        guard.get("max_position_percent_by_account_risk"),
        raw.get("max_position_percent_by_account_risk"),
    )
    max_notional_percent = (
        max_margin_percent * leverage
        if max_margin_percent is not None and leverage is not None
        else None
    )
    verdict = _clean_text(guard.get("verdict"), fallback="", max_chars=32).upper()
    return {
        "payload_present": payload_present,
        "symbol": symbol,
        "display_symbol": display_symbol,
        "side": side,
        "guard_side": guard_side,
        "confidence": confidence,
        "risk_guard_verdict": verdict,
        "current_price": current_price,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "target_1r": calculate_one_r_target(side or "", entry, stop),
        "risk_reward_ratio": risk_reward,
        "stop_distance_percent": _nonnegative_number(
            guard.get("stop_distance_percent"), raw.get("stop_distance_percent")
        ),
        "leverage": leverage,
        "leveraged_loss_percent_on_margin": _nonnegative_number(
            guard.get("leveraged_loss_percent_on_margin"),
            raw.get("leveraged_loss_percent_on_margin"),
        ),
        "max_position_percent_by_account_risk": max_margin_percent,
        "max_notional_percent_by_account_risk": max_notional_percent,
        "risk_percent": _nonnegative_number(guard.get("risk_percent")),
        "stop_direction_valid": _stop_direction_valid(side, entry, stop),
        "target_direction_valid": _target_direction_valid(side, entry, target),
        "market_view": _clean_text(
            report_sections.get("view") or analysis_json.get("view"), max_chars=160
        ),
        "market_regime": _clean_text(
            report_sections.get("regime") or analysis_json.get("regime"), max_chars=120
        ),
        "summary": _clean_text(
            report_sections.get("summary") or analysis_json.get("summary"), max_chars=260
        ),
        "facts": facts,
        "inferences": inferences,
        "entry_trigger": _clean_text(
            trigger,
            fallback="캔들 종가 확인과 리테스트 후 사용자가 최종 판단",
            max_chars=240,
        ),
        "aggressive_action": _clean_text(
            actions.get("aggressive") or raw.get("aggressive_action"), max_chars=220
        ),
        "conservative_action": _clean_text(
            actions.get("conservative") or raw.get("conservative_action"), max_chars=220
        ),
        "invalidation": _clean_text(
            report_sections.get("invalidation") or analysis_json.get("invalidation"),
            max_chars=240,
        ),
        "counter_scenario": counter_scenario,
        "guard_warnings": _text_items(guard.get("warnings"), max_items=3, max_chars=150),
        "guard_errors": _text_items(guard.get("errors"), max_items=3, max_chars=150),
        "guard_hard_blocks": _text_items(
            guard.get("hard_blocks"), max_items=3, max_chars=150
        ),
    }


def evaluate_trade_alert(payload: Any) -> Dict[str, Any]:
    """Evaluate production eligibility without cooldown, files, logs, or network I/O."""
    candidate = normalize_trade_alert_payload(payload)
    reason = "eligible"
    eligible = True
    if not candidate["payload_present"]:
        eligible, reason = False, "no_payload"
    elif candidate["risk_guard_verdict"] != "PASS":
        eligible, reason = False, "risk_guard_not_pass"
    elif candidate["side"] not in ("LONG", "SHORT"):
        eligible, reason = False, "signal_not_buy_or_sell"
    elif candidate["guard_side"] and candidate["guard_side"] != candidate["side"]:
        eligible, reason = False, "risk_guard_side_mismatch"
    elif candidate["confidence"] < MIN_TRADE_ALERT_CONFIDENCE:
        eligible, reason = False, "confidence_too_low"

    return {
        "eligible": eligible,
        "reason": reason,
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "guard_side": candidate["guard_side"],
        "confidence": candidate["confidence"],
        "risk_guard_verdict": candidate["risk_guard_verdict"],
        "stop_direction_valid": candidate["stop_direction_valid"],
        "target_direction_valid": candidate["target_direction_valid"],
    }


def _bullet_lines(items: List[str], fallback: str = "정보 없음") -> str:
    if not items:
        return f"• {fallback}"
    return "\n".join(f"• {item}" for item in items)


def _stop_direction_text(candidate: Mapping[str, Any]) -> str:
    valid = candidate.get("stop_direction_valid")
    side = candidate.get("side")
    if valid is True:
        relation = "손절가 < 진입가" if side == "LONG" else "손절가 > 진입가"
        return f"손절 방향 검증: 정상 ({relation})"
    if valid is False:
        required = "진입가보다 낮아야" if side == "LONG" else "진입가보다 높아야"
        return f"⚠️ 가격 구조 경고: {side} 손절가는 {required} 합니다."
    return "⚠️ 가격 구조 경고: 진입가 또는 손절가가 없어 방향을 확인할 수 없습니다."


def _guard_side_text(candidate: Mapping[str, Any]) -> str:
    guard_side = candidate.get("guard_side")
    side = candidate.get("side")
    if not guard_side:
        return "Risk Guard 검증 방향: 정보 없음"
    if guard_side != side:
        return f"⚠️ 방향 불일치 경고: 신호 {side} / Risk Guard {guard_side}"
    return f"Risk Guard 검증 방향: {guard_side} (일치)"


def _target_direction_text(candidate: Mapping[str, Any]) -> str:
    valid = candidate.get("target_direction_valid")
    side = candidate.get("side")
    if valid is True:
        return "AI 최종 목표 방향: 정상"
    if valid is False:
        required = "진입가보다 높아야" if side == "LONG" else "진입가보다 낮아야"
        return f"⚠️ 목표 구조 경고: {side} 목표가는 {required} 합니다."
    return "AI 최종 목표 방향: 확인 불가"


def _limit_message(full_blocks: List[str], essential_blocks: List[str]) -> str:
    message = "\n\n".join(block for block in full_blocks if block).strip()
    if len(message) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return message

    suffix = "\n\n[길이 제한으로 기타 해설을 생략했습니다.]"
    compact = "\n\n".join(block for block in essential_blocks if block).strip()
    if len(compact) + len(suffix) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return compact + suffix

    keep = TELEGRAM_MAX_MESSAGE_LENGTH - len(suffix)
    return compact[:keep].rstrip() + suffix


def build_trade_alert_message(
    payload: Any,
    *,
    test_mode: bool = False,
) -> str:
    """Build a deterministic Korean message without files, state, logs, or network I/O."""
    candidate = normalize_trade_alert_payload(payload)
    side = candidate["side"]
    side_label = {"LONG": "롱 (LONG)", "SHORT": "숏 (SHORT)"}.get(
        side, "확인 불가"
    )
    verdict = candidate["risk_guard_verdict"] or "정보 없음"
    verdict_label = "통과 (PASS)" if verdict == "PASS" else verdict
    title = (
        "[테스트] [BitSwipe 진입 심사 보고서]"
        if test_mode
        else "[BitSwipe 진입 심사 보고서]"
    )
    confidence = _num(candidate["confidence"], 0, "%")
    rr = _num(candidate["risk_reward_ratio"], 2)
    rr_display = "N/A" if rr == "N/A" else f"1:{rr}"

    identity = (
        f"{title}\n\n"
        f"종목: {candidate['display_symbol']}\n"
        f"최종 판정: Risk Guard {verdict_label}\n"
        f"방향: {side_label}\n"
        f"신뢰도: {confidence}\n"
        f"시장 관점: {candidate['market_view']}\n"
        f"시장 국면: {candidate['market_regime']}"
    )
    price_plan = (
        "가격 계획\n"
        f"현재가: {_price(candidate['current_price'])}\n"
        f"진입가: {_price(candidate['entry_price'])}\n"
        f"손절가: {_price(candidate['stop_price'])}\n"
        f"1차 목표가 (1R): {_price(candidate['target_1r'])}\n"
        f"AI 최종 목표가: {_price(candidate['target_price'])}\n"
        f"예상 손익비: {rr_display}\n"
        f"{_stop_direction_text(candidate)}\n"
        f"{_target_direction_text(candidate)}\n"
        f"{_guard_side_text(candidate)}"
    )
    risk = (
        "위험 관리\n"
        f"손절 거리: {_num(candidate['stop_distance_percent'], 2, '%')}\n"
        f"검토 레버리지: {_num(candidate['leverage'], 1, 'x')}\n"
        "증거금 기준 손절 예상 손실률: "
        f"{_num(candidate['leveraged_loss_percent_on_margin'], 2, '%')}\n"
        "계좌 위험 기준 최대 증거금 배분 비율: "
        f"{_num(candidate['max_position_percent_by_account_risk'], 2, '%')}\n"
        "레버리지 반영 최대 명목 노출 비율: "
        f"{_num(candidate['max_notional_percent_by_account_risk'], 2, '%')}"
    )
    invalidation = (
        "시나리오 폐기 조건\n"
        f"무효화 조건: {candidate['invalidation']}\n"
        "반대 시나리오\n"
        f"{_bullet_lines(candidate['counter_scenario'])}"
    )
    warning = (
        "주의\n"
        "이 알림은 자동 주문 신호가 아닙니다. 실제 주문을 생성하지 않습니다.\n"
        "캔들 종가와 손절 위치를 다시 확인한 뒤 사용자가 최종 승인해야 합니다.\n"
        "추격 진입·물타기·손절 확대를 금지합니다."
    )
    current = (
        "현재 상황\n"
        f"요약: {candidate['summary']}\n"
        "핵심 사실\n"
        f"{_bullet_lines(candidate['facts'])}\n"
        "해석\n"
        f"{_bullet_lines(candidate['inferences'])}"
    )
    scenario = (
        "진입 시나리오\n"
        f"진입 트리거: {candidate['entry_trigger']}\n"
        f"공격적 대응: {candidate['aggressive_action']}\n"
        f"보수적 대응: {candidate['conservative_action']}"
    )
    position = (
        "포지션 관리\n"
        "• 1차 목표에서 일부 익절을 검토합니다.\n"
        "• 1R 도달 이후 본절 관리를 검토합니다.\n"
        "• 물타기 금지 · 손절 확대 금지 · 추격 진입 금지"
    )

    full_blocks = [
        identity,
        current,
        scenario,
        price_plan,
        risk,
        position,
        invalidation,
        warning,
    ]
    essential_blocks = [identity, price_plan, risk, invalidation, warning]
    return _limit_message(full_blocks, essential_blocks)


def _cooldown_seconds() -> int:
    raw = os.getenv("TELEGRAM_ALERT_COOLDOWN_SECONDS")
    try:
        if raw:
            return max(60, int(raw))
    except (TypeError, ValueError):
        pass
    return DEFAULT_COOLDOWN_SECONDS


def _load_state() -> Dict[str, Any]:
    try:
        if not STATE_PATH.exists():
            return {}
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    temporary.replace(STATE_PATH)


def _append_log(row: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        safe = dict(row)
        safe["ts"] = _now_iso()
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=True) + "\n")
    except Exception:
        pass


def get_recent_alert_logs(limit: int = 20) -> List[Dict[str, Any]]:
    try:
        if not LOG_PATH.exists():
            return []
        safe_limit = max(1, min(int(limit), 500))
        with LOG_PATH.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=safe_limit)

        rows = []
        for line in lines:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except Exception:
                continue
        return rows
    except Exception:
        return []


def _signature_number(value: Any) -> str:
    number = _safe_float(value)
    return "NA" if number is None else format(number, ".10g")


def _alert_signature(candidate: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(candidate.get("symbol") or "UNKNOWN"),
            str(candidate.get("side") or "UNKNOWN"),
            _signature_number(candidate.get("entry_price")),
            _signature_number(candidate.get("stop_price")),
            _signature_number(candidate.get("target_price")),
        ]
    )


def _legacy_alert_signature(candidate: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(candidate.get("symbol") or "UNKNOWN"),
            str(candidate.get("side") or "UNKNOWN"),
            _rounded(candidate.get("entry_price")),
            _rounded(candidate.get("stop_price")),
            _rounded(candidate.get("target_price")),
        ]
    )


def _should_suppress_duplicate(signature: str) -> Dict[str, Any]:
    state = _load_state()
    now = time.time()
    cooldown = _cooldown_seconds()
    last_sent_at = _safe_float(state.get(signature)) or 0.0
    elapsed = now - last_sent_at
    if last_sent_at > 0 and elapsed < cooldown:
        return {
            "suppress": True,
            "reason": "duplicate_suppressed",
            "elapsed_seconds": round(elapsed, 1),
            "cooldown_seconds": cooldown,
        }
    return {"suppress": False, "cooldown_seconds": cooldown}


def _mark_sent(signature: str) -> None:
    state = _load_state()
    state[signature] = time.time()
    if len(state) > 200:
        state = dict(
            sorted(
                state.items(),
                key=lambda item: _safe_float(item[1]) or 0.0,
                reverse=True,
            )[:200]
        )
    _save_state(state)


def _safe_send(
    sender: Callable[[str], Mapping[str, Any]], message: str
) -> Dict[str, Any]:
    try:
        result = sender(message)
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__}
    if not isinstance(result, Mapping):
        return {"ok": False, "error_type": "invalid_response"}
    return dict(result)


def maybe_send_trade_alert(
    payload: Any,
    *,
    dry_run: bool = False,
    test_mode: bool = False,
    sender: Optional[Callable[[str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Evaluate and optionally send while isolating test paths from production state."""
    candidate = normalize_trade_alert_payload(payload)
    evaluation = evaluate_trade_alert(payload)
    base_result = {
        "symbol": evaluation["symbol"],
        "side": evaluation["side"],
        "confidence": evaluation["confidence"],
        "verdict": evaluation["risk_guard_verdict"],
        "eligible": evaluation["eligible"],
    }

    if not evaluation["eligible"]:
        result = {"sent": False, "reason": evaluation["reason"], **base_result}
        if not dry_run and not test_mode:
            _append_log(result)
        return result

    message = build_trade_alert_message(payload, test_mode=test_mode)
    if dry_run:
        return {
            "sent": False,
            "reason": "dry_run",
            "would_send": True,
            "message": message,
            "message_length": len(message),
            "evaluation": evaluation,
            **base_result,
        }

    if test_mode:
        if sender is None:
            return {
                "sent": False,
                "reason": "test_sender_required",
                "test_mode": True,
                "message_length": len(message),
                "telegram_ok": False,
                **base_result,
            }
        telegram_result = _safe_send(sender, message)
        sent = telegram_result.get("ok") is True
        return {
            "sent": sent,
            "reason": "test_sent" if sent else "test_send_failed",
            "test_mode": True,
            "message_length": len(message),
            "telegram_ok": sent,
            **base_result,
        }

    selected_sender = sender or send_telegram_message
    signature = _alert_signature(candidate)
    legacy_signature = _legacy_alert_signature(candidate)
    duplicate = _should_suppress_duplicate(signature)
    if not duplicate.get("suppress") and legacy_signature != signature:
        legacy_duplicate = _should_suppress_duplicate(legacy_signature)
        if legacy_duplicate.get("suppress"):
            duplicate = {**legacy_duplicate, "matched_signature": legacy_signature}
    if duplicate.get("suppress"):
        result = {
            "sent": False,
            "reason": "duplicate_suppressed",
            "signature": signature,
            "matched_signature": duplicate.get("matched_signature", signature),
            "elapsed_seconds": duplicate.get("elapsed_seconds"),
            "cooldown_seconds": duplicate.get("cooldown_seconds"),
            **base_result,
        }
        _append_log(result)
        return result

    telegram_result = _safe_send(selected_sender, message)
    sent = telegram_result.get("ok") is True
    state_recorded = False
    state_error_type = None
    if sent:
        try:
            _mark_sent(signature)
            state_recorded = True
        except Exception as exc:
            state_error_type = type(exc).__name__

    result = {
        "sent": sent,
        "reason": "sent" if sent else "telegram_send_failed",
        "signature": signature,
        "telegram_ok": sent,
        "message_length": len(message),
        "state_recorded": state_recorded,
        **base_result,
    }
    if state_error_type:
        result["state_error_type"] = state_error_type
    _append_log(result)
    return {**result, "telegram_result": telegram_result}

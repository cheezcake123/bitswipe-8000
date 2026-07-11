from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notifier.trade_alert import (  # noqa: E402
    LOG_PATH,
    MIN_TRADE_ALERT_CONFIDENCE,
    STATE_PATH,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    build_trade_alert_message,
    calculate_one_r_target,
    evaluate_trade_alert,
    maybe_send_trade_alert,
    normalize_trade_alert_payload,
)


def build_test_payload(side: str) -> Dict[str, Any]:
    normalized_side = side.strip().upper()
    if normalized_side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")

    is_long = normalized_side == "LONG"
    symbol = "TESTLONGUSDT" if is_long else "TESTSHORTUSDT"
    signal = "BUY" if is_long else "SELL"
    entry = 100.0
    stop = 98.0 if is_long else 102.0
    target = 104.0 if is_long else 96.0
    view = "상방 우위" if is_long else "하방 우위"
    trigger_key = "bull_trigger" if is_long else "bear_trigger"
    trigger = 100.5 if is_long else 99.5

    return {
        "symbol": symbol,
        "pair_label": symbol,
        "price": entry,
        "signal": signal,
        "confidence": 82,
        "analysis_json": {
            "view": view,
            "regime": "변동성 확장",
            "key_facts": [
                "4시간 구조가 진입 방향과 정합합니다.",
                "거래량이 최근 평균보다 증가했습니다.",
            ],
            "inferences": [
                "종가 확인 전에는 진입하지 않습니다.",
                "리테스트가 실패하면 시나리오를 폐기합니다.",
            ],
            "counter_scenario": [
                "핵심 레벨을 반대 방향으로 종가 이탈하면 관점을 취소합니다."
            ],
            "levels": {
                "bull_trigger": trigger if is_long else None,
                "bear_trigger": trigger if not is_long else None,
            },
            "trade": {
                "entry": entry,
                "stop": stop,
                "target": target,
                "leverage": 3,
            },
            "actions": {
                "aggressive": "확인 봉 마감 뒤 소규모 분할 진입만 검토합니다.",
                "conservative": "리테스트 지지 또는 저항 확인 뒤 진입을 검토합니다.",
            },
            "invalidation": "손절 기준을 종가로 명확히 이탈하면 즉시 폐기합니다.",
            "summary": "Risk Guard 통과 여부와 가격 구조를 재확인하는 테스트 시나리오입니다.",
        },
        "report_sections": {
            "view": view,
            "regime": "변동성 확장",
            "facts": [
                "멀티 타임프레임 구조가 진입 방향과 일치합니다.",
                "손절 위치가 진입가의 올바른 방향에 있습니다.",
            ],
            "interpretation": [
                "확인 봉과 리테스트가 모두 충족될 때만 진입을 검토합니다."
            ],
            "counter_scenario": [
                "반대 방향 종가 이탈 시 현재 시나리오는 무효입니다."
            ],
            "response": ["확인 봉 마감과 리테스트를 기다립니다."],
            "invalidation": "손절 기준을 종가로 이탈하면 시나리오를 폐기합니다.",
            "summary": "자동 주문 없이 상세 진입 심사 메시지만 검증합니다.",
        },
        "trade_levels": {
            "entry": entry,
            "stop": stop,
            "target": target,
        },
        "risk_guard": {
            "valid": True,
            "verdict": "PASS",
            "side": normalized_side.lower(),
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "leverage": 3.0,
            "risk_percent": 1.0,
            "stop_distance": 2.0,
            "stop_distance_percent": 2.0,
            "leveraged_loss_percent_on_margin": 6.0,
            "reward_distance": 4.0,
            "reward_percent": 4.0,
            "risk_reward_ratio": 2.0,
            "max_position_percent_by_account_risk": 16.67,
            "warnings": [],
            "hard_blocks": [],
        },
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "leverage": 3.0,
        "risk_reward_ratio": 2.0,
        "stop_distance_percent": 2.0,
        "leveraged_loss_percent_on_margin": 6.0,
        "max_position_percent_by_account_risk": 16.67,
    }


def _file_snapshot(path: Path) -> Tuple[bool, int, int, str]:
    if not path.exists():
        return (False, 0, 0, "")
    data = path.read_bytes()
    stat = path.stat()
    return (True, stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())


def _validate_case(
    side: str,
    payload: Dict[str, Any],
    message: str,
    result: Dict[str, Any],
    *,
    send: bool,
    state_unchanged: bool,
) -> List[Tuple[str, bool]]:
    candidate = normalize_trade_alert_payload(payload)
    evaluation = evaluate_trade_alert(payload)
    entry = candidate["entry_price"]
    stop = candidate["stop_price"]
    target = candidate["target_price"]
    target_1r = calculate_one_r_target(side, entry, stop)
    is_long = side == "LONG"

    checks = [
        ("Risk Guard verdict가 PASS", evaluation["risk_guard_verdict"] == "PASS"),
        (
            "confidence가 75 이상",
            evaluation["confidence"] >= MIN_TRADE_ALERT_CONFIDENCE,
        ),
        ("방향 인식 성공", evaluation["side"] == side),
        (
            "손절 방향 정상",
            stop < entry if is_long else stop > entry,
        ),
        (
            "1R 목표 방향 정상",
            target_1r > entry if is_long else target_1r < entry,
        ),
        (
            "AI 최종 목표 방향 정상",
            target > entry if is_long else target < entry,
        ),
        (
            "손익비가 유한한 숫자",
            isinstance(candidate["risk_reward_ratio"], float)
            and math.isfinite(candidate["risk_reward_ratio"]),
        ),
        (
            "한국어 상세 알림 제목 포함",
            "[BitSwipe 진입 심사 보고서]" in message,
        ),
        (
            "Telegram 길이 제한 준수",
            len(message) <= TELEGRAM_MAX_MESSAGE_LENGTH,
        ),
        ("테스트 제목 구분", "[테스트]" in message),
        ("production 상태와 로그 불변", state_unchanged),
    ]
    if send:
        checks.append(("명시적 Telegram 테스트 전송 성공", result.get("sent") is True))
    else:
        checks.extend(
            [
                ("기본 실행이 dry-run", result.get("reason") == "dry_run"),
                ("dry-run에서 전송 시도 없음", result.get("sent") is False),
                ("dry-run 결과가 would_send", result.get("would_send") is True),
            ]
        )
    return checks


def _run_case(side: str, *, send: bool) -> bool:
    payload = build_test_payload(side)
    evaluation = evaluate_trade_alert(payload)
    message = build_trade_alert_message(payload, test_mode=True)

    state_before = _file_snapshot(STATE_PATH)
    log_before = _file_snapshot(LOG_PATH)
    result = maybe_send_trade_alert(
        payload,
        dry_run=not send,
        test_mode=True,
    )
    state_after = _file_snapshot(STATE_PATH)
    log_after = _file_snapshot(LOG_PATH)
    state_unchanged = state_before == state_after and log_before == log_after

    checks = _validate_case(
        side,
        payload,
        message,
        result,
        send=send,
        state_unchanged=state_unchanged,
    )

    print(f"\n===== {side} {'TELEGRAM TEST' if send else 'DRY-RUN'} =====")
    print(
        json.dumps(
            {
                "eligible": evaluation["eligible"],
                "reason": evaluation["reason"],
                "symbol": evaluation["symbol"],
                "side": evaluation["side"],
                "confidence": evaluation["confidence"],
                "risk_guard_verdict": evaluation["risk_guard_verdict"],
                "message_length": len(message),
                "telegram_attempted": send,
                "telegram_ok": result.get("telegram_ok") if send else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n" + message)
    print("\n검증 결과")
    for label, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")

    if send:
        print(
            "\nTelegram 테스트 전송: "
            + ("성공" if result.get("sent") else f"실패 ({result.get('reason')})")
        )
    return all(passed for _, passed in checks)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LONG/SHORT 상세 진입 심사 알림을 production 상태와 분리해 검증합니다."
    )
    parser.add_argument(
        "--side",
        choices=("long", "short", "both"),
        default="both",
        help="검증할 가상 payload 방향입니다.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="명시한 경우에만 [테스트] Telegram 메시지를 전송합니다.",
    )
    args = parser.parse_args()

    sides = ["LONG", "SHORT"] if args.side == "both" else [args.side.upper()]
    results = [_run_case(side, send=args.send) for side in sides]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

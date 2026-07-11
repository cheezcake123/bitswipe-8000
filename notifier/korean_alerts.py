from __future__ import annotations

from typing import Any, Dict


_DISPLAY_ENUMS: Dict[str, Dict[str, str]] = {
    "asset_class": {
        "crypto": "암호화폐",
        "tradfi": "전통 금융",
    },
    "event_type": {
        "structure_watch": "가격 구조 관찰",
        "near_support_watch": "최근 지지선 근접 관찰",
        "near_resistance_watch": "최근 저항선 근접 관찰",
        "reversal_watch_near_support": "지지선 부근 반전 관찰",
        "resistance_decision_watch": "저항선 돌파·거절 관찰",
        "strong_move_pullback_watch": "강한 움직임 뒤 눌림 관찰",
    },
    "direction": {
        "two_way": "양방향 관찰",
        "bullish_watch": "상방 관찰",
        "bearish_watch": "하방 관찰",
    },
    "trend": {
        "uptrend": "상승 추세",
        "downtrend": "하락 추세",
        "mixed": "혼조",
        "unknown": "확인 불가",
    },
    "reason": {
        "near_recent_support": "최근 지지선 근접",
        "near_recent_resistance": "최근 저항선 근접",
        "possible_reversal_near_support": "지지선 부근 반전 가능성",
        "possible_rejection_or_breakout_near_resistance": "저항선 돌파·거절 분기점",
        "strong_1h_move_pullback_needed": "1시간 강한 움직임 뒤 눌림 필요",
        "strong_4h_move": "4시간 강한 움직임",
        "volume_spike": "거래량 급증",
        "volume_expansion": "거래량 확대",
        "uptrend_context": "상승 추세 맥락",
        "downtrend_context": "하락 추세 맥락",
    },
    "confirmation": {
        "wait for candle close confirmation and a clean retest. not entry.": (
            "캔들 종가 확인과 명확한 리테스트를 기다립니다. 진입 신호가 아닙니다."
        ),
        "watch for either support hold or breakdown. confirmation needed.": (
            "지지 유지 또는 이탈을 관찰하고 추가 확인을 기다립니다."
        ),
        "watch for breakout close or rejection. confirmation needed.": (
            "돌파 종가 또는 저항 거절을 확인할 때까지 기다립니다."
        ),
        "strong move detected; wait for pullback/retest. not entry.": (
            "강한 움직임 뒤 눌림과 리테스트를 기다립니다. 진입 신호가 아닙니다."
        ),
    },
}


def display_alert_value(
    category: str,
    value: Any,
    *,
    fallback: str = "정보 없음",
) -> str:
    """Translate one known display value without mutating internal enum data."""
    raw = str(value or "").strip()
    if not raw:
        return fallback
    mapping = _DISPLAY_ENUMS.get(str(category), {})
    return mapping.get(raw.lower(), raw)

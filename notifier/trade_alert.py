
import json

import os

import time
from collections import deque

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Dict, List



from notifier.telegram_notifier import send_telegram_message





DATA_DIR = Path(__file__).resolve().parents[1] / "data"

STATE_PATH = DATA_DIR / "telegram_alert_state.json"

LOG_PATH = DATA_DIR / "telegram_alert_log.jsonl"

FINAL_VERDICT_LOG_PATH = DATA_DIR.parent / "logs" / "final_verdicts.jsonl"

DEFAULT_COOLDOWN_SECONDS = 3600





def _now_iso() -> str:

    return datetime.now(timezone.utc).isoformat()





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





def _side(signal: Any):

    s = str(signal or "").strip().lower()



    if s in ["buy", "long", "maesu", "\ub9e4\uc218"]:

        return "LONG"



    if s in ["sell", "short", "maedo", "\ub9e4\ub3c4"]:

        return "SHORT"



    return None





def _safe_float(value: Any):

    try:

        if value is None:

            return None

        return float(value)

    except Exception:

        return None





def _rounded(value: Any, digits: int = 2) -> str:

    n = _safe_float(value)

    if n is None:

        return "NA"

    return f"{n:.{digits}f}"





def _cooldown_seconds() -> int:

    raw = os.getenv("TELEGRAM_ALERT_COOLDOWN_SECONDS")

    try:

        if raw:

            return max(60, int(raw))

    except Exception:

        pass

    return DEFAULT_COOLDOWN_SECONDS





def _load_state() -> Dict[str, Any]:

    try:

        if not STATE_PATH.exists():

            return {}

        return json.loads(STATE_PATH.read_text())

    except Exception:

        return {}





def _save_state(state: Dict[str, Any]) -> None:

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2))





def _append_log(row: Dict[str, Any]) -> None:

    try:

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        row = dict(row)

        row["ts"] = _now_iso()

        with LOG_PATH.open("a") as f:

            f.write(json.dumps(row, ensure_ascii=True) + "\n")

    except Exception:

        pass





def get_recent_alert_logs(limit: int = 20) -> List[Dict[str, Any]]:

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





def _alert_signature(payload: Dict[str, Any], side: str) -> str:

    guard = payload.get("risk_guard") or {}

    symbol = payload.get("symbol") or payload.get("pair_label") or "UNKNOWN"



    parts = [

        str(symbol),

        side,

        _rounded(guard.get("entry_price")),

        _rounded(guard.get("stop_price")),

        _rounded(guard.get("target_price")),

    ]



    return "|".join(parts)





def _should_suppress_duplicate(signature: str) -> Dict[str, Any]:

    state = _load_state()

    now = time.time()

    cooldown = _cooldown_seconds()



    last_sent_at = float(state.get(signature, 0) or 0)

    elapsed = now - last_sent_at



    if last_sent_at > 0 and elapsed < cooldown:

        return {

            "suppress": True,

            "reason": "duplicate_suppressed",

            "elapsed_seconds": round(elapsed, 1),

            "cooldown_seconds": cooldown,

        }



    return {

        "suppress": False,

        "cooldown_seconds": cooldown,

    }





def _mark_sent(signature: str) -> None:

    state = _load_state()

    state[signature] = time.time()



    if len(state) > 200:

        items = sorted(state.items(), key=lambda x: x[1], reverse=True)[:200]

        state = dict(items)



    _save_state(state)








def _float_or_none(value: Any):

    try:

        if value is None:

            return None

        return float(value)

    except Exception:

        return None





def _normalize_backtest_symbol(value: Any) -> str:

    text = str(value or "").strip().upper()

    text = text.replace("/", "").replace("-", "").replace("_", "")

    return text





def _append_final_verdict_log(payload: Dict[str, Any], result: Dict[str, Any]) -> None:

    """

    Append final risk-guard verdict data for offline validation/backtesting.

    This never affects Telegram sending.

    """

    try:

        if not isinstance(payload, dict) or not payload:

            return



        guard = payload.get("risk_guard") or {}



        raw_symbol = (

            result.get("symbol")

            or payload.get("pair_label")

            or payload.get("symbol")

            or "UNKNOWN"

        )

        symbol = _normalize_backtest_symbol(raw_symbol)



        side = result.get("side") or _side(payload.get("signal"))



        confidence = result.get("confidence")

        if confidence is None:

            confidence = _float_or_none(payload.get("confidence"))



        sent = bool(result.get("sent"))



        row = {

            "ts": _now_iso(),

            "source": "trade_alert",

            "symbol": symbol,

            "display_symbol": raw_symbol,

            "market_type": "BINANCE" if symbol.endswith("USDT") else "UNKNOWN",



            "direction": side,

            "decision": "FINAL_ALERT_SENT" if sent else "FINAL_SKIPPED",

            "grade": None,

            "rule_score": None,

            "confidence": confidence,



            "entry": _float_or_none(guard.get("entry_price")),

            "stop": _float_or_none(guard.get("stop_price")),

            "target": _float_or_none(guard.get("target_price")),

            "rr": _float_or_none(guard.get("risk_reward_ratio")),



            "risk_verdict": guard.get("verdict"),

            "worth_taking": guard.get("worth_taking"),

            "stop_distance_percent": _float_or_none(guard.get("stop_distance_percent")),

            "leverage": _float_or_none(guard.get("leverage")),

            "max_position_percent_by_account_risk": _float_or_none(

                guard.get("max_position_percent_by_account_risk")

            ),



            "sent": sent,

            "alert_sent": sent,

            "reason": result.get("reason"),

            "telegram_ok": result.get("telegram_ok"),

            "signature": result.get("signature"),

        }



        FINAL_VERDICT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)



        with FINAL_VERDICT_LOG_PATH.open("a", encoding="utf-8") as f:

            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    except Exception:

        return


def maybe_send_trade_alert(payload: Dict[str, Any]) -> Dict[str, Any]:

    try:

        symbol = "UNKNOWN"

        side = None

        signature = None



        if not payload:

            result = {"sent": False, "reason": "no_payload"}

            _append_log(result)

            _append_final_verdict_log(payload, result)
            return result



        guard = payload.get("risk_guard") or {}

        verdict = str(guard.get("verdict") or "").upper()



        symbol = payload.get("pair_label") or payload.get("symbol") or "UNKNOWN"



        if verdict != "PASS":

            result = {

                "sent": False,

                "reason": f"risk_guard_not_pass:{verdict}",

                "symbol": symbol,

                "verdict": verdict,

            }

            _append_log(result)

            _append_final_verdict_log(payload, result)
            return result



        side = _side(payload.get("signal"))

        if not side:

            result = {

                "sent": False,

                "reason": "signal_not_buy_or_sell",

                "symbol": symbol,

                "verdict": verdict,

            }

            _append_log(result)

            _append_final_verdict_log(payload, result)
            return result



        try:

            confidence = float(payload.get("confidence") or 0)

        except Exception:

            confidence = 0.0



        if confidence < 75:

            result = {

                "sent": False,

                "reason": f"confidence_too_low:{confidence}",

                "symbol": symbol,

                "side": side,

                "confidence": confidence,

                "verdict": verdict,

            }

            _append_log(result)

            _append_final_verdict_log(payload, result)
            return result



        signature = _alert_signature(payload, side)

        dup = _should_suppress_duplicate(signature)



        if dup.get("suppress"):

            result = {

                "sent": False,

                "reason": dup.get("reason"),

                "symbol": symbol,

                "side": side,

                "confidence": confidence,

                "verdict": verdict,

                "signature": signature,

                "elapsed_seconds": dup.get("elapsed_seconds"),

                "cooldown_seconds": dup.get("cooldown_seconds"),

            }

            _append_log(result)

            _append_final_verdict_log(payload, result)
            return result



        message = (

            "BitSwipe A-grade candidate\n\n"

            f"{symbol} / {side}\n"

            f"Confidence: {confidence:.0f}%\n"

            f"Current price: {_price(payload.get('price'))}\n\n"

            f"Entry: {_price(guard.get('entry_price'))}\n"

            f"Stop: {_price(guard.get('stop_price'))}\n"

            f"Target: {_price(guard.get('target_price'))}\n\n"

            f"Risk reward: {_num(guard.get('risk_reward_ratio'), 2)}\n"

            f"Stop distance: {_num(guard.get('stop_distance_percent'), 2)}%\n"

            f"Leverage: {_num(guard.get('leverage'), 1)}x\n"

            f"Leveraged loss: {_num(guard.get('leveraged_loss_percent_on_margin'), 2)}%\n"

            f"Max position for 1pct account risk: {_num(guard.get('max_position_percent_by_account_risk'), 2)}%\n\n"

            "Verdict: Risk Guard PASS\n"

            "Do not chase immediately. Re-check candle close and stop location."

        )



        telegram_result = send_telegram_message(message)



        if telegram_result.get("ok"):

            _mark_sent(signature)



        result = {

            "sent": bool(telegram_result.get("ok")),

            "reason": "sent" if telegram_result.get("ok") else "telegram_send_failed",

            "symbol": symbol,

            "side": side,

            "confidence": confidence,

            "verdict": verdict,

            "signature": signature,

            "telegram_ok": bool(telegram_result.get("ok")),

        }



        _append_log(result)



        _append_final_verdict_log(payload, result)
        return {

            **result,

            "telegram_result": telegram_result,

        }



    except Exception as exc:

        result = {

            "sent": False,

            "reason": f"alert_error:{exc}",

            "symbol": symbol,

            "side": side,

            "signature": signature,

        }

        _append_log(result)

        _append_final_verdict_log(payload, result)
        return result


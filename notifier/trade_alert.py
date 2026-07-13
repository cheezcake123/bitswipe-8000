
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



        side_ko = {

            "LONG": "\ub871",

            "SHORT": "\uc20f",

        }.get(str(side).upper(), str(side))



        analysis_json = payload.get("analysis_json") or {}

        if not isinstance(analysis_json, dict):

            analysis_json = {}



        report_sections = payload.get("report_sections") or {}

        if not isinstance(report_sections, dict):

            report_sections = {}



        levels = analysis_json.get("levels") or {}

        if not isinstance(levels, dict):

            levels = {}



        trade = analysis_json.get("trade") or {}

        if not isinstance(trade, dict):

            trade = {}



        actions = analysis_json.get("actions") or {}

        if not isinstance(actions, dict):

            actions = {}



        def _text(value, default="-"):

            value = str(value or "").strip()

            return value if value else default



        def _items(value, limit=2):

            if isinstance(value, list):

                return [

                    str(item).strip()

                    for item in value

                    if str(item).strip()

                ][:limit]

            if isinstance(value, str) and value.strip():

                return [value.strip()]

            return []



        view = _text(

            report_sections.get("view")

            or analysis_json.get("view"),

            side_ko,

        )



        regime = _text(

            report_sections.get("regime")

            or analysis_json.get("regime"),

            "\ud655\uc778 \ud544\uc694",

        )



        summary = _text(

            report_sections.get("summary")

            or analysis_json.get("summary"),

            "\ub9ac\uc2a4\ud06c \uc2ec\uc0ac\ub97c \ud1b5\uacfc\ud55c \uc870\uac74\ubd80 \uc9c4\uc785 \ud6c4\ubcf4\uc785\ub2c8\ub2e4.",

        )



        invalidation_text = _text(

            report_sections.get("invalidation")

            or analysis_json.get("invalidation"),

            "\uc190\uc808\uac00 \uc774\ud0c8 \uc2dc \uae30\uc874 \uc2dc\ub098\ub9ac\uc624\ub97c \ud3d0\uae30\ud569\ub2c8\ub2e4.",

        )



        facts = _items(

            report_sections.get("facts")

            or analysis_json.get("key_facts"),

            2,

        )



        interpretations = _items(

            report_sections.get("interpretation")

            or analysis_json.get("inferences"),

            2,

        )



        counter_scenarios = _items(

            report_sections.get("counter_scenario")

            or analysis_json.get("counter_scenario"),

            2,

        )



        aggressive = _text(

            actions.get("aggressive"),

            "\ud655\uc778\ubd09 \ub9c8\uac10 \ud6c4 \ucd08\uc18c\uc561\uc73c\ub85c\ub9cc \uac80\ud1a0",

        )



        conservative = _text(

            actions.get("conservative"),

            "\ub3cc\ud30c \ub610\ub294 \uc774\ud0c8 \ud6c4 \uc7ac\ud14c\uc2a4\ud2b8 \ud655\uc778 \uc804\uae4c\uc9c0 \uad00\ub9dd",

        )



        entry_num = _safe_float(guard.get("entry_price"))

        stop_num = _safe_float(guard.get("stop_price"))

        target_2_num = _safe_float(guard.get("target_price"))



        target_1_num = None

        if entry_num is not None and stop_num is not None:

            risk_distance = abs(entry_num - stop_num)



            if side == "LONG":

                target_1_num = entry_num + risk_distance

            elif side == "SHORT":

                target_1_num = entry_num - risk_distance



        if side == "LONG":

            trigger_price = (

                levels.get("bull_trigger")

                if levels.get("bull_trigger") is not None

                else levels.get("resistance")

            )

            trigger_text = (

                f"{_price(trigger_price)} \uc704\uc5d0\uc11c 15\ubd84\ubd09 \uc885\uac00 \ud655\uc815 "

                "\ud6c4 \uc7ac\ud14c\uc2a4\ud2b8 \uc9c0\uc9c0 \ud655\uc778"

            )

        else:

            trigger_price = (

                levels.get("bear_trigger")

                if levels.get("bear_trigger") is not None

                else levels.get("support")

            )

            trigger_text = (

                f"{_price(trigger_price)} \uc544\ub798\uc5d0\uc11c 15\ubd84\ubd09 \uc885\uac00 \ud655\uc815 "

                "\ud6c4 \ubc18\ub4f1 \uc7ac\ud14c\uc2a4\ud2b8 \uc800\ud56d \ud655\uc778"

            )



        message_lines = [

            "\U0001f7e2 [BitSwipe \uc9c4\uc785 \uc2ec\uc0ac \ubcf4\uace0\uc11c]",

            "",

            f"\uc885\ubaa9: {symbol}",

            "\ucd5c\uc885 \ud310\uc815: \ub9ac\uc2a4\ud06c \uc2ec\uc0ac \ud1b5\uacfc \u00b7 \uc870\uac74\ubd80 \uc218\ub3d9 \uac80\ud1a0",

            f"\ubc29\ud5a5: {side_ko}",

            f"\uc2e0\ub8b0\ub3c4: {confidence:.0f}%",

            f"\uc2dc\uc7a5 \uad00\uc810: {view}",

            f"\uc2dc\uc7a5 \uad6d\uba74: {regime}",

            "",

            "\U0001f4cc \ud604\uc7ac \uc0c1\ud669",

            f"- {summary}",

        ]



        for item in facts:

            message_lines.append(f"- {item}")



        for item in interpretations:

            message_lines.append(f"- {item}")



        message_lines.extend([

            "",

            "\U0001f3af \uc9c4\uc785 \uc2dc\ub098\ub9ac\uc624",

            f"1) {trigger_text}",

            f"2) \uacf5\uaca9\uc801 \ub300\uc751: {aggressive}",

            f"3) \ubcf4\uc218\uc801 \ub300\uc751: {conservative}",

            "",

            "\U0001f4b0 \uac00\uaca9 \uacc4\ud68d",

            f"- \ud604\uc7ac\uac00: {_price(payload.get('price'))}",

            f"- \uc9c4\uc785\uac00: {_price(entry_num)}",

            f"- \uc190\uc808\uac00: {_price(stop_num)}",

            f"- 1\ucc28 \ubaa9\ud45c(1R): {_price(target_1_num)}",

            f"- 2\ucc28 \ubaa9\ud45c(AI \ucd5c\uc885 \ubaa9\ud45c): {_price(target_2_num)}",

            f"- \uc608\uc0c1 \uc190\uc775\ube44: {_num(guard.get('risk_reward_ratio'), 2)}:1",

            "",

            "\U0001f6e1 \uc704\ud5d8 \uad00\ub9ac",

            f"- \uc190\uc808 \uac70\ub9ac: {_num(guard.get('stop_distance_percent'), 2)}%",

            f"- \uac80\ud1a0 \ub808\ubc84\ub9ac\uc9c0: \uaca9\ub9ac {_num(guard.get('leverage'), 1)}\ubc30",

            f"- \ub808\ubc84\ub9ac\uc9c0 \ubc18\uc601 \uc608\uc0c1 \uc190\uc2e4: {_num(guard.get('leveraged_loss_percent_on_margin'), 2)}%",

            f"- \uacc4\uc88c 1% \uc704\ud5d8 \uae30\uc900 \ucd5c\ub300 \uba85\ubaa9 \ud3ec\uc9c0\uc158: "

            f"\uacc4\uc88c\uc758 {_num(guard.get('max_position_percent_by_account_risk'), 2)}%",

            "",

            "\U0001f4cb \ud3ec\uc9c0\uc158 \uad00\ub9ac",

            "- 1\ucc28 \ubaa9\ud45c\uc5d0\uc11c \uc77c\ubd80 \uc775\uc808",

            "- 1R \ub3c4\ub2ec \ud6c4 \uc190\uc808\uac00\ub97c \uc9c4\uc785\uac00 \ub610\ub294 \ubcf8\uc808 \uadfc\ucc98\ub85c \uc870\uc815",

            "- \ubb3c\ud0c0\uae30, \uc190\uc808 \ud655\ub300, \ucd94\uaca9 \uc9c4\uc785 \uae08\uc9c0",

            "",

            "\u274c \uc2dc\ub098\ub9ac\uc624 \ud3d0\uae30 \uc870\uac74",

            f"- {invalidation_text}",

        ])



        for item in counter_scenarios:

            message_lines.append(f"- {item}")



        message_lines.extend([

            "",

            "\u26a0\ufe0f \uc790\ub3d9 \uc8fc\ubb38 \uc2e0\ud638\uac00 \uc544\ub2d9\ub2c8\ub2e4.",

            "\ud655\uc778\ubd09, \uc9c4\uc785\uac00, \uc190\uc808\uac00, \uc190\uc775\ube44\ub97c \uc7ac\ud655\uc778\ud55c \ud6c4 \uc0ac\uc6a9\uc790\uac00 \ucd5c\uc885 \uc2b9\uc778\ud558\uc138\uc694.",

        ])



        message = "\n".join(message_lines)



        if len(message) > 3900:

            message = (

                message[:3820].rstrip()

                + "\n\n\u203b \ud154\ub808\uadf8\ub7a8 \uae38\uc774 \uc81c\ud55c\uc73c\ub85c \uc77c\ubd80 \uc124\uba85\uc744 \uc904\uc600\uc2b5\ub2c8\ub2e4."

            )



        registration_plan = {
            "symbol": symbol,
            "direction": side,
            "entry": entry_num,
            "stop": stop_num,
            "target_1": target_1_num,
            "target_2": target_2_num,
            "rr": _safe_float(guard.get("risk_reward_ratio")),
            "confidence": confidence,
            "signature": signature,
            "risk_verdict": verdict,
        }

        telegram_result = send_telegram_message(
            message,
            registration_plan=registration_plan,
        )



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


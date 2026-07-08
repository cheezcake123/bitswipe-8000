
# -*- coding: utf-8 -*-

import os

import sys

import json

import time

import math

import urllib.error

import urllib.request

from pathlib import Path

from datetime import datetime, timezone



import requests



ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))



try:

    from scripts.ai_budget_guard import can_run_auto_ai, mark_auto_ai_run

except Exception:

    can_run_auto_ai = None

    mark_auto_ai_run = None




try:

    from candidate_logger import append_candidate_log

except Exception:

    def append_candidate_log(candidate):

        return




LOG_PATH = ROOT / "logs" / "watchlist_scan.log"

STATE_PATH = ROOT / "logs" / "watchlist_ai_state.json"



BINANCE_SYMBOLS = [

    "BTCUSDT",

    "ETHUSDT",

    "SOLUSDT",

    "XRPUSDT",

    "DOGEUSDT",

    "BNBUSDT",

    "ADAUSDT",

    "AVAXUSDT",

    "LINKUSDT",

    "WLDUSDT",

    "ZECUSDT",

]



ETF_SYMBOLS = [

    "SOXL",

    "QQQ",

    "EWY",

]



INTERVAL = "15m"

LIMIT = 80



MIN_GRADE_TO_ANALYZE = "B+"

MIN_SCORE_TO_ANALYZE = 70



MAX_AI_CALLS_PER_RUN = 2

SYMBOL_COOLDOWN_SECONDS = 60 * 60 * 2



ANALYZE_TIMEOUT_SECONDS = 60



# Candidate scoring thresholds

MOVE_15M_WATCH = 0.70

MOVE_15M_STRONG = 1.10

MOVE_4H_WATCH = 1.50

MOVE_4H_STRONG = 2.50

VOL_WATCH = 1.70

VOL_STRONG = 2.50

NEAR_LEVEL_PCT = 0.45

NEAR_LEVEL_LOOSE_PCT = 0.75

MIN_ESTIMATED_RR = 1.20



def utc_now():

    return datetime.now(timezone.utc)



def now_str():

    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")



def write_log(msg):

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOG_PATH.open("a", encoding="utf-8") as f:

        f.write(f"[{now_str()}] {msg}\n")



def compact(obj, limit=1600):

    try:

        return json.dumps(obj, ensure_ascii=True, default=str)[:limit]

    except Exception:

        return str(obj)[:limit]



def load_env_file():

    env_path = ROOT / ".env"

    if not env_path.exists():

        return

    text = env_path.read_text(encoding="utf-8", errors="ignore")

    for line in text.splitlines():

        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:

            continue

        k, v = line.split("=", 1)

        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))



def load_state():

    if not STATE_PATH.exists():

        return {"last_ai_by_symbol": {}}

    try:

        return json.loads(STATE_PATH.read_text(encoding="utf-8"))

    except Exception:

        return {"last_ai_by_symbol": {}}



def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")



def is_symbol_on_cooldown(symbol, state):

    last_map = state.get("last_ai_by_symbol", {})

    last_ts = float(last_map.get(symbol, 0) or 0)

    age = time.time() - last_ts

    return age < SYMBOL_COOLDOWN_SECONDS, int(SYMBOL_COOLDOWN_SECONDS - age)



def mark_symbol_ai(symbol, state):

    state.setdefault("last_ai_by_symbol", {})[symbol] = time.time()

    save_state(state)



def send_telegram(text):

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:

        write_log("TELEGRAM_SKIP missing token or chat_id")

        return False



    try:

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        r = requests.post(

            url,

            data={

                "chat_id": chat_id,

                "text": text,

                "disable_web_page_preview": True,

            },

            timeout=12,

        )

        write_log(f"TELEGRAM_RESULT status={r.status_code} body={r.text[:500]}")

        return r.status_code == 200

    except Exception as e:

        write_log(f"TELEGRAM_ERROR {type(e).__name__}: {e}")

        return False



def fetch_binance_klines(symbol):

    url = "https://fapi.binance.com/fapi/v1/klines"

    r = requests.get(

        url,

        params={"symbol": symbol, "interval": INTERVAL, "limit": LIMIT},

        timeout=12,

    )



    if r.status_code != 200:

        raise RuntimeError(f"Binance status={r.status_code} body={r.text[:300]}")



    data = r.json()

    if not isinstance(data, list) or len(data) < 40:

        raise RuntimeError(f"Not enough candles: {compact(data, 300)}")

    return data



def fetch_etf_klines(symbol):

    try:

        import yfinance as yf

    except Exception as e:

        raise RuntimeError(f"yfinance import failed: {e}")



    df = yf.download(

        symbol,

        period="7d",

        interval="15m",

        progress=False,

        auto_adjust=False,

        threads=False,

    )



    if df is None or len(df) < 40:

        raise RuntimeError(f"Not enough yfinance rows: {len(df) if df is not None else 'none'}")



    close_data = df["Close"]

    high_data = df["High"]

    low_data = df["Low"]

    volume_data = df["Volume"]



    if hasattr(close_data, "columns"):

        close_data = close_data.iloc[:, 0]

    if hasattr(high_data, "columns"):

        high_data = high_data.iloc[:, 0]

    if hasattr(low_data, "columns"):

        low_data = low_data.iloc[:, 0]

    if hasattr(volume_data, "columns"):

        volume_data = volume_data.iloc[:, 0]



    rows = []

    idx = df.tail(LIMIT).index

    closes = close_data.tail(LIMIT).values

    highs = high_data.tail(LIMIT).values

    lows = low_data.tail(LIMIT).values

    vols = volume_data.tail(LIMIT).values



    for i in range(len(closes)):

        rows.append({

            "open": float(closes[i]),

            "high": float(highs[i]),

            "low": float(lows[i]),

            "close": float(closes[i]),

            "volume": float(vols[i]),

        })



    if len(rows) < 40:

        raise RuntimeError("Not enough ETF converted candles")



    return rows



def normalize_binance_klines(klines):

    rows = []

    for k in klines:

        rows.append({

            "open": float(k[1]),

            "high": float(k[2]),

            "low": float(k[3]),

            "close": float(k[4]),

            "volume": float(k[5]),

        })

    return rows



def avg(values):

    values = list(values)

    if not values:

        return 0.0

    return sum(values) / len(values)



def clamp(x, lo, hi):

    return max(lo, min(hi, x))



def pct(a, b):

    if b == 0:

        return 0.0

    return (a / b - 1.0) * 100.0



def estimated_rr(direction, entry, support, resistance, atr_pct):

    if entry <= 0:

        return 0.0



    atr_abs = entry * max(atr_pct, 0.25) / 100.0



    if direction == "LONG":

        stop = min(support * 0.996, entry - atr_abs * 0.8)

        target = max(resistance, entry + atr_abs * 2.0)

        risk = entry - stop

        reward = target - entry

    elif direction == "SHORT":

        stop = max(resistance * 1.004, entry + atr_abs * 0.8)

        target = min(support, entry - atr_abs * 2.0)

        risk = stop - entry

        reward = entry - target

    else:

        return 0.0



    if risk <= 0:

        return 0.0

    return reward / risk



def grade_from_score(score):

    if score >= 85:

        return "A"

    if score >= 78:

        return "A-"

    if score >= 70:

        return "B+"

    if score >= 62:

        return "B"

    return "C"



def score_candidate(symbol, rows, market_type):

    closes = [r["close"] for r in rows]

    highs = [r["high"] for r in rows]

    lows = [r["low"] for r in rows]

    vols = [r["volume"] for r in rows]



    last = closes[-1]

    prev = closes[-2]

    c4 = closes[-5] if len(closes) >= 5 else closes[0]

    c16 = closes[-17] if len(closes) >= 17 else closes[0]

    c48 = closes[-49] if len(closes) >= 49 else closes[0]



    move_15m = pct(last, prev)

    move_1h = pct(last, c4)

    move_4h = pct(last, c16)

    move_12h = pct(last, c48)



    recent_highs = highs[-32:]

    recent_lows = lows[-32:]

    support = min(recent_lows)

    resistance = max(recent_highs)



    dist_support = abs(last / support - 1.0) * 100.0 if support else 999.0

    dist_resistance = abs(resistance / last - 1.0) * 100.0 if last else 999.0



    recent_vol = vols[-1]

    base_vols = vols[-41:-1]

    avg_vol = avg(base_vols)

    vol_spike = recent_vol / avg_vol if avg_vol > 0 else 0.0



    ranges_pct = []

    for r in rows[-20:]:

        if r["close"] > 0:

            ranges_pct.append((r["high"] - r["low"]) / r["close"] * 100.0)

    atr_pct = avg(ranges_pct)



    last_range = highs[-1] - lows[-1]

    body = abs(closes[-1] - rows[-1]["open"])

    body_ratio = body / last_range if last_range > 0 else 0.0



    near_support = dist_support <= NEAR_LEVEL_PCT

    near_resistance = dist_resistance <= NEAR_LEVEL_PCT

    loose_near_support = dist_support <= NEAR_LEVEL_LOOSE_PCT

    loose_near_resistance = dist_resistance <= NEAR_LEVEL_LOOSE_PCT



    breakout_up = last >= resistance * 0.998 and move_15m > 0 and vol_spike >= 1.5

    breakdown_down = last <= support * 1.002 and move_15m < 0 and vol_spike >= 1.5



    score = 0

    reasons = []



    if abs(move_15m) >= MOVE_15M_STRONG:

        score += 18

        reasons.append(f"strong_15m_move {move_15m:+.2f}%")

    elif abs(move_15m) >= MOVE_15M_WATCH:

        score += 10

        reasons.append(f"15m_move {move_15m:+.2f}%")



    if abs(move_4h) >= MOVE_4H_STRONG:

        score += 20

        reasons.append(f"strong_4h_move {move_4h:+.2f}%")

    elif abs(move_4h) >= MOVE_4H_WATCH:

        score += 12

        reasons.append(f"4h_move {move_4h:+.2f}%")



    if vol_spike >= VOL_STRONG:

        score += 22

        reasons.append(f"strong_volume x{vol_spike:.2f}")

    elif vol_spike >= VOL_WATCH:

        score += 14

        reasons.append(f"volume x{vol_spike:.2f}")



    if near_support:

        score += 16

        reasons.append(f"near_support {support:.6g}")

    elif loose_near_support:

        score += 8

        reasons.append(f"loose_near_support {support:.6g}")



    if near_resistance:

        score += 16

        reasons.append(f"near_resistance {resistance:.6g}")

    elif loose_near_resistance:

        score += 8

        reasons.append(f"loose_near_resistance {resistance:.6g}")



    if breakout_up:

        score += 18

        reasons.append("breakout_up_watch")



    if breakdown_down:

        score += 18

        reasons.append("breakdown_down_watch")



    if body_ratio >= 0.60 and vol_spike >= 1.4:

        score += 8

        reasons.append(f"clean_candle_body {body_ratio:.2f}")



    trend_up = move_1h > 0 and move_4h > 0

    trend_down = move_1h < 0 and move_4h < 0



    if trend_up and move_12h >= 0:

        score += 6

        reasons.append("multi_tf_up")

    elif trend_down and move_12h <= 0:

        score += 6

        reasons.append("multi_tf_down")



    direction = "WAIT"

    if breakout_up or (loose_near_support and move_15m > 0 and vol_spike >= 1.4) or (trend_up and vol_spike >= 1.7):

        direction = "LONG"

    elif breakdown_down or (loose_near_resistance and move_15m < 0 and vol_spike >= 1.4) or (trend_down and vol_spike >= 1.7):

        direction = "SHORT"

    else:

        if move_4h > 0 and move_15m > 0 and vol_spike >= 1.7:

            direction = "LONG"

        elif move_4h < 0 and move_15m < 0 and vol_spike >= 1.7:

            direction = "SHORT"



    rr = estimated_rr(direction, last, support, resistance, atr_pct)



    if direction != "WAIT":

        score += 8

        reasons.append(f"directional_bias {direction}")



    if rr >= 2.0:

        score += 12

        reasons.append(f"estimated_rr {rr:.2f}")

    elif rr >= MIN_ESTIMATED_RR:

        score += 7

        reasons.append(f"estimated_rr {rr:.2f}")

    else:

        score -= 12

        reasons.append(f"low_estimated_rr {rr:.2f}")



    # ETF data can spike around session boundaries. Require slightly stronger score.

    if market_type == "ETF":

        if vol_spike >= VOL_STRONG or abs(move_15m) >= MOVE_15M_STRONG:

            pass

        else:

            score -= 8

            reasons.append("etf_noise_penalty")



    score = int(clamp(score, 0, 100))

    grade = grade_from_score(score)



    should_analyze = score >= MIN_SCORE_TO_ANALYZE and direction != "WAIT" and rr >= MIN_ESTIMATED_RR



    return {

        "symbol": symbol,

        "market_type": market_type,

        "score": score,

        "grade": grade,

        "should_analyze": should_analyze,

        "direction": direction,

        "last": last,

        "support": support,

        "resistance": resistance,

        "move_15m": move_15m,

        "move_1h": move_1h,

        "move_4h": move_4h,

        "move_12h": move_12h,

        "vol_spike": vol_spike,

        "atr_pct": atr_pct,

        "estimated_rr": rr,

        "reasons": reasons,

    }



def call_ai_analyze(symbol):

    url = f"http://127.0.0.1:8000/api/analyze?symbol={symbol}"

    req = urllib.request.Request(

        url,

        data=b"",

        method="POST",

        headers={"Content-Type": "application/json"},

    )



    try:

        with urllib.request.urlopen(req, timeout=ANALYZE_TIMEOUT_SECONDS) as res:

            body = res.read().decode("utf-8", errors="replace")

            try:

                data = json.loads(body)

            except Exception:

                data = {"raw": body[:1000]}



            started = bool(data.get("started", True))

            return {

                "ok": True,

                "status": res.status,

                "started": started,

                "data": data,

            }



    except urllib.error.HTTPError as exc:

        body = exc.read().decode("utf-8", errors="replace")

        return {

            "ok": False,

            "status": exc.code,

            "error": body[:1000],

        }

    except Exception as exc:

        return {

            "ok": False,

            "status": None,

            "error": f"{type(exc).__name__}: {exc}",

        }



def budget_allowed():

    if can_run_auto_ai is None:

        return {"allowed": True, "note": "ai_budget_guard_unavailable"}



    try:

        return can_run_auto_ai()

    except Exception as e:

        return {"allowed": False, "error": f"budget_guard_error {type(e).__name__}: {e}"}



def mark_budget_run():

    if mark_auto_ai_run is None:

        return {"marked": False, "note": "ai_budget_guard_unavailable"}



    try:

        return mark_auto_ai_run()

    except Exception as e:

        return {"marked": False, "error": f"{type(e).__name__}: {e}"}



def format_candidate_summary(candidates, selected, skipped_cooldown):

    lines = []

    lines.append("BitSwipe AI candidate scanner")

    lines.append("A/B+ candidates selected for GPT analysis.")

    lines.append("")

    if candidates:

        lines.append("Top candidates:")

        for c in candidates[:5]:

            lines.append(

                f"- {c['symbol']} grade={c['grade']} score={c['score']} "

                f"dir={c['direction']} rr={c['estimated_rr']:.2f} "

                f"15m={c['move_15m']:+.2f}% 4h={c['move_4h']:+.2f}% vol=x{c['vol_spike']:.2f}"

            )

    if selected:

        lines.append("")

        lines.append("AI requested:")

        for c in selected:

            lines.append(f"- {c['symbol']} grade={c['grade']} score={c['score']}")

    if skipped_cooldown:

        lines.append("")

        lines.append("Skipped by cooldown:")

        for item in skipped_cooldown:

            lines.append(f"- {item[0]} wait={item[1]}s")

    lines.append("")

    lines.append("Reminder: AI analysis is not auto-entry. Check stop, RR, and news.")

    return "\n".join(lines)





def get_candidate_blocked_reason(result):

    if result.get("should_analyze"):

        return None



    try:

        if float(result.get("estimated_rr") or 0) < MIN_ESTIMATED_RR:

            return "LOW_ESTIMATED_RR"

    except Exception:

        pass



    if result.get("direction") == "WAIT":

        return "NO_CLEAR_DIRECTION"



    try:

        if int(result.get("score") or 0) < MIN_SCORE_TO_ANALYZE:

            return "LOW_RULE_SCORE"

    except Exception:

        pass



    return "NOT_ELIGIBLE_FOR_AI"





def build_candidate_log_row(result, candidate_symbols=None, selected_symbols=None, alert_sent=False):

    candidate_symbols = candidate_symbols or set()

    selected_symbols = selected_symbols or set()



    symbol = result.get("symbol")

    ai_called = symbol in selected_symbols

    eligible_for_ai = bool(result.get("should_analyze"))



    if ai_called:

        decision = "AI_CALLED"

    elif eligible_for_ai or symbol in candidate_symbols:

        decision = "AI_CANDIDATE"

    elif result.get("direction") == "WAIT":

        decision = "WAIT"

    else:

        decision = "BLOCK"



    reasons = result.get("reasons") or []



    return {

        "ts": datetime.now(timezone.utc).isoformat(),

        "symbol": symbol,

        "market_type": result.get("market_type"),

        "direction": result.get("direction"),

        "decision": decision,

        "grade": result.get("grade"),

        "rule_score": result.get("score"),

        "confidence": None,



        # Entry/stop/target are not produced by this pre-AI scanner yet.

        # They will be filled later by the analyzer/final verdict logging layer.

        "entry": None,

        "stop": None,

        "target": None,



        "last": result.get("last"),

        "rr": result.get("estimated_rr"),

        "support": result.get("support"),

        "resistance": result.get("resistance"),

        "move_15m": result.get("move_15m"),

        "move_1h": result.get("move_1h"),

        "move_4h": result.get("move_4h"),

        "move_12h": result.get("move_12h"),

        "volume_spike": result.get("vol_spike"),

        "atr_pct": result.get("atr_pct"),



        "eligible_for_ai": eligible_for_ai,

        "ai_called": ai_called,

        "alert_sent": bool(alert_sent and ai_called),

        "blocked_reason": get_candidate_blocked_reason(result),

        "reasons": reasons,

        "notes": "; ".join(str(x) for x in reasons),

    }





def log_candidate_batch(all_results, candidates=None, selected=None, alert_sent=False):

    candidates = candidates or []

    selected = selected or []



    candidate_symbols = {c.get("symbol") for c in candidates}

    selected_symbols = {c.get("symbol") for c in selected}



    for result in all_results:

        append_candidate_log(

            build_candidate_log_row(

                result,

                candidate_symbols=candidate_symbols,

                selected_symbols=selected_symbols,

                alert_sent=alert_sent,

            )

        )


def main():

    load_env_file()

    write_log("SCAN_START mode=ai_candidate_filter")



    state = load_state()

    all_results = []

    candidates = []



    for symbol in BINANCE_SYMBOLS:

        try:

            raw = fetch_binance_klines(symbol)

            rows = normalize_binance_klines(raw)

            result = score_candidate(symbol, rows, "BINANCE")

            all_results.append(result)

            write_log("SCORE " + compact(result))

            if result["should_analyze"]:

                candidates.append(result)

        except Exception as e:

            write_log(f"BINANCE_FAIL symbol={symbol} {type(e).__name__}: {e}")



    for symbol in ETF_SYMBOLS:

        try:

            rows = fetch_etf_klines(symbol)

            result = score_candidate(symbol, rows, "ETF")

            all_results.append(result)

            write_log("SCORE " + compact(result))

            if result["should_analyze"]:

                candidates.append(result)

        except Exception as e:

            write_log(f"ETF_FAIL symbol={symbol} {type(e).__name__}: {e}")



    candidates.sort(key=lambda x: (x["score"], x["estimated_rr"], abs(x["move_15m"])), reverse=True)



    if not candidates:

        summary = {

            "checked": len(all_results),

            "candidates": 0,

            "top": [

                {

                    "symbol": r["symbol"],

                    "grade": r["grade"],

                    "score": r["score"],

                    "direction": r["direction"],

                    "rr": round(r["estimated_rr"], 2),

                }

                for r in sorted(all_results, key=lambda x: x["score"], reverse=True)[:5]

            ],

        }

        write_log("NO_AI_CANDIDATE " + compact(summary))



        log_candidate_batch(all_results, candidates=[], selected=[], alert_sent=False)

        write_log("SCAN_DONE")

        return



    write_log("CANDIDATES " + compact([

        {

            "symbol": c["symbol"],

            "grade": c["grade"],

            "score": c["score"],

            "direction": c["direction"],

            "rr": round(c["estimated_rr"], 2),

            "reasons": c["reasons"],

        }

        for c in candidates

    ]))



    selected = []

    skipped_cooldown = []



    for c in candidates:

        if len(selected) >= MAX_AI_CALLS_PER_RUN:

            break



        symbol = c["symbol"]

        on_cd, remain = is_symbol_on_cooldown(symbol, state)

        if on_cd:

            skipped_cooldown.append((symbol, remain))

            write_log(f"SKIP_COOLDOWN symbol={symbol} remain={remain}s")

            continue



        guard = budget_allowed()

        if not guard.get("allowed", False):

            write_log("SKIP_BUDGET " + compact(guard))

            break



        write_log("AI_REQUEST_START " + compact({

            "symbol": symbol,

            "grade": c["grade"],

            "score": c["score"],

            "direction": c["direction"],

            "rr": round(c["estimated_rr"], 2),

            "reasons": c["reasons"],

            "budget": guard,

        }))



        response = call_ai_analyze(symbol)

        write_log("AI_REQUEST_RESULT " + compact({

            "symbol": symbol,

            "response": response,

        }))



        if response.get("ok") and response.get("started", True):

            selected.append(c)

            mark_symbol_ai(symbol, state)

            marked = mark_budget_run()

            write_log("AI_MARKED " + compact({

                "symbol": symbol,

                "marked": marked,

            }))

        else:

            # If server rejected due cooldown/rate limit, do not mark local cooldown.

            write_log("AI_NOT_MARKED " + compact({

                "symbol": symbol,

                "response": response,

            }))



    # Optional light status notice.

    # To avoid duplicate Telegram messages, this is off by default.

    # Enable by setting WATCH_SEND_AI_REQUEST_NOTICE=1 in .env.

    alert_sent = False

    if os.getenv("WATCH_SEND_AI_REQUEST_NOTICE", "0") == "1" and selected:

        send_telegram(format_candidate_summary(candidates, selected, skipped_cooldown))

        alert_sent = True



    log_candidate_batch(all_results, candidates=candidates, selected=selected, alert_sent=alert_sent)

    write_log("SCAN_DONE " + compact({

        "checked": len(all_results),

        "candidates": len(candidates),

        "selected_for_ai": [c["symbol"] for c in selected],

        "cooldown_skipped": skipped_cooldown,

    }))



if __name__ == "__main__":

    main()


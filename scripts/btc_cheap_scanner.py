
import json

import urllib.request

from statistics import mean





BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

SYMBOL = "BTCUSDT"





def _fetch_klines(interval, limit):

    url = f"{BASE_URL}?symbol={SYMBOL}&interval={interval}&limit={limit}"



    with urllib.request.urlopen(url, timeout=10) as res:

        data = json.loads(res.read().decode("utf-8"))



    return data





def _to_candles(raw):

    candles = []



    for row in raw:

        candles.append({

            "open_time": int(row[0]),

            "open": float(row[1]),

            "high": float(row[2]),

            "low": float(row[3]),

            "close": float(row[4]),

            "volume": float(row[5]),

        })



    return candles





def _pct(a, b):

    if b == 0:

        return 0.0

    return ((a - b) / b) * 100.0





def scan_btc():

    try:

        raw_15m = _fetch_klines("15m", 100)

        raw_1h = _fetch_klines("1h", 80)



        candles_15m = _to_candles(raw_15m)[:-1]

        candles_1h = _to_candles(raw_1h)[:-1]



        if len(candles_15m) < 30 or len(candles_1h) < 30:

            return {

                "ok": False,

                "should_run_ai": False,

                "reason": "not_enough_candles",

            }



        last_15m = candles_15m[-1]

        prev_15m = candles_15m[-2]



        close = last_15m["close"]



        change_15m = _pct(last_15m["close"], prev_15m["close"])

        change_1h = _pct(candles_15m[-1]["close"], candles_15m[-5]["close"])

        change_4h = _pct(candles_15m[-1]["close"], candles_15m[-17]["close"])



        recent_volumes = [c["volume"] for c in candles_15m[-21:-1]]

        avg_volume = mean(recent_volumes) if recent_volumes else 0

        volume_ratio = last_15m["volume"] / avg_volume if avg_volume > 0 else 0



        highs_24h = [c["high"] for c in candles_15m[-96:]]

        lows_24h = [c["low"] for c in candles_15m[-96:]]



        high_24h = max(highs_24h)

        low_24h = min(lows_24h)



        distance_from_high_pct = abs(_pct(close, high_24h))

        distance_from_low_pct = abs(_pct(close, low_24h))



        score = 0

        reasons = []



        abs_15m = abs(change_15m)

        abs_1h = abs(change_1h)

        abs_4h = abs(change_4h)



        if abs_15m >= 0.35:

            score += 25

            reasons.append("strong_15m_move")

        elif abs_15m >= 0.20:

            score += 15

            reasons.append("moderate_15m_move")



        if abs_1h >= 0.70:

            score += 25

            reasons.append("strong_1h_move")

        elif abs_1h >= 0.40:

            score += 15

            reasons.append("moderate_1h_move")



        if abs_4h >= 1.20:

            score += 20

            reasons.append("strong_4h_move")

        elif abs_4h >= 0.80:

            score += 10

            reasons.append("moderate_4h_move")



        if volume_ratio >= 1.50:

            score += 20

            reasons.append("volume_spike")

        elif volume_ratio >= 1.20:

            score += 10

            reasons.append("volume_expansion")



        if distance_from_high_pct <= 0.50:

            score += 10

            reasons.append("near_24h_high")



        if distance_from_low_pct <= 0.50:

            score += 10

            reasons.append("near_24h_low")



        if change_15m * change_1h > 0:

            score += 10

            reasons.append("short_term_alignment")



        min_score = 60

        should_run_ai = score >= min_score



        return {

            "ok": True,

            "should_run_ai": should_run_ai,

            "score": score,

            "min_score": min_score,

            "reason": "scanner_pass" if should_run_ai else "scanner_score_too_low",

            "reasons": reasons,

            "symbol": SYMBOL,

            "close": close,

            "change_15m_pct": round(change_15m, 4),

            "change_1h_pct": round(change_1h, 4),

            "change_4h_pct": round(change_4h, 4),

            "volume_ratio": round(volume_ratio, 4),

            "high_24h": high_24h,

            "low_24h": low_24h,

            "distance_from_high_pct": round(distance_from_high_pct, 4),

            "distance_from_low_pct": round(distance_from_low_pct, 4),

        }



    except Exception as exc:

        return {

            "ok": False,

            "should_run_ai": False,

            "reason": "scanner_error",

            "error": str(exc),

        }





if __name__ == "__main__":

    print(json.dumps(scan_btc(), indent=2))



import argparse

import json

import urllib.parse

import urllib.request

from collections import Counter, defaultdict

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Any, Optional





DEFAULT_LOG_PATH = Path("logs/candidates.jsonl")

BINANCE_FAPI_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"





def parse_ts(value: Any) -> Optional[datetime]:

    if not value:

        return None



    try:

        text = str(value)

        if text.endswith("Z"):

            text = text[:-1] + "+00:00"

        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:

            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:

        return None





def parse_float(value: Any) -> Optional[float]:

    if value is None:

        return None

    try:

        return float(value)

    except Exception:

        return None





def load_rows(path: Path):

    if not path.exists():

        return []



    rows = []

    with path.open("r", encoding="utf-8") as f:

        for line_no, line in enumerate(f, start=1):

            line = line.strip()

            if not line:

                continue

            try:

                row = json.loads(line)

                row["_line_no"] = line_no

                rows.append(row)

            except json.JSONDecodeError:

                continue



    return rows





def fetch_binance_futures_klines(symbol: str, start: datetime, end: datetime, interval: str):

    params = {

        "symbol": symbol.upper(),

        "interval": interval,

        "startTime": int(start.timestamp() * 1000),

        "endTime": int(end.timestamp() * 1000),

        "limit": 1000,

    }



    url = BINANCE_FAPI_KLINES_URL + "?" + urllib.parse.urlencode(params)



    req = urllib.request.Request(

        url,

        headers={"User-Agent": "BitSwipe validation backtester"},

    )



    with urllib.request.urlopen(req, timeout=15) as res:

        body = res.read().decode("utf-8", errors="replace")



    data = json.loads(body)

    if not isinstance(data, list):

        raise RuntimeError(f"Unexpected Binance response for {symbol}: {str(data)[:300]}")



    candles = []

    for item in data:

        # Binance kline:

        # [open_time, open, high, low, close, volume, close_time, ...]

        candles.append(

            {

                "open_time": datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc),

                "open": float(item[1]),

                "high": float(item[2]),

                "low": float(item[3]),

                "close": float(item[4]),

                "close_time": datetime.fromtimestamp(item[6] / 1000, tz=timezone.utc),

            }

        )



    return candles





def classify_candidate(row, candles):

    direction = str(row.get("direction") or "").upper()



    entry = parse_float(row.get("entry"))

    stop = parse_float(row.get("stop"))

    target = parse_float(row.get("target"))



    if direction not in {"LONG", "SHORT"}:

        return "SKIPPED_NON_DIRECTIONAL", None



    if entry is None or stop is None or target is None:

        return "SKIPPED_MISSING_LEVELS", None



    if direction == "LONG":

        if not (stop < entry < target):

            return "SKIPPED_INVALID_LEVELS", None



        for candle in candles:

            hit_stop = candle["low"] <= stop

            hit_target = candle["high"] >= target



            if hit_stop and hit_target:

                return "AMBIGUOUS_SAME_CANDLE", candle

            if hit_stop:

                return "LOSS_STOP_FIRST", candle

            if hit_target:

                return "WIN_TARGET_FIRST", candle



    if direction == "SHORT":

        if not (target < entry < stop):

            return "SKIPPED_INVALID_LEVELS", None



        for candle in candles:

            hit_stop = candle["high"] >= stop

            hit_target = candle["low"] <= target



            if hit_stop and hit_target:

                return "AMBIGUOUS_SAME_CANDLE", candle

            if hit_stop:

                return "LOSS_STOP_FIRST", candle

            if hit_target:

                return "WIN_TARGET_FIRST", candle



    return "EXPIRED_NO_HIT", None





def main():

    parser = argparse.ArgumentParser(

        description="Backtest BitSwipe candidate logs with target-before-stop validation."

    )

    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))

    parser.add_argument("--hours", type=int, default=24)

    parser.add_argument("--interval", default="15m")

    parser.add_argument("--max-rows", type=int, default=1000)

    parser.add_argument("--symbol", default=None)

    args = parser.parse_args()



    log_path = Path(args.log_path)

    rows = load_rows(log_path)



    if args.symbol:

        rows = [row for row in rows if str(row.get("symbol") or "").upper() == args.symbol.upper()]



    if args.max_rows and len(rows) > args.max_rows:

        rows = rows[-args.max_rows:]



    outcome_counter = Counter()

    grade_outcomes = defaultdict(Counter)

    decision_outcomes = defaultdict(Counter)



    evaluated = []



    for row in rows:

        symbol = row.get("symbol")

        market_type = row.get("market_type")

        ts = parse_ts(row.get("ts"))



        if not symbol or not ts:

            outcome = "SKIPPED_MISSING_SYMBOL_OR_TS"

            candle = None

        elif market_type != "BINANCE":

            outcome = "SKIPPED_NON_BINANCE"

            candle = None

        else:

            # Fast local skips before doing any network call.

            direction = str(row.get("direction") or "").upper()

            entry = parse_float(row.get("entry"))

            stop = parse_float(row.get("stop"))

            target = parse_float(row.get("target"))



            if direction not in {"LONG", "SHORT"}:

                outcome = "SKIPPED_NON_DIRECTIONAL"

                candle = None

            elif entry is None or stop is None or target is None:

                outcome = "SKIPPED_MISSING_LEVELS"

                candle = None

            else:

                try:

                    candles = fetch_binance_futures_klines(

                        symbol=symbol,

                        start=ts,

                        end=ts + timedelta(hours=args.hours),

                        interval=args.interval,

                    )

                    outcome, candle = classify_candidate(row, candles)

                except Exception as e:

                    outcome = f"FETCH_OR_CLASSIFY_ERROR:{type(e).__name__}"

                    candle = None



        outcome_counter[outcome] += 1

        grade_outcomes[row.get("grade") or "UNKNOWN"][outcome] += 1

        decision_outcomes[row.get("decision") or "UNKNOWN"][outcome] += 1



        evaluated.append(

            {

                "line": row.get("_line_no"),

                "ts": row.get("ts"),

                "symbol": symbol,

                "direction": row.get("direction"),

                "decision": row.get("decision"),

                "grade": row.get("grade"),

                "outcome": outcome,

                "hit_time": candle["open_time"].isoformat() if candle else None,

            }

        )



    print("[BitSwipe Candidate Backtest Report]")

    print()

    print(f"Log path: {log_path}")

    print(f"Rows loaded: {len(rows)}")

    print(f"Window: {args.hours}h")

    print(f"Interval: {args.interval}")



    print()

    print("Outcomes:")

    if not outcome_counter:

        print("- none")

    else:

        for key, count in outcome_counter.most_common():

            print(f"- {key}: {count}")



    print()

    print("By grade:")

    if not grade_outcomes:

        print("- none")

    else:

        for grade, counter in sorted(grade_outcomes.items()):

            parts = ", ".join(f"{k}={v}" for k, v in counter.most_common())

            print(f"- {grade}: {parts}")



    print()

    print("By decision:")

    if not decision_outcomes:

        print("- none")

    else:

        for decision, counter in sorted(decision_outcomes.items()):

            parts = ", ".join(f"{k}={v}" for k, v in counter.most_common())

            print(f"- {decision}: {parts}")



    print()

    print("Recent evaluated rows:")

    for item in evaluated[-10:]:

        print(

            f"- line={item['line']} {item['symbol']} {item['direction']} "

            f"{item['grade']} {item['decision']} -> {item['outcome']}"

        )





if __name__ == "__main__":

    main()


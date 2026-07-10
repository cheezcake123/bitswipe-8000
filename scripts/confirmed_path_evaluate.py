
#!/usr/bin/env python3

import argparse

import json

from datetime import datetime, timezone

from pathlib import Path



import requests



ROOT = Path(__file__).resolve().parents[1]

WATCH_PATH = ROOT / "logs/confirmed_path_watch.jsonl"

OUTCOME_PATH = ROOT / "logs/confirmed_path_outcomes.jsonl"



BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"

COST_FRACTION = 0.0014





def parse_time(value):

    text = str(value or "").strip().replace("Z", "+00:00")

    dt = datetime.fromisoformat(text)



    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=timezone.utc)



    return dt.astimezone(timezone.utc)





def read_jsonl(path):

    rows = []



    if not path.exists():

        return rows



    for line in path.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        try:

            row = json.loads(line)



            if isinstance(row, dict):

                rows.append(row)

        except Exception:

            continue



    return rows





def fetch_candles(symbol, start_ms, end_ms):

    response = requests.get(

        BINANCE_URL,

        params={

            "symbol": symbol,

            "interval": "1m",

            "startTime": start_ms,

            "endTime": end_ms,

            "limit": 1000,

        },

        timeout=15,

    )

    response.raise_for_status()

    return response.json()





def evaluate(row, candles):

    direction = row["direction"]

    entry = float(row["confirmed_entry"])

    stop = float(row["stop"])

    target = float(row["target_2"])

    target_1 = row.get("target_1")



    stop_first_at = None

    target_first_at = None

    target_1_at = None



    highs = []

    lows = []



    for candle in candles:

        open_time = int(candle[0])

        high = float(candle[2])

        low = float(candle[3])



        highs.append(high)

        lows.append(low)



        if direction == "LONG":

            stop_hit = low <= stop

            target_hit = high >= target

            target_1_hit = (

                target_1 is not None

                and high >= float(target_1)

            )

        else:

            stop_hit = high >= stop

            target_hit = low <= target

            target_1_hit = (

                target_1 is not None

                and low <= float(target_1)

            )



        if target_1_hit and target_1_at is None:

            target_1_at = open_time



        if stop_hit and stop_first_at is None:

            stop_first_at = open_time



        if target_hit and target_first_at is None:

            target_first_at = open_time



        if stop_first_at is not None or target_first_at is not None:

            if stop_hit and target_hit:

                result = "AMBIGUOUS"

            elif stop_hit:

                result = "STOP_FIRST"

            else:

                result = "TARGET_FIRST"

            break

    else:

        result = "OPEN_AT_240M"



    risk_distance = abs(entry - stop)



    if result == "TARGET_FIRST":

        gross_r = abs(target - entry) / risk_distance

    elif result in ("STOP_FIRST", "AMBIGUOUS"):

        gross_r = -1.0

    else:

        final_close = float(candles[-1][4])



        if direction == "LONG":

            gross_r = (final_close - entry) / risk_distance

        else:

            gross_r = (entry - final_close) / risk_distance



    if direction == "LONG":

        mfe_r = (max(highs) - entry) / risk_distance

        mae_r = (entry - min(lows)) / risk_distance

    else:

        mfe_r = (entry - min(lows)) / risk_distance

        mae_r = (max(highs) - entry) / risk_distance



    cost_r = entry * COST_FRACTION / risk_distance



    return {

        "result": result,

        "target_1_hit": target_1_at is not None,

        "target_1_at_ms": target_1_at,

        "target_at_ms": target_first_at,

        "stop_at_ms": stop_first_at,

        "gross_r": round(gross_r, 6),

        "cost_r": round(cost_r, 6),

        "net_r": round(gross_r - cost_r, 6),

        "mfe_r": round(max(0.0, mfe_r), 6),

        "mae_r": round(max(0.0, mae_r), 6),

    }





def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()



    watches = read_jsonl(WATCH_PATH)

    outcomes = read_jsonl(OUTCOME_PATH)

    completed = {

        row.get("watch_key")

        for row in outcomes

    }



    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    added = 0

    immature = 0

    failed = 0



    pending_output = []



    for row in watches:

        watch_key = row.get("watch_key")



        if not watch_key or watch_key in completed:

            continue



        try:

            confirmed = parse_time(row["confirmed_at"])

            horizon = int(row.get("horizon_minutes", 240))



            confirmed_ms = int(confirmed.timestamp() * 1000)

            start_ms = (confirmed_ms // 60000 + 1) * 60000

            end_ms = start_ms + horizon * 60000



            if now_ms < end_ms + 60000:

                immature += 1

                continue



            candles = fetch_candles(

                row["symbol"],

                start_ms,

                end_ms - 1,

            )



            if len(candles) < horizon:

                immature += 1

                continue



            result = evaluate(row, candles)



            output = {

                **row,

                **result,

                "evaluation_start_ms": start_ms,

                "evaluation_end_ms": end_ms,

                "candle_count": len(candles),

                "evaluated_at": datetime.now(

                    timezone.utc

                ).isoformat(),

            }



            pending_output.append(output)

            added += 1



        except Exception as exc:

            failed += 1

            print(

                f"FAIL {row.get('symbol')}: "

                f"{type(exc).__name__}: {exc}"

            )



    if pending_output and not args.dry_run:

        OUTCOME_PATH.parent.mkdir(

            parents=True,

            exist_ok=True,

        )



        with OUTCOME_PATH.open(

            "a",

            encoding="utf-8",

        ) as file:

            for row in pending_output:

                file.write(

                    json.dumps(

                        row,

                        ensure_ascii=False,

                    )

                    + "\n"

                )



    print("[Confirmed Path Evaluate]")

    print(f"watch_total: {len(watches)}")

    print(f"already_evaluated: {len(completed)}")

    print(f"added: {added}")

    print(f"immature: {immature}")

    print(f"failed: {failed}")

    print(f"dry_run: {args.dry_run}")





if __name__ == "__main__":

    main()


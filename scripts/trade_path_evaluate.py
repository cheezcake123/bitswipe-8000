
#!/usr/bin/env python3

import argparse

import json

import time

from collections import Counter

from datetime import datetime, timedelta, timezone

from pathlib import Path

from urllib.error import HTTPError, URLError

from urllib.parse import urlencode

from urllib.request import Request, urlopen



ROOT = Path(__file__).resolve().parents[1]



PREDICTION_LOG = ROOT / "logs/prediction_watch.jsonl"

PATH_OUTCOME_LOG = ROOT / "logs/trade_path_outcomes.jsonl"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"



INTERVAL = "1m"

INTERVAL_MS = 60_000

DEFAULT_HORIZON_MIN = 240





def now_utc():

    return datetime.now(timezone.utc)





def now_text():

    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")





def log(message):

    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    with WATCH_LOG.open("a", encoding="utf-8") as handle:

        handle.write(f"[{now_text()}] {message}\n")





def load_jsonl(path):

    if not path.exists():

        return []



    rows = []



    for line in path.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        try:

            rows.append(json.loads(line))

        except Exception:

            pass



    return rows





def append_jsonl(path, row):

    path.parent.mkdir(parents=True, exist_ok=True)



    with path.open("a", encoding="utf-8") as handle:

        handle.write(

            json.dumps(

                row,

                ensure_ascii=False,

                separators=(",", ":"),

            )

            + "\n"

        )





def number(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default





def parse_ts(value):

    if not value:

        return None



    try:

        result = datetime.fromisoformat(

            str(value).replace("Z", "+00:00")

        )



        if result.tzinfo is None:

            result = result.replace(tzinfo=timezone.utc)



        return result.astimezone(timezone.utc)



    except Exception:

        return None





def ceil_next_minute_ms(dt):

    current_ms = int(dt.timestamp() * 1000)

    return ((current_ms // INTERVAL_MS) + 1) * INTERVAL_MS





def fetch_klines(symbol, start_ms, end_ms):

    params = urlencode({

        "symbol": symbol,

        "interval": INTERVAL,

        "startTime": start_ms,

        "endTime": end_ms,

        "limit": 1000,

    })



    url = f"{BINANCE_KLINE_URL}?{params}"



    request = Request(

        url,

        headers={

            "User-Agent": "BitSwipe-TradePath/1.0",

            "Accept": "application/json",

        },

    )



    last_error = None



    for attempt in range(3):

        try:

            with urlopen(request, timeout=20) as response:

                payload = json.loads(

                    response.read().decode("utf-8")

                )



            if not isinstance(payload, list):

                raise RuntimeError(

                    f"unexpected Binance response: {payload}"

                )



            candles = []



            for item in payload:

                candles.append({

                    "open_time_ms": int(item[0]),

                    "open_time": datetime.fromtimestamp(

                        int(item[0]) / 1000,

                        tz=timezone.utc,

                    ).isoformat(),

                    "open": float(item[1]),

                    "high": float(item[2]),

                    "low": float(item[3]),

                    "close": float(item[4]),

                    "close_time_ms": int(item[6]),

                })



            return candles



        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:

            last_error = exc

            time.sleep(1.0 + attempt)



    raise RuntimeError(

        f"Binance kline fetch failed: {last_error}"

    )





def direction_return_pct(direction, entry, price):

    if entry <= 0:

        return 0.0



    raw = (price / entry - 1.0) * 100.0



    if direction == "SHORT":

        return -raw



    return raw





def path_excursions(direction, entry, candles):

    if not candles or entry <= 0:

        return 0.0, 0.0



    highest = max(candle["high"] for candle in candles)

    lowest = min(candle["low"] for candle in candles)



    if direction == "LONG":

        mfe_pct = max(0.0, (highest / entry - 1.0) * 100.0)

        mae_pct = max(0.0, (1.0 - lowest / entry) * 100.0)



    else:

        mfe_pct = max(0.0, (1.0 - lowest / entry) * 100.0)

        mae_pct = max(0.0, (highest / entry - 1.0) * 100.0)



    return mfe_pct, mae_pct





def evaluate_path(prediction, candles, horizon_min):

    direction = prediction.get("direction")

    entry = number(prediction.get("entry_price"))

    stop = number(prediction.get("virtual_stop"))

    target = number(prediction.get("virtual_target"))

    risk_pct = number(prediction.get("risk_pct"))

    planned_rr = number(prediction.get("planned_rr"))



    result = "OPEN_AT_240M"

    resolved_at = None

    resolution_candle = None

    used_candles = candles



    for index, candle in enumerate(candles):

        if direction == "LONG":

            stop_hit = candle["low"] <= stop

            target_hit = candle["high"] >= target

        else:

            stop_hit = candle["high"] >= stop

            target_hit = candle["low"] <= target



        if stop_hit and target_hit:

            result = "AMBIGUOUS"

        elif target_hit:

            result = "TARGET_FIRST"

        elif stop_hit:

            result = "STOP_FIRST"

        else:

            continue



        resolved_at = candle["open_time"]

        resolution_candle = candle

        used_candles = candles[:index + 1]

        break



    final_price = candles[-1]["close"] if candles else entry

    final_direction_return_pct = direction_return_pct(

        direction,

        entry,

        final_price,

    )



    mfe_pct, mae_pct = path_excursions(

        direction,

        entry,

        used_candles,

    )



    if result == "TARGET_FIRST":

        gross_r = planned_rr

        conservative_r = planned_rr



    elif result == "STOP_FIRST":

        gross_r = -1.0

        conservative_r = -1.0



    elif result == "AMBIGUOUS":

        gross_r = None

        conservative_r = -1.0



    else:

        gross_r = (

            final_direction_return_pct / risk_pct

            if risk_pct > 0

            else None

        )

        conservative_r = gross_r



    return {

        "prediction_id": prediction.get("prediction_id"),

        "created_at": prediction.get("created_at"),

        "evaluated_at": now_utc().isoformat(),

        "symbol": prediction.get("symbol"),

        "market_type": prediction.get("market_type"),

        "direction": direction,

        "prediction_class": prediction.get("prediction_class"),

        "score": prediction.get("score"),

        "grade": prediction.get("grade"),

        "plan_version": prediction.get("plan_version"),

        "horizon_min": horizon_min,

        "path_interval": INTERVAL,

        "entry_price": entry,

        "virtual_stop": stop,

        "virtual_target": target,

        "risk_pct": risk_pct,

        "reward_pct": prediction.get("reward_pct"),

        "planned_rr": planned_rr,

        "path_result": result,

        "resolved_at": resolved_at,

        "resolution_candle": resolution_candle,

        "path_start_at": (

            candles[0]["open_time"]

            if candles

            else None

        ),

        "path_end_at": (

            candles[-1]["open_time"]

            if candles

            else None

        ),

        "candle_count": len(candles),

        "final_price": final_price,

        "final_direction_return_pct": final_direction_return_pct,

        "mfe_pct": mfe_pct,

        "mae_pct": mae_pct,

        "gross_r": gross_r,

        "conservative_r": conservative_r,

        "cost_model": "not_applied",

        "partial_first_minute_excluded": True,

    }





def outcome_key(row):

    return (

        str(row.get("prediction_id")),

        int(number(row.get("horizon_min"))),

        str(row.get("path_interval")),

        str(row.get("plan_version")),

    )





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--horizon",

        type=int,

        default=DEFAULT_HORIZON_MIN,

    )

    parser.add_argument(

        "--limit",

        type=int,

        default=0,

    )

    parser.add_argument(

        "--dry-run",

        action="store_true",

    )



    args = parser.parse_args()



    predictions = load_jsonl(PREDICTION_LOG)

    existing = load_jsonl(PATH_OUTCOME_LOG)



    existing_keys = {

        outcome_key(row)

        for row in existing

    }



    now = now_utc()



    eligible = []

    missing_plan = 0

    not_mature = 0

    already_done = 0



    for prediction in predictions:

        created_at = parse_ts(

            prediction.get("created_at")

        )



        if not created_at:

            continue



        if (

            prediction.get("plan_version") != "scanner_rr_v1"

            or prediction.get("virtual_stop") is None

            or prediction.get("virtual_target") is None

        ):

            missing_plan += 1

            continue



        mature_at = created_at + timedelta(

            minutes=args.horizon

        )



        if now < mature_at:

            not_mature += 1

            continue



        key = (

            str(prediction.get("prediction_id")),

            args.horizon,

            INTERVAL,

            str(prediction.get("plan_version")),

        )



        if key in existing_keys:

            already_done += 1

            continue



        eligible.append(prediction)



    eligible.sort(

        key=lambda row: (

            parse_ts(row.get("created_at"))

            or datetime.min.replace(tzinfo=timezone.utc)

        )

    )



    if args.limit > 0:

        eligible = eligible[-args.limit:]



    results = []

    failures = []



    for prediction in eligible:

        created_at = parse_ts(

            prediction.get("created_at")

        )



        start_ms = ceil_next_minute_ms(created_at)

        end_ms = int(

            (

                created_at

                + timedelta(minutes=args.horizon)

            ).timestamp()

            * 1000

        )



        try:

            candles = fetch_klines(

                prediction.get("symbol"),

                start_ms,

                end_ms,

            )



            if not candles:

                raise RuntimeError("no candles returned")



            result = evaluate_path(

                prediction,

                candles,

                args.horizon,

            )



            results.append(result)



            if not args.dry_run:

                append_jsonl(

                    PATH_OUTCOME_LOG,

                    result,

                )



            time.sleep(0.12)



        except Exception as exc:

            failures.append({

                "symbol": prediction.get("symbol"),

                "prediction_id": prediction.get("prediction_id"),

                "error": f"{type(exc).__name__}: {exc}",

            })



    counts = Counter(

        row.get("path_result")

        for row in results

    )



    summary = {

        "predictions": len(predictions),

        "eligible": len(eligible),

        "evaluated": len(results),

        "missing_plan": missing_plan,

        "not_mature": not_mature,

        "already_done": already_done,

        "failures": len(failures),

        "results": dict(counts),

        "dry_run": args.dry_run,

    }



    print("[BitSwipe Trade Path Evaluator]")

    print(json.dumps(

        summary,

        ensure_ascii=False,

        indent=2,

    ))



    if results:

        print("")

        print("evaluated trades:")



        for row in results:

            gross_r = row.get("gross_r")

            gross_text = (

                "N/A"

                if gross_r is None

                else f"{gross_r:.3f}R"

            )



            print(

                f"- {row['symbol']} "

                f"{row['direction']} "

                f"{row['path_result']} "

                f"gross={gross_text} "

                f"conservative="

                f"{row['conservative_r']:.3f}R "

                f"MFE={row['mfe_pct']:.3f}% "

                f"MAE={row['mae_pct']:.3f}%"

            )



    if failures:

        print("")

        print("failures:")



        for failure in failures:

            print(

                f"- {failure['symbol']}: "

                f"{failure['error']}"

            )



    log(

        "TRADE_PATH_EVAL "

        + json.dumps(

            summary,

            ensure_ascii=False,

            separators=(",", ":"),

        )

    )



    if failures:

        raise SystemExit(1)





if __name__ == "__main__":

    main()


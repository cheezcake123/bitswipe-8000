
#!/usr/bin/env python3

import argparse

import json

from datetime import datetime, timedelta, timezone

from pathlib import Path



import requests



ROOT = Path(__file__).resolve().parents[1]

ACTIVE_STATE_PATH = ROOT / "logs/active_scenarios.json"



BINANCE_KLINES_URL = (

    "https://fapi.binance.com/fapi/v1/klines"

)



INTERVAL = "15m"

CANDLE_LIMIT = 5





def now_utc():

    return datetime.now(timezone.utc)





def parse_ts(value):

    try:

        result = datetime.fromisoformat(

            str(value).replace("Z", "+00:00")

        )



        if result.tzinfo is None:

            result = result.replace(

                tzinfo=timezone.utc

            )



        return result.astimezone(timezone.utc)



    except Exception:

        return None





def number(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default





def load_state():

    try:

        if ACTIVE_STATE_PATH.exists():

            value = json.loads(

                ACTIVE_STATE_PATH.read_text(

                    encoding="utf-8"

                )

            )



            if isinstance(value, dict):

                return value

    except Exception:

        pass



    return {

        "active": {},

        "history": [],

    }





def save_state(state):

    ACTIVE_STATE_PATH.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    ACTIVE_STATE_PATH.write_text(

        json.dumps(

            state,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def fetch_closed_candles(symbol):

    response = requests.get(

        BINANCE_KLINES_URL,

        params={

            "symbol": symbol,

            "interval": INTERVAL,

            "limit": CANDLE_LIMIT,

        },

        timeout=20,

    )



    response.raise_for_status()



    current_ms = int(

        now_utc().timestamp() * 1000

    )



    candles = []



    for item in response.json():

        close_time = int(item[6])



        if close_time >= current_ms:

            continue



        candles.append({

            "open_time": int(item[0]),

            "close_time": close_time,

            "open": number(item[1]),

            "high": number(item[2]),

            "low": number(item[3]),

            "close": number(item[4]),

        })



    return candles





def evaluate(record, candles, current_time=None):

    current_time = current_time or now_utc()



    expires_at = parse_ts(

        record.get("expires_at")

    )



    if (

        expires_at is not None

        and current_time >= expires_at

    ):

        return {

            "status": "EXPIRED",

            "reason": "45분 유효기간 종료",

            "candle": None,

        }



    if not candles:

        return {

            "status": "WATCHING",

            "reason": "완료된 15분봉 데이터 없음",

            "candle": None,

        }



    candle = candles[-1]



    direction = record.get("direction")



    stop = number(

        record.get("virtual_stop")

    )

    entry_reference = number(

        record.get("entry_reference")

    )

    zone_low = number(

        record.get("entry_zone_low")

    )

    zone_high = number(

        record.get("entry_zone_high")

    )

    support = number(

        record.get("support")

    )

    resistance = number(

        record.get("resistance")

    )



    if direction == "SHORT":

        if candle["close"] >= stop:

            return {

                "status": "INVALIDATED",

                "reason": (

                    "15분봉이 숏 무효화 가격 "

                    f"{stop} 위에서 마감"

                ),

                "candle": candle,

            }



        touched_zone = (

            candle["high"] >= zone_low

        )



        bearish_rejection = (

            candle["close"] < candle["open"]

            and candle["close"]

            <= entry_reference

        )



        below_resistance = (

            resistance <= 0

            or candle["close"] < resistance

        )



        if (

            touched_zone

            and bearish_rejection

            and below_resistance

        ):

            return {

                "status": "CONFIRMED",

                "reason": (

                    "진입 구간 접촉 후 "

                    "저항 아래 약세 마감"

                ),

                "candle": candle,

            }



    elif direction == "LONG":

        if candle["close"] <= stop:

            return {

                "status": "INVALIDATED",

                "reason": (

                    "15분봉이 롱 무효화 가격 "

                    f"{stop} 아래에서 마감"

                ),

                "candle": candle,

            }



        touched_zone = (

            candle["low"] <= zone_high

        )



        bullish_reaction = (

            candle["close"] > candle["open"]

            and candle["close"]

            >= entry_reference

        )



        above_support = (

            support <= 0

            or candle["close"] > support

        )



        if (

            touched_zone

            and bullish_reaction

            and above_support

        ):

            return {

                "status": "CONFIRMED",

                "reason": (

                    "진입 구간 접촉 후 "

                    "지지 위 강세 마감"

                ),

                "candle": candle,

            }



    return {

        "status": "WATCHING",

        "reason": "확인 또는 무효화 조건 미충족",

        "candle": candle,

    }





def demo_record():

    current_time = now_utc()



    return {

        "scenario_id": "AAVEUSDT|SHORT|DEMO",

        "status": "WATCHING",

        "symbol": "AAVEUSDT",

        "direction": "SHORT",

        "entry_reference": 93.36,

        "entry_zone_low": 93.36,

        "entry_zone_high": 94.00,

        "support": 90.48,

        "resistance": 94.00,

        "virtual_stop": 94.376,

        "target_1": 91.92,

        "target_2": 90.48,

        "expires_at": (

            current_time

            + timedelta(minutes=45)

        ).isoformat(),

    }





def run_demo():

    record = demo_record()

    current_time = now_utc()



    examples = {

        "WATCHING": [{

            "open": 93.10,

            "high": 93.25,

            "low": 92.95,

            "close": 93.18,

        }],

        "CONFIRMED": [{

            "open": 93.90,

            "high": 94.02,

            "low": 93.20,

            "close": 93.30,

        }],

        "INVALIDATED": [{

            "open": 94.10,

            "high": 94.70,

            "low": 94.00,

            "close": 94.50,

        }],

    }



    print("[Scenario Tracker Demo]")



    for expected, candles in examples.items():

        result = evaluate(

            record,

            candles,

            current_time=current_time,

        )



        print("")

        print(f"예상: {expected}")

        print(f"판정: {result['status']}")

        print(f"이유: {result['reason']}")



    expired_record = dict(record)



    expired_record["expires_at"] = (

        current_time

        - timedelta(minutes=1)

    ).isoformat()



    expired_result = evaluate(

        expired_record,

        [],

        current_time=current_time,

    )



    print("")

    print("예상: EXPIRED")

    print(

        f"판정: {expired_result['status']}"

    )

    print(

        f"이유: {expired_result['reason']}"

    )





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--demo",

        action="store_true",

    )



    parser.add_argument(

        "--dry-run",

        action="store_true",

    )



    args = parser.parse_args()



    if args.demo:

        run_demo()

        return



    state = load_state()

    active = state.setdefault(

        "active",

        {},

    )

    history = state.setdefault(

        "history",

        [],

    )



    checked = 0

    watching = 0

    confirmed = 0

    invalidated = 0

    expired = 0

    failures = 0

    resolved_keys = []



    for key, record in list(

        active.items()

    ):

        if (

            not isinstance(record, dict)

            or record.get("status")

            != "WATCHING"

        ):

            continue



        checked += 1



        try:

            candles = fetch_closed_candles(

                record.get("symbol")

            )



            result = evaluate(

                record,

                candles,

            )



        except Exception as exc:

            failures += 1



            print(

                f"- {key}: ERROR "

                f"{type(exc).__name__}: {exc}"

            )



            continue



        status = result["status"]



        print(

            f"- {key}: {status} "

            f"({result['reason']})"

        )



        if status == "WATCHING":

            watching += 1



            if not args.dry_run:

                record["last_checked_at"] = (

                    now_utc().isoformat()

                )



                record["last_reason"] = (

                    result["reason"]

                )



            continue



        if status == "CONFIRMED":

            confirmed += 1



        elif status == "INVALIDATED":

            invalidated += 1



        elif status == "EXPIRED":

            expired += 1



        if not args.dry_run:

            record["status"] = "PENDING_NOTIFY"

            record["pending_resolution"] = status

            record["resolution"] = (

                result["reason"]

            )

            record["resolved_at"] = (

                now_utc().isoformat()

            )

            record["resolution_candle"] = (

                result["candle"]

            )



            record["followup_sent"] = False



    if not args.dry_run:

        for key in resolved_keys:

            active.pop(key, None)



        state["updated_at"] = (

            now_utc().isoformat()

        )



        save_state(state)



    print("")

    print("[Scenario Tracker]")

    print(f"checked: {checked}")

    print(f"watching: {watching}")

    print(f"confirmed: {confirmed}")

    print(f"invalidated: {invalidated}")

    print(f"expired: {expired}")

    print(f"failures: {failures}")

    print(f"dry_run: {args.dry_run}")





if __name__ == "__main__":

    main()


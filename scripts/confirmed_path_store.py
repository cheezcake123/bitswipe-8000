
#!/usr/bin/env python3

import json

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

SCENARIO_PATH = ROOT / "logs/active_scenarios.json"

WATCH_PATH = ROOT / "logs/confirmed_path_watch.jsonl"



PLAN_VERSION = "confirmed_trigger_v1"

HORIZON_MINUTES = 240





def number(value):

    try:

        return float(value)

    except Exception:

        return None





def load_scenario_history():

    if not SCENARIO_PATH.exists():

        return []



    try:

        data = json.loads(

            SCENARIO_PATH.read_text(

                encoding="utf-8",

                errors="ignore",

            )

        )

    except Exception:

        return []



    if isinstance(data, dict):

        history = data.get("history", [])

        return history if isinstance(history, list) else []



    return data if isinstance(data, list) else []





def load_existing_keys():

    keys = set()



    if not WATCH_PATH.exists():

        return keys



    for line in WATCH_PATH.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        try:

            row = json.loads(line)

            keys.add(row.get("watch_key"))

        except Exception:

            continue



    return keys





def get_confirmed_entry(record):

    candle = record.get("confirmation_candle")



    if isinstance(candle, dict):

        value = number(candle.get("close"))

        if value is not None:

            return value



    for key in (

        "confirmed_price",

        "confirmation_price",

        "entry_reference",

    ):

        value = number(record.get(key))

        if value is not None:

            return value



    direction = str(record.get("direction", "")).upper()



    if direction == "SHORT":

        return number(record.get("entry_zone_low"))



    return number(record.get("entry_zone_high"))





def get_value(record, *keys):

    for key in keys:

        value = number(record.get(key))

        if value is not None:

            return value

    return None





def main():

    history = load_scenario_history()

    existing = load_existing_keys()

    added = 0

    skipped = 0



    WATCH_PATH.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    with WATCH_PATH.open(

        "a",

        encoding="utf-8",

    ) as output:

        for record in history:

            if str(record.get("status", "")).upper() != "CONFIRMED":

                continue



            symbol = str(record.get("symbol", "")).upper()

            direction = str(record.get("direction", "")).upper()



            confirmed_at = (

                record.get("resolved_at")

                or record.get("confirmed_at")

                or record.get("last_checked_at")

            )



            watch_key = (

                str(record.get("scenario_id") or "")

                or f"{symbol}|{direction}|{confirmed_at}"

            )



            if not symbol or direction not in ("LONG", "SHORT"):

                skipped += 1

                continue



            if not confirmed_at or watch_key in existing:

                skipped += 1

                continue



            entry = get_confirmed_entry(record)

            stop = get_value(

                record,

                "virtual_stop",

                "stop",

                "stop_price",

            )

            target_1 = get_value(

                record,

                "target_1",

                "target1",

            )

            target_2 = get_value(

                record,

                "target_2",

                "target2",

                "virtual_target",

            )



            if None in (entry, stop, target_2):

                skipped += 1

                continue



            row = {

                "watch_key": watch_key,

                "plan_version": PLAN_VERSION,

                "symbol": symbol,

                "direction": direction,

                "confirmed_at": confirmed_at,

                "confirmed_entry": entry,

                "stop": stop,

                "target_1": target_1,

                "target_2": target_2,

                "horizon_minutes": HORIZON_MINUTES,

                "stored_at": datetime.now(

                    timezone.utc

                ).isoformat(),

                "evaluated": False,

            }



            output.write(

                json.dumps(

                    row,

                    ensure_ascii=False,

                )

                + "\n"

            )



            existing.add(watch_key)

            added += 1



    print("[Confirmed Path Store]")

    print(f"history_confirmed: {sum(str(x.get('status', '')).upper() == 'CONFIRMED' for x in history)}")

    print(f"added: {added}")

    print(f"skipped: {skipped}")

    print(f"path: {WATCH_PATH}")





if __name__ == "__main__":

    main()


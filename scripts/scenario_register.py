
#!/usr/bin/env python3

import argparse

import json

from datetime import datetime, timedelta, timezone

from pathlib import Path



from prediction_watch_store import virtual_trade_plan

from scenario_reporter import (

    candidate_class,

    demo_row,

    latest_batch,

    load_rows,

    make_entry_zone,

    make_targets,

)



ROOT = Path(__file__).resolve().parents[1]



NOTIFY_STATE_PATH = ROOT / "logs/scenario_notify_state.json"

ACTIVE_STATE_PATH = ROOT / "logs/active_scenarios.json"



MAX_NOTIFY_AGE_SECONDS = 180

SCENARIO_LIFETIME_MINUTES = 45





def now_utc():

    return datetime.now(timezone.utc)





def parse_ts(value):

    try:

        result = datetime.fromisoformat(

            str(value).replace("Z", "+00:00")

        )



        if result.tzinfo is None:

            result = result.replace(tzinfo=timezone.utc)



        return result.astimezone(timezone.utc)



    except Exception:

        return None





def load_json(path, default):

    try:

        if path.exists():

            value = json.loads(

                path.read_text(encoding="utf-8")

            )



            if isinstance(value, dict):

                return value

    except Exception:

        pass



    return default





def save_json(path, value):

    path.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    path.write_text(

        json.dumps(

            value,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def scenario_key(row):

    return f"{row['symbol']}|{row['direction']}"





def make_record(row, sent_at):

    plan = virtual_trade_plan(

        direction=row["direction"],

        entry=row["entry"],

        support=row["support"],

        resistance=row["resistance"],

        atr_pct=row["atr_pct"],

    )



    if not plan:

        return None



    zone_low, zone_high = make_entry_zone(

        row,

        plan,

    )



    target_1, target_2 = make_targets(

        row,

        plan,

    )



    expires_at = sent_at + timedelta(

        minutes=SCENARIO_LIFETIME_MINUTES

    )



    risk_pct = float(plan["risk_pct"])



    position_pct = (

        min(200.0, 0.25 / risk_pct * 100.0)

        if risk_pct > 0

        else 0.0

    )



    return {

        "scenario_id": (

            f"{row['symbol']}|"

            f"{row['direction']}|"

            f"{sent_at.replace(microsecond=0).isoformat()}"

        ),

        "status": "WATCHING",

        "symbol": row["symbol"],

        "market_type": row["market_type"],

        "direction": row["direction"],

        "classification": "PILOT_ELIGIBLE_STRICT",

        "grade": row["grade"],

        "score": row["score"],

        "candidate_at": row["ts"].isoformat(),

        "sent_at": sent_at.isoformat(),

        "registered_at": now_utc().isoformat(),

        "expires_at": expires_at.isoformat(),

        "entry_reference": row["entry"],

        "entry_zone_low": zone_low,

        "entry_zone_high": zone_high,

        "support": row["support"],

        "resistance": row["resistance"],

        "atr_pct": row["atr_pct"],

        "virtual_stop": plan["virtual_stop"],

        "target_1": target_1,

        "target_2": target_2,

        "risk_pct": plan["risk_pct"],

        "reward_pct": plan["reward_pct"],

        "planned_rr": plan["planned_rr"],

        "max_account_risk_pct": 0.25,

        "max_position_pct": position_pct,

        "max_leverage": 2.0,

        "followup_sent": False,

        "resolution": None,

        "resolved_at": None,

    }





def run_demo():

    row = demo_row()

    record = make_record(

        row,

        now_utc(),

    )



    print("[Scenario Register Demo]")

    print(

        json.dumps(

            record,

            ensure_ascii=False,

            indent=2,

        )

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



    notify_state = load_json(

        NOTIFY_STATE_PATH,

        {"sent": {}},

    )



    active_state = load_json(

        ACTIVE_STATE_PATH,

        {"active": {}},

    )



    sent_items = notify_state.get(

        "sent",

        {},

    )



    active = active_state.setdefault(

        "active",

        {},

    )



    registered = 0

    skipped = 0

    current_time = now_utc()



    for row in latest_batch(load_rows()):

        if (

            candidate_class(row)

            != "PILOT_ELIGIBLE_STRICT"

        ):

            continue



        key = scenario_key(row)

        sent_item = sent_items.get(key)



        if not isinstance(sent_item, dict):

            skipped += 1

            continue



        sent_at = parse_ts(

            sent_item.get("sent_at")

        )



        if sent_at is None:

            skipped += 1

            continue



        notify_age = (

            current_time - sent_at

        ).total_seconds()



        if (

            notify_age < 0

            or notify_age > MAX_NOTIFY_AGE_SECONDS

        ):

            skipped += 1

            continue



        existing = active.get(key)



        if (

            isinstance(existing, dict)

            and existing.get("status")

            == "WATCHING"

        ):

            skipped += 1

            continue



        record = make_record(

            row,

            sent_at,

        )



        if record is None:

            skipped += 1

            continue



        if args.dry_run:

            print(

                json.dumps(

                    record,

                    ensure_ascii=False,

                    indent=2,

                )

            )

        else:

            active[key] = record



        registered += 1



    if not args.dry_run:

        active_state["updated_at"] = (

            current_time.isoformat()

        )



        save_json(

            ACTIVE_STATE_PATH,

            active_state,

        )



    print("[Scenario Register]")

    print(f"registered: {registered}")

    print(f"skipped: {skipped}")

    print(f"active: {len(active)}")

    print(f"dry_run: {args.dry_run}")





if __name__ == "__main__":

    main()


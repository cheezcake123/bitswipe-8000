
#!/usr/bin/env python3

import json

import shutil

from datetime import datetime, timezone

from pathlib import Path



from prediction_watch_store import virtual_trade_plan



ROOT = Path(__file__).resolve().parents[1]

CANDIDATES = ROOT / "logs/candidates.jsonl"

PREDICTIONS = ROOT / "logs/prediction_watch.jsonl"





def load_jsonl(path):

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





def number(value):

    try:

        return float(value)

    except Exception:

        return 0.0





def candidate_key(row):

    return (

        str(row.get("ts") or ""),

        str(row.get("symbol") or ""),

        str(row.get("direction") or ""),

        round(number(row.get("last")), 8),

    )





def prediction_key(row):

    return (

        str(row.get("created_at") or ""),

        str(row.get("symbol") or ""),

        str(row.get("direction") or ""),

        round(number(row.get("entry_price")), 8),

    )





def main():

    candidates = load_jsonl(CANDIDATES)

    predictions = load_jsonl(PREDICTIONS)



    candidate_map = {

        candidate_key(row): row

        for row in candidates

        if row.get("ts")

    }



    updated = 0

    already_present = 0

    unmatched = []



    for pred in predictions:

        if (

            pred.get("plan_version") == "scanner_rr_v1"

            and pred.get("virtual_stop") is not None

            and pred.get("virtual_target") is not None

        ):

            already_present += 1

            continue



        candidate = candidate_map.get(

            prediction_key(pred)

        )



        if not candidate:

            unmatched.append(pred.get("prediction_id"))

            continue



        atr_pct = number(candidate.get("atr_pct"))



        plan = virtual_trade_plan(

            pred.get("direction"),

            pred.get("entry_price"),

            pred.get("support"),

            pred.get("resistance"),

            atr_pct,

        )



        if not plan:

            unmatched.append(pred.get("prediction_id"))

            continue



        pred["atr_pct"] = atr_pct

        pred["plan_version"] = "scanner_rr_v1"

        pred.update(plan)

        pred["rr_delta"] = (

            plan["planned_rr"]

            - number(pred.get("rr"))

        )



        updated += 1



    if unmatched:

        print("BACKFILL_ABORTED")

        print("unmatched:", len(unmatched))

        for item in unmatched[:10]:

            print("-", item)

        raise SystemExit(1)



    timestamp = datetime.now(

        timezone.utc

    ).strftime("%Y%m%dT%H%M%SZ")



    backup = PREDICTIONS.with_name(

        f"prediction_watch.jsonl.bak.{timestamp}"

    )



    shutil.copy2(PREDICTIONS, backup)



    temp = PREDICTIONS.with_name(

        "prediction_watch.jsonl.tmp"

    )



    with temp.open("w", encoding="utf-8") as handle:

        for row in predictions:

            handle.write(

                json.dumps(

                    row,

                    ensure_ascii=False,

                    separators=(",", ":"),

                )

                + "\n"

            )



    temp.replace(PREDICTIONS)



    print("[Prediction Plan Backfill]")

    print("predictions:", len(predictions))

    print("updated:", updated)

    print("already_present:", already_present)

    print("unmatched:", len(unmatched))

    print("backup:", backup)

    print("BACKFILL_OK")





if __name__ == "__main__":

    main()


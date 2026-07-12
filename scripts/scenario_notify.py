
#!/usr/bin/env python3

from korean_alerts import localize_alert_text
import argparse

import json

import os

from datetime import datetime, timedelta, timezone

from pathlib import Path



import requests



from scenario_reporter import (

    candidate_class,

    demo_row,

    latest_batch,

    load_rows,

    scenario_report,

)



ROOT = Path(__file__).resolve().parents[1]



STATE_PATH = ROOT / "logs/scenario_notify_state.json"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



DEDUP_MINUTES = 60

STATE_RETENTION_HOURS = 48





def now_utc():

    return datetime.now(timezone.utc)





def now_text():

    return now_utc().strftime(

        "%Y-%m-%d %H:%M:%S UTC"

    )





def load_env_file(path):

    if not path.exists():

        return



    for line in path.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        line = line.strip()



        if (

            not line

            or line.startswith("#")

            or "=" not in line

        ):

            continue



        key, value = line.split("=", 1)



        os.environ.setdefault(

            key.strip(),

            value.strip().strip('"').strip("'"),

        )





def load_env():

    load_env_file(ROOT / ".env")

    load_env_file(ROOT / ".env.local")





def log(message):

    WATCH_LOG.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    with WATCH_LOG.open(

        "a",

        encoding="utf-8",

    ) as handle:

        handle.write(

            f"[{now_text()}] {message}\n"

        )





def compact(value):

    return json.dumps(

        value,

        ensure_ascii=False,

        separators=(",", ":"),

    )





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





def load_state():

    try:

        if STATE_PATH.exists():

            data = json.loads(

                STATE_PATH.read_text(

                    encoding="utf-8"

                )

            )



            if isinstance(data, dict):

                return data

    except Exception:

        pass



    return {

        "sent": {},

    }





def save_state(state):

    STATE_PATH.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    STATE_PATH.write_text(

        json.dumps(

            state,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def candidate_key(row):

    return "|".join([

        row["symbol"],

        row["direction"],

    ])





def cleanup_state(state):

    cutoff = now_utc() - timedelta(

        hours=STATE_RETENTION_HOURS

    )



    cleaned = {}



    for key, value in (

        state.get("sent") or {}

    ).items():

        timestamp = parse_ts(

            value.get("sent_at")

            if isinstance(value, dict)

            else None

        )



        if timestamp and timestamp >= cutoff:

            cleaned[key] = value



    state["sent"] = cleaned



    return state





def recently_sent(state, key):

    item = (

        state.get("sent") or {}

    ).get(key)



    if not isinstance(item, dict):

        return False



    sent_at = parse_ts(item.get("sent_at"))



    if not sent_at:

        return False



    return (

        now_utc() - sent_at

        < timedelta(minutes=DEDUP_MINUTES)

    )





def send_telegram(text):

    text = localize_alert_text(text)
    token = os.environ.get(

        "TELEGRAM_BOT_TOKEN",

        "",

    )

    chat_id = os.environ.get(

        "TELEGRAM_CHAT_ID",

        "",

    )



    if not token or not chat_id:

        log(

            "SCENARIO_TELEGRAM_SKIP "

            "missing_token_or_chat_id"

        )



        return False, None



    url = (

        "https://api.telegram.org/"

        f"bot{token}/sendMessage"

    )



    try:

        response = requests.post(

            url,

            json={

                "chat_id": chat_id,

                "text": text,

                "disable_web_page_preview": True,

            },

            timeout=25,

        )



        try:

            payload = response.json()

        except Exception:

            payload = {}



        ok = (

            response.status_code == 200

            and bool(payload.get("ok"))

        )



        message_id = (

            payload.get("result", {}).get(

                "message_id"

            )

            if isinstance(payload, dict)

            else None

        )



        log(

            "SCENARIO_TELEGRAM_RESULT "

            + compact({

                "status": response.status_code,

                "ok": ok,

                "message_id": message_id,

                "description": (

                    payload.get("description")

                    if isinstance(payload, dict)

                    else None

                ),

            })

        )



        return ok, message_id



    except Exception as exc:

        log(

            "SCENARIO_TELEGRAM_ERROR "

            f"{type(exc).__name__}: {exc}"

        )



        return False, None





def collect_live_reports():

    reports = []



    for row in latest_batch(load_rows()):

        classification = candidate_class(row)



        if (

            classification

            != "PILOT_ELIGIBLE_STRICT"

        ):

            continue



        text = scenario_report(

            row,

            classification,

        )



        if text:

            reports.append(

                (row, classification, text)

            )



    return reports





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--dry-run",

        action="store_true",

    )



    parser.add_argument(

        "--test",

        action="store_true",

    )



    args = parser.parse_args()



    load_env()



    if args.test:

        row = demo_row()



        classification = (

            "PILOT_ELIGIBLE_STRICT"

        )



        report = scenario_report(

            row,

            classification,

        )



        message = (

            "[한국어 시나리오 알림 테스트]\n\n"

            + report

        )



        if args.dry_run:

            print(message)

            return



        ok, message_id = send_telegram(

            message

        )



        if not ok:

            raise SystemExit(

                "테스트 알림 전송 실패"

            )



        print(

            "[Scenario Notify] "

            f"TEST_SENT message_id={message_id}"

        )



        return



    reports = collect_live_reports()



    if not reports:

        print(

            "[Scenario Notify] "

            "SKIP no_strict_candidates"

        )



        log(

            "SCENARIO_NOTIFY_SKIP "

            "no_strict_candidates"

        )



        return



    state = cleanup_state(load_state())



    pending = []



    for row, classification, report in reports:

        key = candidate_key(row)



        if recently_sent(state, key):

            log(

                "SCENARIO_NOTIFY_SKIP "

                + compact({

                    "reason": "duplicate",

                    "symbol": row["symbol"],

                    "direction": row["direction"],

                    "key": key,

                })

            )



            continue



        pending.append(

            (

                row,

                classification,

                report,

                key,

            )

        )



    if not pending:

        print(

            "[Scenario Notify] "

            "SKIP duplicate_candidates"

        )



        save_state(state)

        return



    if args.dry_run:

        for index, item in enumerate(

            pending,

            start=1,

        ):

            row, _, report, _ = item



            print(

                f"\n===== 후보 {index}: "

                f"{row['symbol']} =====\n"

            )



            print(report)



        return



    sent_count = 0



    for row, classification, report, key in pending:

        ok, message_id = send_telegram(

            report

        )



        if not ok:

            log(

                "SCENARIO_NOTIFY_FAILED "

                + compact({

                    "symbol": row["symbol"],

                    "direction": row["direction"],

                    "classification": classification,

                })

            )



            raise SystemExit(

                "한국어 시나리오 전송 실패"

            )



        sent_count += 1



        state.setdefault(

            "sent",

            {},

        )[key] = {

            "sent_at": now_utc().isoformat(),

            "symbol": row["symbol"],

            "direction": row["direction"],

            "classification": classification,

            "message_id": message_id,

        }



        log(

            "SCENARIO_NOTIFY_SUCCESS "

            + compact({

                "symbol": row["symbol"],

                "direction": row["direction"],

                "classification": classification,

                "score": row["score"],

                "rr": row["rr"],

                "message_id": message_id,

            })

        )



    save_state(state)



    print(

        "[Scenario Notify] "

        f"SENT count={sent_count}"

    )





if __name__ == "__main__":

    main()



#!/usr/bin/env python3

from korean_alerts import localize_alert_text
import argparse

import json

import os

import subprocess

import sys

from datetime import datetime, timezone

from pathlib import Path

from zoneinfo import ZoneInfo



import requests



ROOT = Path(__file__).resolve().parents[1]

REPORT_SCRIPT = ROOT / "scripts/daily_forward_report.py"

STATE_PATH = ROOT / "logs/daily_forward_notify_state.json"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



KST = ZoneInfo("Asia/Seoul")

MAX_MESSAGE_LENGTH = 3800





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





def now_utc_text():

    return datetime.now(

        timezone.utc

    ).strftime("%Y-%m-%d %H:%M:%S UTC")





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

            f"[{now_utc_text()}] {message}\n"

        )





def compact(value):

    return json.dumps(

        value,

        ensure_ascii=False,

        separators=(",", ":"),

    )





def load_state():

    try:

        if STATE_PATH.exists():

            return json.loads(

                STATE_PATH.read_text(

                    encoding="utf-8"

                )

            )

    except Exception:

        pass



    return {}





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





def run_report():

    result = subprocess.run(

        [

            sys.executable,

            str(REPORT_SCRIPT),

        ],

        cwd=ROOT,

        text=True,

        capture_output=True,

        timeout=90,

    )



    report = result.stdout.strip()



    if result.returncode != 0:

        error = result.stderr.strip()



        raise RuntimeError(

            f"daily report failed: {error}"

        )



    if not report:

        raise RuntimeError(

            "daily report produced no output"

        )



    return report





def split_message(text):

    chunks = []

    current = ""



    for line in text.splitlines(keepends=True):

        if len(line) > MAX_MESSAGE_LENGTH:

            if current:

                chunks.append(current.rstrip())

                current = ""



            for index in range(

                0,

                len(line),

                MAX_MESSAGE_LENGTH,

            ):

                chunks.append(

                    line[

                        index:index + MAX_MESSAGE_LENGTH

                    ].rstrip()

                )



            continue



        if (

            current

            and len(current) + len(line)

            > MAX_MESSAGE_LENGTH

        ):

            chunks.append(current.rstrip())

            current = line

        else:

            current += line



    if current:

        chunks.append(current.rstrip())



    return chunks or [text]





def send_chunk(token, chat_id, text):

    text = localize_alert_text(text)
    url = (

        f"https://api.telegram.org/"

        f"bot{token}/sendMessage"

    )



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

        payload.get("result", {}).get("message_id")

        if isinstance(payload, dict)

        else None

    )



    log(

        "DAILY_REPORT_TELEGRAM_RESULT "

        + compact({

            "status": response.status_code,

            "ok": ok,

            "message_id": message_id,

            "description": payload.get(

                "description"

            ) if isinstance(payload, dict) else None,

        })

    )



    return ok, message_id





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--dry-run",

        action="store_true",

    )

    parser.add_argument(

        "--force",

        action="store_true",

    )



    args = parser.parse_args()



    load_env()



    today_kst = datetime.now(

        KST

    ).date().isoformat()



    state = load_state()



    if (

        not args.force

        and state.get("last_sent_kst_date")

        == today_kst

    ):

        print(

            "[BitSwipe Daily Report] "

            f"SKIP already_sent {today_kst}"

        )



        log(

            "DAILY_REPORT_SKIP "

            + compact({

                "reason": "already_sent",

                "kst_date": today_kst,

            })

        )



        return



    report = run_report()



    if args.dry_run:

        print("[BitSwipe Daily Report Dry Run]")

        print("")

        print(report)

        return



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

            "DAILY_REPORT_TELEGRAM_SKIP "

            "missing_token_or_chat_id"

        )



        raise SystemExit(

            "텔레그램 토큰 또는 채팅 ID가 없습니다."

        )



    chunks = split_message(report)

    message_ids = []



    for index, chunk in enumerate(chunks, start=1):

        if len(chunks) > 1:

            chunk = (

                f"[{index}/{len(chunks)}]\n"

                + chunk

            )



        ok, message_id = send_chunk(

            token,

            chat_id,

            chunk,

        )



        if not ok:

            log(

                "DAILY_REPORT_NOTIFY_FAILED "

                + compact({

                    "kst_date": today_kst,

                    "chunk": index,

                })

            )



            raise SystemExit(

                "텔레그램 일일 보고서 전송 실패"

            )



        message_ids.append(message_id)



    sent_at = datetime.now(

        timezone.utc

    ).isoformat()



    save_state({

        "last_sent_kst_date": today_kst,

        "last_sent_at": sent_at,

        "message_ids": message_ids,

        "chunk_count": len(chunks),

    })



    log(

        "DAILY_REPORT_NOTIFY_SUCCESS "

        + compact({

            "kst_date": today_kst,

            "chunks": len(chunks),

        })

    )



    print(

        "[BitSwipe Daily Report] "

        f"SENT {today_kst} chunks={len(chunks)}"

    )





if __name__ == "__main__":

    main()


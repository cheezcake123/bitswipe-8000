
#!/usr/bin/env python3

from korean_alerts import localize_alert_text
import argparse

import hashlib

import json

import os

import subprocess

import sys

from datetime import datetime, timedelta, timezone

from pathlib import Path



import requests



ROOT = Path(__file__).resolve().parents[1]

HEALTH_SCRIPT = ROOT / "scripts/confirmed_research_health.py"

STATE_PATH = ROOT / "logs/confirmed_research_alert_state.json"

REMINDER_HOURS = 6





def load_env():

    for name in (".env", ".env.local"):

        path = ROOT / name



        if not path.exists():

            continue



        for line in path.read_text(

            encoding="utf-8",

            errors="ignore",

        ).splitlines():

            line = line.strip()



            if not line or line.startswith("#") or "=" not in line:

                continue



            key, value = line.split("=", 1)

            os.environ.setdefault(

                key.strip(),

                value.strip().strip('"').strip("'"),

            )





def load_state():

    if not STATE_PATH.exists():

        return {}



    try:

        data = json.loads(

            STATE_PATH.read_text(encoding="utf-8")

        )

        return data if isinstance(data, dict) else {}

    except Exception:

        return {}





def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(

        json.dumps(

            state,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def parse_time(value):

    try:

        text = str(value or "").replace("Z", "+00:00")

        result = datetime.fromisoformat(text)



        if result.tzinfo is None:

            result = result.replace(tzinfo=timezone.utc)



        return result.astimezone(timezone.utc)

    except Exception:

        return None





def run_health():

    result = subprocess.run(

        [sys.executable, str(HEALTH_SCRIPT)],

        cwd=ROOT,

        capture_output=True,

        text=True,

        timeout=30,

        check=False,

    )



    output = "\n".join(

        part.strip()

        for part in (result.stdout, result.stderr)

        if part.strip()

    )



    return result.returncode == 0, output





def send_telegram(message):

    message = localize_alert_text(message)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()



    if not token or not chat_id:

        raise RuntimeError("텔레그램 설정이 없습니다.")



    response = requests.post(

        f"https://api.telegram.org/bot{token}/sendMessage",

        json={

            "chat_id": chat_id,

            "text": message,

        },

        timeout=15,

    )

    response.raise_for_status()





def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()



    load_env()



    now = datetime.now(timezone.utc)

    healthy, output = run_health()

    state = load_state()



    previous_healthy = state.get("healthy")

    fingerprint = hashlib.sha256(

        output.encode("utf-8")

    ).hexdigest()



    last_sent = parse_time(state.get("last_sent_at"))

    reminder_due = (

        last_sent is None

        or now - last_sent >= timedelta(hours=REMINDER_HOURS)

    )



    message = None



    if healthy:

        if previous_healthy is False:

            message = (

                "[BitSwipe 연구 파이프라인 복구]\n\n"

                "Confirmed Trigger 연구 상태가 정상으로 돌아왔습니다.\n\n"

                + output

            )

    else:

        changed = fingerprint != state.get("fingerprint")



        if changed or reminder_due:

            message = (

                "[BitSwipe 연구 파이프라인 경고]\n\n"

                + output

            )



    print("[Confirmed Research Alert]")

    print(f"- health: {'PASS' if healthy else 'FAIL'}")

    print(f"- notification: {'YES' if message else 'NO'}")

    print(f"- dry_run: {args.dry_run}")



    if message:

        print("")

        print(message)



        if not args.dry_run:

            send_telegram(message)



    if not args.dry_run:

        new_state = {

            "healthy": healthy,

            "fingerprint": fingerprint,

            "checked_at": now.isoformat(),

            "last_sent_at": (

                now.isoformat()

                if message

                else state.get("last_sent_at")

            ),

        }

        save_state(new_state)





if __name__ == "__main__":

    main()


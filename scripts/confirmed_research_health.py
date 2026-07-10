
#!/usr/bin/env python3

import json

import re

import subprocess

from datetime import datetime, timedelta, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

WATCH_PATH = ROOT / "logs/confirmed_path_watch.jsonl"

OUTCOME_PATH = ROOT / "logs/confirmed_path_outcomes.jsonl"



TIMER_NAME = "bitswipe-confirmed-path.timer"

SERVICE_NAME = "bitswipe-confirmed-path.service"

STALL_GRACE_MINUTES = 30





def run_command(*args):

    result = subprocess.run(

        args,

        capture_output=True,

        text=True,

        timeout=10,

        check=False,

    )

    return result.returncode, result.stdout.strip()





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





def parse_time(value):

    try:

        text = str(value).strip().replace("Z", "+00:00")

        result = datetime.fromisoformat(text)



        if result.tzinfo is None:

            result = result.replace(tzinfo=timezone.utc)



        return result.astimezone(timezone.utc)

    except Exception:

        return None





def stalled_evaluations():

    watches = read_jsonl(WATCH_PATH)

    outcomes = read_jsonl(OUTCOME_PATH)



    completed = {

        row.get("watch_key")

        for row in outcomes

        if row.get("watch_key")

    }



    now = datetime.now(timezone.utc)

    stalled = []



    for row in watches:

        key = row.get("watch_key")



        if not key or key in completed:

            continue



        confirmed_at = parse_time(row.get("confirmed_at"))



        if confirmed_at is None:

            continue



        horizon = int(row.get("horizon_minutes", 240))

        deadline = confirmed_at + timedelta(

            minutes=horizon + STALL_GRACE_MINUTES

        )



        if now > deadline:

            stalled.append(

                str(row.get("symbol") or key)

            )



    return stalled





def recent_failures():

    code, output = run_command(

        "journalctl",

        "-u",

        SERVICE_NAME,

        "--since",

        "60 minutes ago",

        "--no-pager",

    )



    if code != 0:

        return []



    failures = []



    for line in output.splitlines():

        if re.search(r"\bFAIL\b", line):

            failures.append(line.strip())

            continue



        match = re.search(r"failed:\s*(\d+)", line)



        if match and int(match.group(1)) > 0:

            failures.append(line.strip())



    return failures





def main():

    issues = []



    timer_code, timer_state = run_command(

        "systemctl",

        "is-active",

        TIMER_NAME,

    )



    if timer_code != 0 or timer_state != "active":

        issues.append(

            f"연구 타이머 비정상: {timer_state or 'unknown'}"

        )



    _, service_result = run_command(

        "systemctl",

        "show",

        SERVICE_NAME,

        "--property=Result",

        "--value",

    )



    if service_result not in ("", "success"):

        issues.append(

            f"최근 연구 서비스 결과: {service_result}"

        )



    stalled = stalled_evaluations()



    if stalled:

        issues.append(

            f"240분 평가 지연: {len(stalled)}건"

        )



    failures = recent_failures()



    if failures:

        issues.append(

            f"최근 60분 평가 실패: {len(failures)}건"

        )



    print("[Confirmed Research Health]")

    print(f"- timer: {timer_state or 'unknown'}")

    print(f"- service_result: {service_result or 'unknown'}")

    print(f"- stalled: {len(stalled)}")

    print(f"- recent_failures: {len(failures)}")



    if issues:

        print("상태: FAIL")



        for issue in issues:

            print(f"- {issue}")



        raise SystemExit(1)



    print("상태: PASS")





if __name__ == "__main__":

    main()


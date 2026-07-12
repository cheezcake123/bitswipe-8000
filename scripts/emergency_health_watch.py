
#!/usr/bin/env python3

from korean_alerts import localize_alert_text
import argparse

import hashlib

import json

import os

import shutil

import subprocess

import sys

from collections import deque

from datetime import datetime, timedelta, timezone

from pathlib import Path

from zoneinfo import ZoneInfo



import requests



ROOT = Path(__file__).resolve().parents[1]



WATCH_LOG = ROOT / "logs/watchlist_scan.log"

STATE_PATH = ROOT / "logs/emergency_health_watch_state.json"
ACTIVE_SCENARIOS_PATH = ROOT / "logs/active_scenarios.json"



SCENARIO_STALL_GRACE_MINUTES = 30

PENDING_NOTIFY_STALL_MINUTES = 30




KST = ZoneInfo("Asia/Seoul")



SCAN_STALE_MINUTES = 35

RECENT_FAILURE_MINUTES = 60

REPEAT_ALERT_HOURS = 6

MIN_AVAILABLE_MEMORY_MB = 200

MAX_DISK_USAGE_PCT = 90.0





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





def now_utc():

    return datetime.now(timezone.utc)





def now_text():

    return now_utc().strftime(

        "%Y-%m-%d %H:%M:%S UTC"

    )





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



    return {

        "status": "unknown",

        "fingerprint": None,

        "last_alert_at": None,

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





def run_command(args, timeout=25):

    try:

        result = subprocess.run(

            args,

            cwd=ROOT,

            text=True,

            capture_output=True,

            timeout=timeout,

        )



        return (

            result.returncode,

            result.stdout.strip(),

            result.stderr.strip(),

        )



    except Exception as exc:

        return 1, "", f"{type(exc).__name__}: {exc}"





def parse_log_time(line):

    if not line.startswith("["):

        return None



    try:

        return datetime.strptime(

            line[1:24],

            "%Y-%m-%d %H:%M:%S UTC",

        ).replace(tzinfo=timezone.utc)



    except Exception:

        return None





def recent_log_summary():

    summary = {

        "last_scan": None,

        "trade_path_failures": 0,

    }



    if not WATCH_LOG.exists():

        return summary



    with WATCH_LOG.open(

        "r",

        encoding="utf-8",

        errors="ignore",

    ) as handle:

        lines = list(deque(handle, maxlen=20000))



    cutoff = now_utc() - timedelta(

        minutes=RECENT_FAILURE_MINUTES

    )



    for line in lines:

        timestamp = parse_log_time(line)



        if "SCAN_DONE" in line:

            summary["last_scan"] = timestamp



        if (

            timestamp

            and timestamp >= cutoff

            and "TRADE_PATH_EVAL " in line

        ):

            try:

                payload = line.split(

                    "TRADE_PATH_EVAL ",

                    1,

                )[1]



                data = json.loads(payload)



                summary["trade_path_failures"] += int(

                    data.get("failures") or 0

                )



            except Exception:

                pass



    return summary





def available_memory_mb():

    try:

        for line in Path(

            "/proc/meminfo"

        ).read_text().splitlines():

            if line.startswith("MemAvailable:"):

                kb = float(line.split()[1])

                return kb / 1024.0

    except Exception:

        pass



    return None





def scenario_stall_summary(current_time):

    result = {

        "watching_stalled": 0,

        "pending_stalled": 0,

        "details": [],

    }



    if not ACTIVE_SCENARIOS_PATH.exists():

        return result



    try:

        state = json.loads(

            ACTIVE_SCENARIOS_PATH.read_text(

                encoding="utf-8"

            )

        )

    except Exception as exc:

        result["details"].append(

            f"active scenario state read error="

            f"{type(exc).__name__}"

        )

        return result



    active = state.get("active") or {}



    if not isinstance(active, dict):

        result["details"].append(

            "active scenario state malformed"

        )

        return result



    def parse_timestamp(value):

        try:

            timestamp = datetime.fromisoformat(

                str(value).replace("Z", "+00:00")

            )



            if timestamp.tzinfo is None:

                timestamp = timestamp.replace(

                    tzinfo=timezone.utc

                )



            return timestamp.astimezone(

                timezone.utc

            )



        except Exception:

            return None



    for key, record in active.items():

        if not isinstance(record, dict):

            continue



        status = record.get("status")

        symbol = record.get("symbol") or key

        direction = record.get("direction") or "UNKNOWN"



        if status == "WATCHING":

            expires_at = parse_timestamp(

                record.get("expires_at")

            )



            if expires_at is None:

                continue



            delay_minutes = (

                current_time - expires_at

            ).total_seconds() / 60.0



            if delay_minutes >= SCENARIO_STALL_GRACE_MINUTES:

                result["watching_stalled"] += 1

                result["details"].append(

                    f"{symbol} {direction} WATCHING "

                    f"만료 후 {delay_minutes:.0f}분"

                )



        elif status == "PENDING_NOTIFY":

            pending_since = parse_timestamp(

                record.get("resolved_at")

                or record.get("last_checked_at")

                or record.get("registered_at")

                or record.get("sent_at")

            )



            if pending_since is None:

                continue



            delay_minutes = (

                current_time - pending_since

            ).total_seconds() / 60.0



            if delay_minutes >= PENDING_NOTIFY_STALL_MINUTES:

                result["pending_stalled"] += 1

                result["details"].append(

                    f"{symbol} {direction} 후속 알림 "

                    f"{delay_minutes:.0f}분 대기"

                )



    return result





def collect_health():

    now = now_utc()

    issues = []

    details = []



    watch_code, watch_state, _ = run_command([

        "systemctl",

        "is-active",

        "bitswipe-btc-watch.timer",

    ])



    if watch_code != 0 or watch_state != "active":

        issues.append("자동 감시 타이머 비활성")

        details.append(

            f"bitswipe-btc-watch.timer={watch_state or 'unknown'}"

        )



    daily_code, daily_state, _ = run_command([

        "systemctl",

        "is-active",

        "bitswipe-daily-report.timer",

    ])



    if daily_code != 0 or daily_state != "active":

        issues.append("일일 보고 타이머 비활성")

        details.append(

            f"bitswipe-daily-report.timer={daily_state or 'unknown'}"

        )



    _, service_result, _ = run_command([

        "systemctl",

        "show",

        "bitswipe-btc-watch.service",

        "--property=Result",

        "--value",

    ])



    if service_result not in ("success", ""):

        issues.append("최근 자동 감시 서비스 실패")

        details.append(

            f"watch service result={service_result}"

        )



    integrity_code, _, integrity_error = run_command([

        sys.executable,

        "scripts/experiment_integrity_guard.py",

    ])



    if integrity_code != 0:

        issues.append("Forward V1 무결성 검사 실패")



        if integrity_error:

            details.append(

                f"integrity={integrity_error[:200]}"

            )



    logs = recent_log_summary()

    last_scan = logs["last_scan"]



    if last_scan is None:

        issues.append("최근 스캔 시각 확인 불가")

    else:

        scan_age = (

            now - last_scan

        ).total_seconds() / 60.0



        details.append(

            f"last scan age={scan_age:.1f}분"

        )



        if scan_age > SCAN_STALE_MINUTES:

            issues.append(

                f"자동 스캔 {scan_age:.0f}분 지연"

            )



    if logs["trade_path_failures"] > 0:

        issues.append(

            "최근 Trade Path 평가 실패"

        )

        details.append(

            "최근 "

            f"{RECENT_FAILURE_MINUTES}분 실패="

            f"{logs['trade_path_failures']}건"

        )



    scenario_stalls = scenario_stall_summary(now)



    if scenario_stalls["watching_stalled"] > 0:

        count = scenario_stalls["watching_stalled"]



        issues.append(

            f"만료 후 정리되지 않은 시나리오 {count}건"

        )



    if scenario_stalls["pending_stalled"] > 0:

        count = scenario_stalls["pending_stalled"]



        issues.append(

            f"후속 알림 전송 정체 {count}건"

        )



    for detail in scenario_stalls["details"][:5]:

        details.append(detail)



    disk = shutil.disk_usage(ROOT)

    disk_usage_pct = (

        disk.used / disk.total * 100.0

    )



    details.append(

        f"disk={disk_usage_pct:.1f}%"

    )



    if disk_usage_pct >= MAX_DISK_USAGE_PCT:

        issues.append(

            f"디스크 사용률 {disk_usage_pct:.1f}%"

        )



    memory_mb = available_memory_mb()



    if memory_mb is not None:

        details.append(

            f"available memory={memory_mb:.0f}MB"

        )



        if memory_mb < MIN_AVAILABLE_MEMORY_MB:

            issues.append(

                f"가용 메모리 {memory_mb:.0f}MB"

            )



    return {

        "checked_at": now.isoformat(),

        "issues": sorted(set(issues)),

        "details": details,

        "healthy": not issues,

    }





def fingerprint(issues):

    source = "\n".join(sorted(issues))



    return hashlib.sha256(

        source.encode("utf-8")

    ).hexdigest()





def parse_state_time(value):

    try:

        return datetime.fromisoformat(

            str(value).replace("Z", "+00:00")

        )

    except Exception:

        return None





def build_message(health, recovery=False, test=False):

    now_kst = now_utc().astimezone(KST)



    if test and health["healthy"]:

        title = "[BitSwipe 긴급 감시 테스트]"

    elif recovery:

        title = "[BitSwipe 복구 알림]"

    else:

        title = "[BitSwipe 긴급 장애 알림]"



    lines = [

        title,

        "",

        "시각: "

        + now_kst.strftime(

            "%Y-%m-%d %H:%M:%S KST"

        ),

        "",

    ]



    if health["healthy"]:

        lines.extend([

            "현재 상태: 정상",

            "",

            "자동 감시·일일 보고·전진 검증 "

            "무결성이 정상입니다.",

        ])

    else:

        lines.append("현재 상태: 점검 필요")

        lines.append("")

        lines.append("감지된 문제:")



        for issue in health["issues"]:

            lines.append(f"- ⚠️ {issue}")



        lines.extend([

            "",

            "조치 원칙:",

            "- 문제 해소 전 실전 진입 금지",

            "- 자동매매 금지",

            "- 서버 상태와 최근 로그 확인",

        ])



    lines.append("")

    lines.append("진단 정보:")



    for detail in health["details"]:

        lines.append(f"- {detail}")



    return "\n".join(lines)





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

            "HEALTH_WATCH_TELEGRAM_SKIP "

            "missing_token_or_chat_id"

        )

        return False



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



        log(

            "HEALTH_WATCH_TELEGRAM_RESULT "

            + compact({

                "status": response.status_code,

                "ok": ok,

                "message_id": (

                    payload.get("result", {}).get(

                        "message_id"

                    )

                    if isinstance(payload, dict)

                    else None

                ),

                "description": (

                    payload.get("description")

                    if isinstance(payload, dict)

                    else None

                ),

            })

        )



        return ok



    except Exception as exc:

        log(

            "HEALTH_WATCH_TELEGRAM_ERROR "

            f"{type(exc).__name__}: {exc}"

        )

        return False





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



    health = collect_health()

    state = load_state()

    now = now_utc()



    current_status = (

        "healthy"

        if health["healthy"]

        else "unhealthy"

    )



    current_fingerprint = fingerprint(

        health["issues"]

    )



    previous_status = state.get("status")

    previous_fingerprint = state.get(

        "fingerprint"

    )



    last_alert_at = parse_state_time(

        state.get("last_alert_at")

    )



    repeat_due = (

        last_alert_at is None

        or now - last_alert_at

        >= timedelta(hours=REPEAT_ALERT_HOURS)

    )



    should_send = False

    recovery = False

    reason = "no_change"



    if args.force:

        should_send = True

        recovery = health["healthy"]

        reason = "forced"



    elif not health["healthy"]:

        if previous_status != "unhealthy":

            should_send = True

            reason = "new_failure"



        elif current_fingerprint != previous_fingerprint:

            should_send = True

            reason = "failure_changed"



        elif repeat_due:

            should_send = True

            reason = "repeat_reminder"



    elif previous_status == "unhealthy":

        should_send = True

        recovery = True

        reason = "recovered"



    message = build_message(

        health,

        recovery=recovery,

        test=args.force,

    )



    print("[BitSwipe Emergency Health Watch]")

    print("status:", current_status)

    print("reason:", reason)

    print("send:", should_send)

    print("issues:", len(health["issues"]))



    for issue in health["issues"]:

        print("-", issue)



    if args.dry_run:

        print("")

        print(message)

        return



    sent = False



    if should_send:

        sent = send_telegram(message)



        if not sent:

            raise SystemExit(

                "긴급 감시 텔레그램 전송 실패"

            )



    new_state = {

        "status": current_status,

        "fingerprint": current_fingerprint,

        "last_checked_at": now.isoformat(),

        "last_alert_at": (

            now.isoformat()

            if sent

            else state.get("last_alert_at")

        ),

        "last_reason": reason,

        "issues": health["issues"],

    }



    save_state(new_state)



    log(

        "HEALTH_WATCH_RESULT "

        + compact({

            "status": current_status,

            "issues": len(health["issues"]),

            "sent": sent,

            "reason": reason,

        })

    )





if __name__ == "__main__":

    main()


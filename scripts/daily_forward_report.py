
#!/usr/bin/env python3

import json

import math

import subprocess

from collections import Counter

from datetime import datetime, timedelta, timezone

from pathlib import Path

from zoneinfo import ZoneInfo



ROOT = Path(__file__).resolve().parents[1]



CONFIG = ROOT / "config/trade_experiment_v1.json"

PREDICTIONS = ROOT / "logs/prediction_watch.jsonl"

PATH_OUTCOMES = ROOT / "logs/trade_path_outcomes.jsonl"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



KST = ZoneInfo("Asia/Seoul")





def load_json(path):

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return {}





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





def command(args):

    try:

        result = subprocess.run(

            args,

            cwd=ROOT,

            text=True,

            capture_output=True,

            timeout=20,

        )



        return result.returncode, result.stdout.strip()



    except Exception:

        return 1, ""





def deduplicate(rows):

    latest = {}



    for row in rows:

        key = (

            row.get("prediction_id"),

            int(number(row.get("horizon_min"))),

            row.get("path_interval"),

            row.get("plan_version"),

        )



        latest[key] = row



    return sorted(

        latest.values(),

        key=lambda row: str(row.get("created_at") or ""),

    )





def is_strict(row, policy):

    score = number(row.get("score"))

    rr = number(row.get("planned_rr"))



    return (

        row.get("market_type") == policy.get("market_type")

        and row.get("direction") == policy.get("direction")

        and score >= number(policy.get("minimum_score"))

        and score <= number(policy.get("maximum_score"))

        and rr >= number(policy.get("minimum_rr"))

        and int(number(row.get("horizon_min")))

        == int(number(policy.get("horizon_min")))

        and row.get("path_interval")

        == policy.get("path_interval")

        and row.get("plan_version")

        == policy.get("plan_version")

    )





def add_net_r(row, total_cost_bps):

    result = dict(row)



    risk_pct = number(result.get("risk_pct"))

    gross_r = number(result.get("conservative_r"))

    cost_pct = total_cost_bps / 100.0



    cost_r = (

        cost_pct / risk_pct

        if risk_pct > 0

        else 0.0

    )



    result["net_r"] = gross_r - cost_r



    return result





def profit_factor(values):

    gains = sum(value for value in values if value > 0)

    losses = abs(sum(value for value in values if value < 0))



    if losses == 0:

        return math.inf if gains > 0 else 0.0



    return gains / losses





def max_drawdown(values):

    equity = 0.0

    peak = 0.0

    worst = 0.0



    for value in values:

        equity += value

        peak = max(peak, equity)

        worst = max(worst, peak - equity)



    return worst





def metrics(rows):

    values = [number(row.get("net_r")) for row in rows]



    wins = sum(value > 0 for value in values)

    losses = sum(value < 0 for value in values)



    return {

        "count": len(rows),

        "wins": wins,

        "losses": losses,

        "win_rate": (

            wins / len(rows) * 100

            if rows

            else 0.0

        ),

        "average_net_r": (

            sum(values) / len(values)

            if values

            else 0.0

        ),

        "total_net_r": sum(values),

        "profit_factor": profit_factor(values),

        "max_drawdown_r": max_drawdown(values),

    }





def parse_log_time(line):

    if not line.startswith("["):

        return None



    try:

        text = line[1:24]

        return datetime.strptime(

            text,

            "%Y-%m-%d %H:%M:%S UTC",

        ).replace(tzinfo=timezone.utc)



    except Exception:

        return None





def log_summary():

    result = {

        "last_scan": None,

        "scans_24h": 0,

        "strict_alerts_24h": 0,

        "path_failures_24h": 0,

    }



    if not WATCH_LOG.exists():

        return result



    now = datetime.now(timezone.utc)

    cutoff = now - timedelta(hours=24)



    lines = WATCH_LOG.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines()



    for line in lines:

        ts = parse_log_time(line)



        if "SCAN_DONE" in line:

            result["last_scan"] = ts



            if ts and ts >= cutoff:

                result["scans_24h"] += 1



        if (

            ts

            and ts >= cutoff

            and "POLICY_NOTIFY_ATTEMPT" in line

            and '"telegram_ok":true' in line.replace(" ", "")

        ):

            result["strict_alerts_24h"] += 1



        if (

            ts

            and ts >= cutoff

            and "TRADE_PATH_EVAL " in line

        ):

            try:

                payload = line.split(

                    "TRADE_PATH_EVAL ",

                    1,

                )[1]



                data = json.loads(payload)



                result["path_failures_24h"] += int(

                    data.get("failures") or 0

                )



            except Exception:

                pass



    return result





def format_pf(value):

    if math.isinf(value):

        return "∞"



    return f"{value:.2f}"





def main():

    now = datetime.now(timezone.utc)



    config = load_json(CONFIG)

    started_at = parse_ts(config.get("started_at"))

    policy = config.get("policy") or {}



    total_cost_bps = number(

        (config.get("cost_model") or {}).get(

            "total_cost_bps",

            14.0,

        )

    )



    predictions = load_jsonl(PREDICTIONS)

    outcomes = deduplicate(load_jsonl(PATH_OUTCOMES))



    forward_predictions = []



    for row in predictions:

        created = parse_ts(row.get("created_at"))



        if (

            started_at

            and created

            and created >= started_at

        ):

            forward_predictions.append(row)



    forward_outcomes = []



    for row in outcomes:

        created = parse_ts(row.get("created_at"))



        if (

            started_at

            and created

            and created >= started_at

        ):

            forward_outcomes.append(

                add_net_r(row, total_cost_bps)

            )



    strict_forward = [

        row for row in forward_outcomes

        if is_strict(row, policy)

    ]



    strict_stats = metrics(strict_forward)

    path_counts = Counter(

        row.get("path_result", "UNKNOWN")

        for row in forward_outcomes

    )



    timer_code, timer_state = command([

        "systemctl",

        "is-active",

        "bitswipe-btc-watch.timer",

    ])



    _, service_result = command([

        "systemctl",

        "show",

        "bitswipe-btc-watch.service",

        "--property=Result",

        "--value",

    ])



    integrity_code, _ = command([

        "python3",

        "scripts/experiment_integrity_guard.py",

    ])



    _, git_status = command([

        "git",

        "status",

        "--short",

    ])



    logs = log_summary()



    last_scan_age = None



    if logs["last_scan"]:

        last_scan_age = (

            now - logs["last_scan"]

        ).total_seconds() / 60.0



    warnings = []



    if timer_code != 0 or timer_state != "active":

        warnings.append("자동 감시 타이머 비활성")



    if service_result not in ("success", ""):

        warnings.append(

            f"최근 서비스 결과 이상: {service_result}"

        )



    if integrity_code != 0:

        warnings.append("Forward V1 무결성 검사 실패")



    if git_status:

        warnings.append("Git 작업 폴더에 미커밋 변경 존재")



    if last_scan_age is None:

        warnings.append("최근 스캔 시각 확인 불가")

    elif last_scan_age > 35:

        warnings.append(

            f"최근 스캔이 {last_scan_age:.0f}분 전"

        )



    if logs["path_failures_24h"] > 0:

        warnings.append(

            f"24시간 경로 평가 실패 "

            f"{logs['path_failures_24h']}건"

        )



    print("[BitSwipe 일일 운영 보고서]")

    print("")

    print(

        "보고 시각:",

        now.astimezone(KST).strftime(

            "%Y-%m-%d %H:%M:%S KST"

        ),

    )

    print(

        "실험:",

        config.get("experiment_id", "UNKNOWN"),

    )

    print("")



    print("운영 상태:")

    print(

        "- 감시 타이머:",

        "정상" if timer_state == "active" else timer_state,

    )

    print(

        "- 최근 서비스:",

        service_result or "확인 불가",

    )

    print(

        "- 실험 무결성:",

        "PASS" if integrity_code == 0 else "FAIL",

    )

    print(

        "- Git 상태:",

        "깨끗함" if not git_status else "미커밋 변경 있음",

    )



    if logs["last_scan"]:

        print(

            "- 최근 스캔:",

            logs["last_scan"].astimezone(KST).strftime(

                "%Y-%m-%d %H:%M:%S KST"

            ),

        )

    else:

        print("- 최근 스캔: 확인 불가")



    print("")

    print("최근 24시간:")

    print(f"- 자동 스캔: {logs['scans_24h']}회")

    print(

        f"- STRICT 텔레그램 알림: "

        f"{logs['strict_alerts_24h']}건"

    )

    print(

        f"- 경로 평가 실패: "

        f"{logs['path_failures_24h']}건"

    )



    print("")

    print("Forward V1 데이터:")

    print(

        f"- 실험 이후 prediction: "

        f"{len(forward_predictions)}개"

    )

    print(

        f"- 성숙한 Trade Path 결과: "

        f"{len(forward_outcomes)}개"

    )

    print(

        f"- STRICT 성숙 결과: "

        f"{strict_stats['count']}개 / 목표 30개"

    )



    if path_counts:

        print(

            "- 경로 분포:",

            ", ".join(

                f"{key}={value}"

                for key, value in sorted(path_counts.items())

            ),

        )



    print("")

    print("STRICT 전진 성과:")



    if strict_stats["count"] == 0:

        print("- 아직 성숙한 표본 없음")

    else:

        print(

            f"- 비용 차감 승률: "

            f"{strict_stats['win_rate']:.1f}% "

            f"({strict_stats['wins']}/"

            f"{strict_stats['count']})"

        )

        print(

            f"- 평균 순성과: "

            f"{strict_stats['average_net_r']:.3f}R"

        )

        print(

            f"- 누적 순성과: "

            f"{strict_stats['total_net_r']:.3f}R"

        )

        print(

            f"- Profit Factor: "

            f"{format_pf(strict_stats['profit_factor'])}"

        )

        print(

            f"- 최대 낙폭: "

            f"{strict_stats['max_drawdown_r']:.3f}R"

        )



    print("")

    print("이상 감지:")



    if warnings:

        for warning in warnings:

            print(f"- ⚠️ {warning}")

    else:

        print("- 없음")



    print("")

    print("운영 판정:")



    if warnings:

        print("- 시스템 점검 필요")

        print("- 문제 해소 전 실전 진입 금지")



    elif strict_stats["count"] < 30:

        print("- Forward V1 데이터 축적 단계")

        print("- 전략 조건 변경 금지")

        print("- 자동매매 금지")



    else:

        print("- STRICT 표본 30건 도달")

        print("- 성과 및 낙폭 재심사 필요")

        print("- 자동매매 전환은 아직 별도 승인 대상")





if __name__ == "__main__":

    main()


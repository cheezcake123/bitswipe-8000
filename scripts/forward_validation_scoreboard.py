
#!/usr/bin/env python3

import json

import math

import statistics

from collections import Counter

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

CONFIG = ROOT / "config/trade_experiment_v1.json"

OUTCOMES = ROOT / "logs/trade_path_outcomes.jsonl"





def load_json(path):

    return json.loads(path.read_text(encoding="utf-8"))





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

    try:

        dt = datetime.fromisoformat(

            str(value).replace("Z", "+00:00")

        )

        if dt.tzinfo is None:

            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:

        return None





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

        row.get("market_type") == policy["market_type"]

        and row.get("direction") == policy["direction"]

        and score >= number(policy["minimum_score"])

        and score <= number(policy["maximum_score"])

        and rr >= number(policy["minimum_rr"])

        and int(number(row.get("horizon_min"))) == int(policy["horizon_min"])

        and row.get("path_interval") == policy["path_interval"]

        and row.get("plan_version") == policy["plan_version"]

    )





def add_cost(row, total_cost_bps):

    result = dict(row)



    risk_pct = number(result.get("risk_pct"))

    gross_r = number(result.get("conservative_r"))

    cost_pct = total_cost_bps / 100.0

    cost_r = cost_pct / risk_pct if risk_pct > 0 else 0.0



    result["cost_r"] = cost_r

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





def summarize(rows):

    values = [number(row.get("net_r")) for row in rows]



    wins = sum(value > 0 for value in values)

    losses = sum(value < 0 for value in values)

    flats = len(values) - wins - losses



    return {

        "count": len(rows),

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "win_rate": wins / len(rows) * 100 if rows else 0.0,

        "average_net_r": (

            sum(values) / len(values)

            if values

            else 0.0

        ),

        "median_net_r": (

            statistics.median(values)

            if values

            else 0.0

        ),

        "total_net_r": sum(values),

        "profit_factor": profit_factor(values),

        "max_drawdown_r": max_drawdown(values),

    }





def print_summary(title, rows):

    summary = summarize(rows)

    distribution = Counter(

        row.get("path_result", "UNKNOWN")

        for row in rows

    )



    print(title)

    print(f"- 표본: {summary['count']}건")



    if not rows:

        print("- 아직 성숙한 전진 검증 결과 없음")

        print("")

        return



    print(

        f"- 비용 차감 승률: {summary['win_rate']:.1f}% "

        f"({summary['wins']}/{summary['count']})"

    )

    print(

        f"- 패배 {summary['losses']} / "

        f"중립 {summary['flats']}"

    )

    print(

        f"- 평균 순성과: {summary['average_net_r']:.3f}R"

    )

    print(

        f"- 중앙값 순성과: {summary['median_net_r']:.3f}R"

    )

    print(

        f"- 누적 순성과: {summary['total_net_r']:.3f}R"

    )



    pf = summary["profit_factor"]

    print(

        "- Profit Factor: "

        + ("∞" if math.isinf(pf) else f"{pf:.2f}")

    )

    print(

        f"- 최대 낙폭: {summary['max_drawdown_r']:.3f}R"

    )

    print(

        "- 경로 분포: "

        + ", ".join(

            f"{key}={value}"

            for key, value in sorted(distribution.items())

        )

    )

    print("")





def main():

    if not CONFIG.exists():

        raise SystemExit(

            "먼저 start_forward_experiment.py를 실행하세요."

        )



    config = load_json(CONFIG)

    started_at = parse_ts(config["started_at"])

    policy = config["policy"]



    total_cost_bps = number(

        config["cost_model"]["total_cost_bps"]

    )



    rows = deduplicate(load_jsonl(OUTCOMES))



    forward = []



    for row in rows:

        created_at = parse_ts(row.get("created_at"))



        if not created_at or created_at < started_at:

            continue



        forward.append(

            add_cost(row, total_cost_bps)

        )



    strict = [

        row for row in forward

        if is_strict(row, policy)

    ]



    print("[BitSwipe Forward Validation V1]")

    print("")

    print(f"실험 ID: {config['experiment_id']}")

    print(f"시작 시각: {config['started_at']}")

    print(f"기준 커밋: {config['baseline_commit']}")

    print(f"총 거래비용 가정: {total_cost_bps:.1f} bp")

    print("")



    print_summary("전체 전진 검증 성과", forward)

    print_summary("STRICT 전진 검증 성과", strict)



    requirements = config["promotion_requirements"]

    strict_stats = summarize(strict)



    checks = [

        (

            f"STRICT 표본 "

            f"{requirements['minimum_strict_forward_samples']}건 이상",

            strict_stats["count"]

            >= requirements["minimum_strict_forward_samples"],

        ),

        (

            "STRICT 평균 순 R 양수",

            strict_stats["average_net_r"]

            > requirements["minimum_average_net_r"],

        ),

        (

            f"STRICT Profit Factor "

            f"{requirements['minimum_profit_factor']:.2f} 이상",

            strict_stats["profit_factor"]

            >= requirements["minimum_profit_factor"],

        ),

        (

            f"STRICT 최대 낙폭 "

            f"{requirements['maximum_drawdown_r']:.1f}R 이하",

            strict_stats["max_drawdown_r"]

            <= requirements["maximum_drawdown_r"],

        ),

    ]



    print("승격 심사:")



    for name, passed in checks:

        print(f"- [{'PASS' if passed else 'FAIL'}] {name}")



    print("")



    if strict_stats["count"] == 0:

        print("판정: 전진 검증을 막 시작함. 데이터 축적 대기.")

    elif all(passed for _, passed in checks):

        print("판정: 초소액 수동 파일럿 확대 검토 가능.")

        print("자동매매 승격은 별도 검증이 필요함.")

    else:

        print("판정: 검증 부족. 자동매매 금지 유지.")



    print("")

    print("최근 STRICT 전진 결과:")



    if not strict:

        print("- 없음")



    for row in strict[-10:]:

        print(

            f"- {row.get('symbol')} "

            f"{row.get('direction')} "

            f"{row.get('path_result')} "

            f"net={number(row.get('net_r')):.3f}R "

            f"MFE={number(row.get('mfe_pct')):.3f}% "

            f"MAE={number(row.get('mae_pct')):.3f}%"

        )





if __name__ == "__main__":

    main()


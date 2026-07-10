
#!/usr/bin/env python3

import argparse

import json

import math

import statistics

from collections import Counter, defaultdict

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

OUTCOME_LOG = ROOT / "logs/trade_path_outcomes.jsonl"





def load_jsonl(path):

    rows = []



    if not path.exists():

        return rows



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



    return list(latest.values())





def is_strict(row):

    score = number(row.get("score"))

    rr = number(row.get("planned_rr"))



    return (

        row.get("market_type") == "BINANCE"

        and row.get("direction") == "SHORT"

        and 30 <= score <= 59

        and rr >= 2.0

    )





def add_net_result(row, total_cost_bps):

    result = dict(row)



    risk_pct = number(result.get("risk_pct"))

    conservative_r = number(result.get("conservative_r"))



    cost_pct = total_cost_bps / 100.0

    cost_r = cost_pct / risk_pct if risk_pct > 0 else 0.0

    net_r = conservative_r - cost_r



    result["assumed_total_cost_bps"] = total_cost_bps

    result["assumed_cost_pct"] = cost_pct

    result["cost_r"] = cost_r

    result["net_r"] = net_r



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





def stats(rows):

    values = [number(row.get("net_r")) for row in rows]

    gross_values = [

        number(row.get("conservative_r"))

        for row in rows

    ]



    wins = sum(value > 0 for value in values)

    losses = sum(value < 0 for value in values)

    flats = len(values) - wins - losses



    return {

        "count": len(rows),

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "win_rate": wins / len(rows) * 100 if rows else 0.0,

        "avg_gross_r": (

            sum(gross_values) / len(gross_values)

            if gross_values

            else 0.0

        ),

        "avg_net_r": (

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





def print_stats(title, rows):

    summary = stats(rows)

    results = Counter(

        row.get("path_result", "UNKNOWN")

        for row in rows

    )



    print(title)

    print(f"- 표본: {summary['count']}건")



    if not rows:

        print("- 평가 결과 없음")

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

        f"- 평균 보수적 총 R: "

        f"{summary['avg_gross_r']:.3f}R"

    )

    print(

        f"- 평균 비용 차감 R: "

        f"{summary['avg_net_r']:.3f}R"

    )

    print(

        f"- 중앙값 비용 차감 R: "

        f"{summary['median_net_r']:.3f}R"

    )

    print(

        f"- 누적 비용 차감 R: "

        f"{summary['total_net_r']:.3f}R"

    )



    pf = summary["profit_factor"]

    pf_text = "∞" if math.isinf(pf) else f"{pf:.2f}"



    print(f"- Profit Factor: {pf_text}")

    print(

        f"- 최대 누적 낙폭: "

        f"{summary['max_drawdown_r']:.3f}R"

    )

    print(

        "- 경로 분포: "

        + ", ".join(

            f"{key}={value}"

            for key, value in sorted(results.items())

        )

    )

    print("")





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--fee-bps",

        type=float,

        default=10.0,

        help="왕복 수수료 가정",

    )

    parser.add_argument(

        "--slippage-bps",

        type=float,

        default=4.0,

        help="왕복 슬리피지 가정",

    )



    args = parser.parse_args()



    total_cost_bps = args.fee_bps + args.slippage_bps



    raw_rows = load_jsonl(OUTCOME_LOG)

    rows = deduplicate(raw_rows)



    rows = [

        add_net_result(row, total_cost_bps)

        for row in rows

    ]



    rows.sort(

        key=lambda row: str(row.get("created_at") or "")

    )



    strict_rows = [

        row for row in rows

        if is_strict(row)

    ]



    print("[BitSwipe Trade Path Scoreboard]")

    print("")

    print("비용 가정:")

    print(f"- 왕복 수수료: {args.fee_bps:.1f} bp")

    print(f"- 왕복 슬리피지: {args.slippage_bps:.1f} bp")

    print(f"- 총비용: {total_cost_bps:.1f} bp")

    print("- AMBIGUOUS는 보수적으로 -1R 처리")

    print("")



    print_stats("전체 Trade Path 성과", rows)

    print_stats("STRICT Trade Path 성과", strict_rows)



    print("심볼별 성과:")



    by_symbol = defaultdict(list)



    for row in rows:

        by_symbol[row.get("symbol", "UNKNOWN")].append(row)



    for symbol, items in sorted(

        by_symbol.items(),

        key=lambda item: (

            len(item[1]),

            stats(item[1])["total_net_r"],

        ),

        reverse=True,

    ):

        summary = stats(items)



        warning = ""

        if (

            summary["count"] >= 2

            and summary["avg_net_r"] <= 0

        ):

            warning = " ⚠️ 주의"



        print(

            f"- {symbol}: n={summary['count']} "

            f"승률={summary['win_rate']:.1f}% "

            f"평균={summary['avg_net_r']:.3f}R "

            f"누적={summary['total_net_r']:.3f}R"

            f"{warning}"

        )



    print("")

    print("최근 Trade Path 결과:")



    for row in rows[-15:]:

        print(

            f"- {row.get('symbol')} "

            f"{row.get('direction')} "

            f"{row.get('path_result')} "

            f"gross={number(row.get('conservative_r')):.3f}R "

            f"cost={number(row.get('cost_r')):.3f}R "

            f"net={number(row.get('net_r')):.3f}R "

            f"MFE={number(row.get('mfe_pct')):.3f}% "

            f"MAE={number(row.get('mae_pct')):.3f}%"

        )



    print("")

    print("운영 판정:")



    strict_summary = stats(strict_rows)



    conditions = [

        ("STRICT 표본 30건 이상", strict_summary["count"] >= 30),

        ("STRICT 평균 순 R 양수", strict_summary["avg_net_r"] > 0),

        ("STRICT Profit Factor 1.20 이상",

         strict_summary["profit_factor"] >= 1.20),

        ("STRICT 최대 낙폭 5R 이하",

         strict_summary["max_drawdown_r"] <= 5.0),

    ]



    for name, passed in conditions:

        print(f"- [{'PASS' if passed else 'FAIL'}] {name}")



    if all(passed for _, passed in conditions):

        print("판정: 초소액 수동 파일럿 확대 검토 가능.")

        print("자동매매 전환은 별도의 전진 검증이 필요함.")

    else:

        print("판정: 검증 부족. 자동매매 금지 유지.")





if __name__ == "__main__":

    main()


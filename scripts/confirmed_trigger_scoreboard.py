
#!/usr/bin/env python3

import json

import statistics

from collections import Counter

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

WATCH_PATH = ROOT / "logs/confirmed_path_watch.jsonl"

OUTCOME_PATH = ROOT / "logs/confirmed_path_outcomes.jsonl"

MIN_SAMPLE = 30





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





def number(value):

    try:

        return float(value)

    except Exception:

        return None





def fmt(value):

    return "-" if value is None else f"{value:+.3f}R"





def main():

    watches = read_jsonl(WATCH_PATH)

    outcomes = [

        row

        for row in read_jsonl(OUTCOME_PATH)

        if row.get("plan_version") == "confirmed_trigger_v1"

    ]



    results = Counter(

        str(row.get("result", "UNKNOWN"))

        for row in outcomes

    )



    net_values = [

        value

        for row in outcomes

        if (value := number(row.get("net_r"))) is not None

    ]



    mfe_values = [

        value

        for row in outcomes

        if (value := number(row.get("mfe_r"))) is not None

    ]



    mae_values = [

        value

        for row in outcomes

        if (value := number(row.get("mae_r"))) is not None

    ]



    wins = results["TARGET_FIRST"]

    losses = results["STOP_FIRST"]

    settled = wins + losses



    positive_sum = sum(

        value

        for value in net_values

        if value > 0

    )



    negative_sum = abs(

        sum(

            value

            for value in net_values

            if value < 0

        )

    )



    if negative_sum > 0:

        profit_factor = positive_sum / negative_sum

        pf_text = f"{profit_factor:.2f}"

    elif positive_sum > 0:

        pf_text = "∞"

    else:

        pf_text = "-"



    target_1_hits = sum(

        bool(row.get("target_1_hit"))

        for row in outcomes

    )



    print("[Confirmed Trigger V1 Scoreboard]")

    print("")

    print("표본:")

    print(f"- 등록된 확정 시나리오: {len(watches)}")

    print(f"- 240분 평가 완료: {len(outcomes)}")

    print(f"- 검증 목표: {len(outcomes)}/{MIN_SAMPLE}")



    if len(outcomes) < MIN_SAMPLE:

        print("- 상태: 표본 수집 중")

    else:

        print("- 상태: 1차 검토 가능")



    print("")

    print("결과 분포:")

    print(f"- TARGET_FIRST: {wins}")

    print(f"- STOP_FIRST: {losses}")

    print(f"- AMBIGUOUS: {results['AMBIGUOUS']}")

    print(f"- OPEN_AT_240M: {results['OPEN_AT_240M']}")



    print("")

    print("성과:")

    win_rate = wins / settled * 100 if settled else None

    print(

        "- 확정 승패 기준 승률: "

        + (

            f"{win_rate:.1f}% ({wins}/{settled})"

            if win_rate is not None

            else "-"

        )

    )



    average_net = (

        statistics.mean(net_values)

        if net_values

        else None

    )



    median_net = (

        statistics.median(net_values)

        if net_values

        else None

    )



    average_mfe = (

        statistics.mean(mfe_values)

        if mfe_values

        else None

    )



    average_mae = (

        statistics.mean(mae_values)

        if mae_values

        else None

    )



    print(f"- 평균 순손익: {fmt(average_net)}")

    print(f"- 중앙값 순손익: {fmt(median_net)}")

    print(f"- Profit Factor: {pf_text}")

    print(f"- 평균 MFE: {fmt(average_mfe)}")

    print(f"- 평균 MAE: {fmt(average_mae)}")



    target_1_rate = (

        target_1_hits / len(outcomes) * 100

        if outcomes

        else None

    )



    print(

        "- 1차 목표 도달률: "

        + (

            f"{target_1_rate:.1f}% "

            f"({target_1_hits}/{len(outcomes)})"

            if target_1_rate is not None

            else "-"

        )

    )



    print("")

    print("판정:")

    if len(outcomes) < MIN_SAMPLE:

        print(

            "- 아직 전략 성능을 판단하지 않습니다."

        )

        print(

            "- 최소 30개까지 위험 모드 PILOT을 유지합니다."

        )

    elif average_net is not None and average_net > 0:

        print(

            "- 평균 순손익은 양수입니다."

        )

        print(

            "- 최초 후보 성과와 비교 검토가 필요합니다."

        )

    else:

        print(

            "- 평균 순손익이 양수가 아닙니다."

        )

        print(

            "- 위험률 상향을 금지합니다."

        )





if __name__ == "__main__":

    main()



#!/usr/bin/env python3

import json

import statistics

from collections import Counter

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

INITIAL_PATH = ROOT / "logs/trade_path_outcomes.jsonl"

CONFIRMED_PATH = ROOT / "logs/confirmed_path_outcomes.jsonl"





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

            pass



    return rows





def find(row, *names):

    sources = [row]



    for key in ("candidate", "metadata", "plan"):

        value = row.get(key)

        if isinstance(value, dict):

            sources.append(value)



    for source in sources:

        for name in names:

            if source.get(name) is not None:

                return source.get(name)



    return None





def number(value):

    try:

        return float(value)

    except Exception:

        return None





def strict_initial(row):

    plan = str(find(row, "plan_version") or "")

    direction = str(find(row, "direction") or "").upper()

    exchange = str(find(row, "exchange", "venue") or "BINANCE").upper()



    score = number(

        find(row, "score", "rule_score", "candidate_score")

    )

    rr = number(

        find(row, "planned_rr", "rr", "risk_reward")

    )



    if plan and plan != "scanner_rr_v1":

        return False



    return (

        exchange == "BINANCE"

        and direction == "SHORT"

        and score is not None

        and 30 <= score <= 59

        and rr is not None

        and rr >= 2.0

    )





def result_r(row):

    for key in (

        "net_r",

        "conservative_r",

        "cost_adjusted_r",

        "gross_r",

    ):

        value = number(row.get(key))

        if value is not None:

            return value



    return None





def summarize(rows):

    results = Counter(

        str(row.get("result", "UNKNOWN"))

        for row in rows

    )



    values = [

        value

        for row in rows

        if (value := result_r(row)) is not None

    ]



    wins = results["TARGET_FIRST"]

    losses = results["STOP_FIRST"]

    settled = wins + losses



    positive = sum(value for value in values if value > 0)

    negative = abs(sum(value for value in values if value < 0))



    return {

        "count": len(rows),

        "wins": wins,

        "losses": losses,

        "ambiguous": results["AMBIGUOUS"],

        "open": results["OPEN_AT_240M"],

        "win_rate": wins / settled * 100 if settled else None,

        "average": statistics.mean(values) if values else None,

        "median": statistics.median(values) if values else None,

        "pf": positive / negative if negative else None,

    }





def show(title, data):

    print(title)

    print(f"- 평가 완료: {data['count']}")

    print(f"- TARGET_FIRST: {data['wins']}")

    print(f"- STOP_FIRST: {data['losses']}")

    print(f"- AMBIGUOUS: {data['ambiguous']}")

    print(f"- OPEN_AT_240M: {data['open']}")



    if data["win_rate"] is None:

        print("- 승률: -")

    else:

        print(f"- 승률: {data['win_rate']:.1f}%")



    if data["average"] is None:

        print("- 평균 손익: -")

        print("- 중앙값 손익: -")

    else:

        print(f"- 평균 손익: {data['average']:+.3f}R")

        print(f"- 중앙값 손익: {data['median']:+.3f}R")



    print(

        "- Profit Factor: "

        + (f"{data['pf']:.2f}" if data["pf"] is not None else "-")

    )





def main():

    initial = [

        row

        for row in read_jsonl(INITIAL_PATH)

        if strict_initial(row)

    ]



    confirmed = [

        row

        for row in read_jsonl(CONFIRMED_PATH)

        if row.get("plan_version") == "confirmed_trigger_v1"

    ]



    initial_stats = summarize(initial)

    confirmed_stats = summarize(confirmed)



    print("[BitSwipe Entry Confirmation Comparison]")

    print("")

    show("① 최초 STRICT 후보", initial_stats)

    print("")

    show("② 15분봉 확인 후 진입", confirmed_stats)

    print("")

    print("비교 판정:")



    if (

        initial_stats["count"] < 30

        or confirmed_stats["count"] < 30

    ):

        print("- 아직 비교 표본이 부족합니다.")

        print("- 두 방식의 우열을 판단하지 않습니다.")

        print("- 위험 모드 PILOT 0.25%를 유지합니다.")

    else:

        difference = (

            confirmed_stats["average"]

            - initial_stats["average"]

        )

        print(

            f"- 확인 진입 평균 손익 차이: {difference:+.3f}R"

        )

        print("- 표본 구조가 다르므로 인과관계로 해석하지 않습니다.")





if __name__ == "__main__":

    main()


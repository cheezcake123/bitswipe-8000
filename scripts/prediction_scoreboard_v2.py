
#!/usr/bin/env python3

import json

import math

import statistics

from pathlib import Path

from collections import defaultdict



ROOT = Path(__file__).resolve().parents[1]

PREDICTION_LOG = ROOT / "logs/prediction_watch.jsonl"

OUTCOME_LOG = ROOT / "logs/prediction_outcomes.jsonl"



PRIMARY_HORIZON = 240



STRICT_MIN_SCORE = 30

STRICT_MAX_SCORE = 59

STRICT_MIN_RR = 2.0





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





def fnum(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default





def is_strict(row):

    return (

        row.get("market_type") == "BINANCE"

        and row.get("direction") == "SHORT"

        and STRICT_MIN_SCORE

        <= fnum(row.get("score"))

        <= STRICT_MAX_SCORE

        and fnum(row.get("rr")) >= STRICT_MIN_RR

    )





def wilson_interval(wins, total, z=1.96):

    if total <= 0:

        return 0.0, 0.0



    p = wins / total

    denominator = 1 + z * z / total



    center = (

        p + z * z / (2 * total)

    ) / denominator



    margin = (

        z

        * math.sqrt(

            p * (1 - p) / total

            + z * z / (4 * total * total)

        )

        / denominator

    )



    return (

        max(0.0, center - margin),

        min(1.0, center + margin),

    )





def deduplicated_outcomes(predictions, outcomes):

    prediction_by_id = {

        row.get("prediction_id"): row

        for row in predictions

        if row.get("prediction_id")

    }



    # 같은 prediction_id와 horizon이 중복 기록됐다면

    # 마지막 기록만 사용한다.

    latest_by_key = {}



    for outcome in outcomes:

        prediction_id = outcome.get("prediction_id")

        horizon = int(fnum(outcome.get("horizon_min"), 0))



        if not prediction_id or horizon <= 0:

            continue



        merged = {}

        merged.update(prediction_by_id.get(prediction_id, {}))

        merged.update(outcome)



        merged["prediction_id"] = prediction_id

        merged["horizon_min"] = horizon



        latest_by_key[(prediction_id, horizon)] = merged



    return list(latest_by_key.values())





def summarize(rows):

    total = len(rows)

    wins = sum(r.get("outcome") == "WIN" for r in rows)

    losses = sum(r.get("outcome") == "LOSS" for r in rows)

    flats = sum(r.get("outcome") == "FLAT" for r in rows)



    returns = [

        fnum(r.get("direction_return_pct"))

        for r in rows

    ]



    avg_return = (

        sum(returns) / len(returns)

        if returns

        else 0.0

    )



    median_return = (

        statistics.median(returns)

        if returns

        else 0.0

    )



    decisive = wins + losses

    decisive_win_rate = (

        wins / decisive * 100

        if decisive

        else 0.0

    )



    lower, upper = wilson_interval(wins, decisive)



    return {

        "total": total,

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "decisive": decisive,

        "win_rate_all": (

            wins / total * 100

            if total

            else 0.0

        ),

        "win_rate_decisive": decisive_win_rate,

        "wilson_low": lower * 100,

        "wilson_high": upper * 100,

        "avg_return": avg_return,

        "median_return": median_return,

    }





def print_summary(title, rows):

    s = summarize(rows)



    print(title)

    print(f"- 고유 예측 결과: {s['total']}건")



    if not s["total"]:

        print("- 아직 평가 가능한 표본 없음")

        print("")

        return



    print(

        f"- 전체 적중률: "

        f"{s['win_rate_all']:.1f}% "

        f"({s['wins']}/{s['total']})"

    )

    print(

        f"- 승패 결정 건 승률: "

        f"{s['win_rate_decisive']:.1f}% "

        f"({s['wins']}/{s['decisive']})"

    )

    print(

        f"- 95% 승률 범위: "

        f"{s['wilson_low']:.1f}%"

        f"~{s['wilson_high']:.1f}%"

    )

    print(

        f"- 패배 {s['losses']} / "

        f"중립 {s['flats']}"

    )

    print(

        f"- 평균 방향수익: "

        f"{s['avg_return']:.3f}%"

    )

    print(

        f"- 중앙값 방향수익: "

        f"{s['median_return']:.3f}%"

    )

    print("")





def main():

    predictions = load_jsonl(PREDICTION_LOG)

    raw_outcomes = load_jsonl(OUTCOME_LOG)



    outcomes = deduplicated_outcomes(

        predictions,

        raw_outcomes,

    )



    prediction_ids = {

        r.get("prediction_id")

        for r in predictions

        if r.get("prediction_id")

    }



    completed_ids = {

        r.get("prediction_id")

        for r in outcomes

        if r.get("prediction_id")

    }



    print("[BitSwipe Prediction Scoreboard V2]")

    print("")

    print("집계 원칙:")

    print("- prediction_id + horizon별 중복 제거")

    print("- 서로 다른 시간축 결과를 한 승률로 합치지 않음")

    print(f"- 핵심 평가 시간축: {PRIMARY_HORIZON}분")

    print("")



    print(f"저장된 고유 prediction: {len(prediction_ids)}개")

    print(

        f"하나 이상 채점된 prediction: "

        f"{len(completed_ids)}개"

    )

    print(

        f"중복 제거된 horizon outcome: "

        f"{len(outcomes)}개"

    )

    print("")



    horizons = sorted({

        int(fnum(r.get("horizon_min"), 0))

        for r in outcomes

        if fnum(r.get("horizon_min"), 0) > 0

    })



    for horizon in horizons:

        rows = [

            r for r in outcomes

            if int(fnum(r.get("horizon_min"), 0))

            == horizon

        ]

        print_summary(

            f"{horizon}분 전체 성과",

            rows,

        )



    primary = [

        r for r in outcomes

        if int(fnum(r.get("horizon_min"), 0))

        == PRIMARY_HORIZON

    ]



    strict_primary = [

        r for r in primary

        if is_strict(r)

    ]



    print("=" * 60)

    print_summary(

        f"핵심 {PRIMARY_HORIZON}분 전체 성과",

        primary,

    )

    print_summary(

        f"핵심 {PRIMARY_HORIZON}분 STRICT 성과",

        strict_primary,

    )



    print(

        f"{PRIMARY_HORIZON}분 심볼별 성과:"

    )



    by_symbol = defaultdict(list)

    for row in primary:

        by_symbol[row.get("symbol", "UNKNOWN")].append(row)



    if not by_symbol:

        print("- 표본 없음")



    for symbol, rows in sorted(

        by_symbol.items(),

        key=lambda item: len(item[1]),

        reverse=True,

    ):

        s = summarize(rows)



        warning = ""

        if (

            s["total"] >= 3

            and (

                s["win_rate_all"] < 50

                or s["avg_return"] <= 0

            )

        ):

            warning = " ⚠️ 주의"



        print(

            f"- {symbol}: "

            f"{s['wins']}승 {s['losses']}패 "

            f"{s['flats']}중립 / "

            f"평균 {s['avg_return']:.3f}%"

            f"{warning}"

        )



    print("")

    print("STRICT 정책 승격 심사:")



    strict_stats = summarize(strict_primary)



    checks = [

        (

            "표본 30건 이상",

            strict_stats["total"] >= 30,

        ),

        (

            "승패 결정 승률 55% 이상",

            strict_stats["win_rate_decisive"] >= 55,

        ),

        (

            "평균 방향수익 양수",

            strict_stats["avg_return"] > 0,

        ),

        (

            "승률 신뢰구간 하단 45% 이상",

            strict_stats["wilson_low"] >= 45,

        ),

    ]



    for name, passed in checks:

        mark = "PASS" if passed else "FAIL"

        print(f"- [{mark}] {name}")



    if all(passed for _, passed in checks):

        print("")

        print(

            "판정: 초소액 수동 파일럿 확대 검토 가능."

        )

        print(

            "그래도 자동매매 전환은 별도 검증이 필요함."

        )

    else:

        print("")

        print(

            "판정: 표본 또는 안정성 부족."

        )

        print(

            "자동매매 금지, 초소액 수동 검토만 유지."

        )



    print("")

    print(f"최근 {PRIMARY_HORIZON}분 outcome:")



    for row in primary[-15:]:

        print(

            f"- {row.get('symbol')} "

            f"{row.get('direction')} "

            f"{row.get('outcome')} "

            f"ret="

            f"{fnum(row.get('direction_return_pct')):.3f}% "

            f"score={fnum(row.get('score')):.0f} "

            f"rr={fnum(row.get('rr')):.2f}"

        )





if __name__ == "__main__":

    main()


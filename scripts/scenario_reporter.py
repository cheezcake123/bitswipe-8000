
#!/usr/bin/env python3

import argparse

import json

from datetime import datetime, timezone

from pathlib import Path



from prediction_watch_store import virtual_trade_plan



ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_LOG = ROOT / "logs/candidates.jsonl"



LATEST_BATCH_GAP_SECONDS = 10



STRICT_MIN_SCORE = 30

STRICT_MAX_SCORE = 59

STRICT_MIN_RR = 2.0



MAX_ACCOUNT_RISK_PCT = 0.25

MAX_LEVERAGE = 2.0





def number(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default





def value(row, *names, default=None):

    for name in names:

        if row.get(name) is not None:

            return row.get(name)

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





def load_rows(limit=10000):

    if not CANDIDATE_LOG.exists():

        return []



    rows = []



    for line in CANDIDATE_LOG.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines()[-limit:]:

        try:

            raw = json.loads(line)

        except Exception:

            continue



        timestamp = parse_ts(raw.get("ts"))

        entry = number(

            value(

                raw,

                "last",

                "entry",

                "price",

                "close",

                default=0,

            )

        )



        if not timestamp or entry <= 0:

            continue



        rows.append({

            "ts": timestamp,

            "symbol": str(

                value(raw, "symbol", default="?")

            ),

            "market_type": str(

                value(

                    raw,

                    "market_type",

                    default="UNKNOWN",

                )

            ),

            "direction": str(

                value(

                    raw,

                    "direction",

                    default="WAIT",

                )

            ),

            "score": number(

                value(

                    raw,

                    "score",

                    "rule_score",

                    default=0,

                )

            ),

            "rr": number(

                value(

                    raw,

                    "estimated_rr",

                    "rr",

                    "risk_reward",

                    default=0,

                )

            ),

            "grade": str(

                value(raw, "grade", default="?")

            ),

            "entry": entry,

            "support": number(

                value(raw, "support", default=0)

            ),

            "resistance": number(

                value(raw, "resistance", default=0)

            ),

            "atr_pct": number(

                value(raw, "atr_pct", default=0)

            ),

            "blocked_reason": str(

                value(

                    raw,

                    "blocked_reason",

                    "block_reason",

                    default="UNKNOWN",

                )

            ),

            "reasons": value(

                raw,

                "reasons",

                default=[],

            ) or [],

        })



    return rows





def latest_batch(rows):

    if not rows:

        return []



    rows = sorted(rows, key=lambda row: row["ts"])

    newest = rows[-1]["ts"]



    return [

        row

        for row in rows

        if (

            newest - row["ts"]

        ).total_seconds() <= LATEST_BATCH_GAP_SECONDS

    ]





def candidate_class(row):

    if row["market_type"] != "BINANCE":

        return None



    if row["direction"] not in ("LONG", "SHORT"):

        return None



    if row["rr"] < STRICT_MIN_RR:

        return None



    if (

        row["direction"] == "SHORT"

        and STRICT_MIN_SCORE

        <= row["score"]

        <= STRICT_MAX_SCORE

    ):

        return "PILOT_ELIGIBLE_STRICT"



    return "WATCH_STRONG_RR_NOT_STRICT"





def format_price(price):

    price = number(price)



    if price >= 1000:

        return f"{price:,.2f}"



    if price >= 100:

        return f"{price:,.3f}"



    if price >= 1:

        return f"{price:,.4f}"



    if price >= 0.01:

        return f"{price:.6f}"



    return f"{price:.8f}"





def make_entry_zone(row, plan):

    entry = row["entry"]

    stop = plan["virtual_stop"]

    risk = abs(stop - entry)



    if row["direction"] == "SHORT":

        reference = max(

            entry,

            row["resistance"],

        )



        upper = min(

            reference,

            stop - risk * 0.15,

        )



        upper = max(entry, upper)



        return entry, upper



    reference = min(

        entry,

        row["support"]

        if row["support"] > 0

        else entry,

    )



    lower = max(

        reference,

        stop + risk * 0.15,

    )



    lower = min(entry, lower)



    return lower, entry





def make_targets(row, plan):

    entry = row["entry"]

    final_target = plan["virtual_target"]



    first_target = (

        entry

        + (final_target - entry) * 0.5

    )



    return first_target, final_target





def position_size_pct(risk_pct):

    if risk_pct <= 0:

        return 0.0



    raw = (

        MAX_ACCOUNT_RISK_PCT

        / risk_pct

        * 100.0

    )



    return min(raw, MAX_LEVERAGE * 100.0)





def scenario_report(row, classification):

    plan = virtual_trade_plan(

        direction=row["direction"],

        entry=row["entry"],

        support=row["support"],

        resistance=row["resistance"],

        atr_pct=row["atr_pct"],

    )



    if not plan:

        return None



    zone_low, zone_high = make_entry_zone(

        row,

        plan,

    )



    target_1, target_2 = make_targets(

        row,

        plan,

    )



    risk_pct = plan["risk_pct"]

    position_pct = position_size_pct(risk_pct)



    strict = (

        classification

        == "PILOT_ELIGIBLE_STRICT"

    )



    if row["direction"] == "SHORT":

        view = "저항 반응을 기다리는 조건부 숏"

        confirmation = [

            (

                f"15분봉이 저항 "

                f"{format_price(row['resistance'])} "

                f"아래에서 마감"

            ),

            (

                "반등 재시험에서 이전 고점을 "

                "넘지 못하고 낮은 고점 형성"

            ),

            (

                "진입 직전 가격이 손절선과 "

                "지나치게 가까워지지 않을 것"

            ),

        ]



        invalidation = [

            (

                f"15분봉이 무효화 가격 "

                f"{format_price(plan['virtual_stop'])} "

                f"위에서 마감"

            ),

            (

                f"저항 {format_price(row['resistance'])} "

                f"돌파 후 지지 전환"

            ),

            "거래량을 동반한 상승 추세 재개",

        ]



        chase_condition = (

            f"가격이 {format_price(target_1)} "

            f"아래로 먼저 급락하면 추격 금지"

        )



    else:

        view = "지지 반응을 기다리는 조건부 롱"

        confirmation = [

            (

                f"15분봉이 지지 "

                f"{format_price(row['support'])} "

                f"위에서 마감"

            ),

            (

                "하락 재시험에서 이전 저점을 "

                "깨지 않고 높은 저점 형성"

            ),

            (

                "진입 직전 가격이 손절선과 "

                "지나치게 가까워지지 않을 것"

            ),

        ]



        invalidation = [

            (

                f"15분봉이 무효화 가격 "

                f"{format_price(plan['virtual_stop'])} "

                f"아래에서 마감"

            ),

            (

                f"지지 {format_price(row['support'])} "

                f"이탈 후 저항 전환"

            ),

            "거래량을 동반한 하락 추세 재개",

        ]



        chase_condition = (

            f"가격이 {format_price(target_1)} "

            f"위로 먼저 급등하면 추격 금지"

        )



    verdict = (

        "조건 충족 후 초소액 수동 검토"

        if strict

        else "관찰 전용 — 실전 진입 기준 미충족"

    )



    classification_ko = {"PILOT_ELIGIBLE_STRICT": "엄격 조건 충족", "WATCH_STRONG_RR_NOT_STRICT": "손익비 양호 관찰 대상"}.get(classification, classification)

    lines = [

        "[BitSwipe 한국어 조건부 매매 시나리오]",

        "",

        f"종목: {row['symbol']}",

        f"등급: {row['grade']}",

        f"분류: {classification_ko}",

        f"관점: {view}",

        f"기준 가격: {format_price(row['entry'])}",

        "",

        "핵심 해석:",

        (

            f"- 점수 {row['score']:.0f}, "

            f"예상 손익비 {plan['planned_rr']:.2f}:1"

        ),

        (

            f"- 지지 {format_price(row['support'])} / "

            f"저항 {format_price(row['resistance'])}"

        ),

        "- 지금 즉시 시장가 진입 신호가 아님",

        "",

        "예상 진입 구간:",

        (

            f"- {format_price(zone_low)}"

            f" ~ {format_price(zone_high)}"

        ),

        "",

        "진입 확인 조건:",

    ]



    for index, item in enumerate(

        confirmation,

        start=1,

    ):

        lines.append(f"{index}) {item}")



    lines.extend([

        "",

        "손절 및 관점 무효화:",

        (

            f"- 가상 손절: "

            f"{format_price(plan['virtual_stop'])}"

        ),

    ])



    for item in invalidation:

        lines.append(f"- {item}")



    lines.extend([

        "",

        "목표 구간:",

        f"- 1차 목표: {format_price(target_1)}",

        f"- 2차 목표: {format_price(target_2)}",

        (

            f"- 총 예상 보상: "

            f"{plan['reward_pct']:.3f}%"

        ),

        (

            f"- 총 예상 위험: "

            f"{plan['risk_pct']:.3f}%"

        ),

        (

            f"- 총 예상 손익비: "

            f"{plan['planned_rr']:.2f}:1"

        ),

        "",

        "진입 금지 조건:",

        f"- {chase_condition}",

        "- 확인 조건이 나오기 전 선진입 금지",

        "- 손절 가격을 넓혀서 손익비를 조작하지 말 것",

        "- 급격한 시장 전체 방향 전환 시 취소",

        "",

        "위험 관리:",

        (

            f"- 계좌 최대 허용 손실: "

            f"{MAX_ACCOUNT_RISK_PCT:.2f}%"

        ),

        (

            f"- 계산상 최대 명목 포지션: "

            f"계좌의 약 {position_pct:.1f}%"

        ),

        f"- 레버리지 상한: {MAX_LEVERAGE:.0f}배",

        "- 하루 2연패 시 매매 중단",

        "",

        "시나리오 유효기간:",

        "- 다음 3개 15분봉 이내에 재평가",

        "- 진입가·손절가·목표가가 달라지면 기존 시나리오 폐기",

        "",

        f"최종 판정: {verdict}",

        "",

        "주의: 자동진입 신호가 아니라 수동 의사결정 보조 보고서입니다.",

    ])



    return "\n".join(lines)





def demo_row():

    return {

        "ts": datetime.now(timezone.utc),

        "symbol": "AAVEUSDT",

        "market_type": "BINANCE",

        "direction": "SHORT",

        "score": 54.0,

        "rr": 2.83,

        "grade": "C",

        "entry": 93.36,

        "support": 90.48,

        "resistance": 94.00,

        "atr_pct": 0.6885,

        "blocked_reason": "LOW_RULE_SCORE",

        "reasons": [

            "4h_move +2.28%",

            "volume x1.84",

            "loose_near_resistance 94",

            "directional_bias SHORT",

            "estimated_rr 2.83",

        ],

    }





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--demo",

        action="store_true",

    )

    parser.add_argument(

        "--include-watch",

        action="store_true",

    )



    args = parser.parse_args()



    if args.demo:

        rows = [demo_row()]

    else:

        rows = latest_batch(load_rows())



    reports = []



    for row in rows:

        classification = candidate_class(row)



        if not classification:

            continue



        if (

            classification

            == "WATCH_STRONG_RR_NOT_STRICT"

            and not args.include_watch

        ):

            continue



        report = scenario_report(

            row,

            classification,

        )



        if report:

            reports.append(report)



    if not reports:

        print("[BitSwipe Scenario Reporter]")

        print("현재 한국어 조건부 시나리오 후보 없음.")

        print("억지 진입 금지. 다음 스캔을 기다리세요.")

        return



    print(

        "\n\n"

        + "=" * 70

        + "\n\n"

    ).join(reports)





if __name__ == "__main__":

    main()


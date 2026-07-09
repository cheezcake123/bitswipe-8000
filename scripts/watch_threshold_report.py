
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import Counter

from datetime import datetime



LOG_PATH = Path("logs/candidates.jsonl")



MIN_SCORE_TO_ANALYZE = 70

MIN_ESTIMATED_RR = 1.20





def parse_ts(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except Exception:

        return None





def val(row, *names, default=None):

    for name in names:

        if name in row and row.get(name) is not None:

            return row.get(name)

    return default





def fnum(x, default=0.0):

    try:

        return float(x)

    except Exception:

        return default





def load_rows(limit=5000):

    if not LOG_PATH.exists():

        return []



    rows = []

    for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:

        try:

            row = json.loads(line)

            row["_ts"] = parse_ts(row.get("ts"))

            rows.append(row)

        except Exception:

            pass

    return rows





def score(row):

    return fnum(val(row, "score", "rule_score", default=0))





def rr(row):

    return fnum(val(row, "estimated_rr", "rr", "risk_reward", default=0))





def direction(row):

    return val(row, "direction", default="UNKNOWN")





def grade(row):

    return val(row, "grade", default="UNKNOWN")





def decision(row):

    return val(row, "decision", default="UNKNOWN")





def symbol(row):

    return val(row, "symbol", default="?")





def logged_blocked_reason(row):

    return val(row, "blocked_reason", "block_reason", "reason", default="UNKNOWN")





def recalculated_reason(row):

    s = score(row)

    r = rr(row)

    d = direction(row)



    if s >= MIN_SCORE_TO_ANALYZE and d != "WAIT" and r >= MIN_ESTIMATED_RR:

        return "AI_ELIGIBLE"



    if d == "WAIT":

        return "NO_CLEAR_DIRECTION"



    if s >= MIN_SCORE_TO_ANALYZE and d != "WAIT" and 0 < r < MIN_ESTIMATED_RR:

        return "HIGH_SCORE_LOW_RR_REVIEW_ONLY"



    if r <= 0:

        return "RR_UNAVAILABLE"



    if r < MIN_ESTIMATED_RR:

        return "LOW_ESTIMATED_RR"



    if s < MIN_SCORE_TO_ANALYZE:

        return "LOW_RULE_SCORE"



    return "NOT_ELIGIBLE_FOR_AI"





def reason_ko(reason):

    mapping = {

        "AI_ELIGIBLE": "AI 후보 통과",

        "NO_CLEAR_DIRECTION": "방향 불명확",

        "LOW_ESTIMATED_RR": "손익비 부족",

        "HIGH_SCORE_LOW_RR_REVIEW_ONLY": "점수는 높지만 손익비 부족",

        "RR_UNAVAILABLE": "손익비 계산 불가",

        "LOW_RULE_SCORE": "룰 점수 부족",

        "NOT_ELIGIBLE_FOR_AI": "AI 검토 조건 미충족",

        "UNKNOWN": "알 수 없음",

    }

    return mapping.get(reason, str(reason))





def show(title, items, limit=12):

    print()

    print(title)



    if not items:

        print("- 없음")

        return



    items = sorted(items, key=lambda r: (score(r), rr(r)), reverse=True)[:limit]



    for row in items:

        print(

            f"- {symbol(row)}: "

            f"{decision(row)}/{grade(row)}, "

            f"방향={direction(row)}, "

            f"점수={score(row):.0f}, "

            f"RR={rr(row):.2f}, "

            f"기록차단={logged_blocked_reason(row)}, "

            f"재계산={recalculated_reason(row)}({reason_ko(recalculated_reason(row))})"

        )





def main():

    rows = load_rows()



    print("[BitSwipe AI 후보 기준 점검 리포트]")

    print()



    if not rows:

        print("candidates.jsonl 로그가 없거나 읽을 수 없음")

        return



    timed = [r for r in rows if r.get("_ts")]

    if timed:

        print(f"분석 로그 수: {len(rows)}개")

        print(f"분석 범위: {min(r['_ts'] for r in timed).isoformat()} ~ {max(r['_ts'] for r in timed).isoformat()}")

    else:

        print(f"분석 로그 수: {len(rows)}개")

    print()



    print("판정 분포:")

    for k, v in Counter(decision(r) for r in rows).most_common():

        print(f"- {k}: {v}개")



    print()

    print("등급 분포:")

    for k, v in Counter(grade(r) for r in rows).most_common():

        print(f"- {k}: {v}개")



    print()

    print("기록된 차단 사유 분포:")

    for k, v in Counter(logged_blocked_reason(r) for r in rows).most_common():

        print(f"- {k}: {v}개")



    print()

    print("현재 기준으로 재계산한 차단 사유 분포:")

    for k, v in Counter(recalculated_reason(r) for r in rows).most_common():

        print(f"- {k} ({reason_ko(k)}): {v}개")



    non_wait = [r for r in rows if direction(r) != "WAIT"]

    score_pass = [r for r in rows if score(r) >= MIN_SCORE_TO_ANALYZE]

    rr_pass = [r for r in rows if rr(r) >= MIN_ESTIMATED_RR]

    all_pass = [

        r for r in rows

        if score(r) >= MIN_SCORE_TO_ANALYZE

        and direction(r) != "WAIT"

        and rr(r) >= MIN_ESTIMATED_RR

    ]



    high_score_low_rr = [

        r for r in rows

        if score(r) >= MIN_SCORE_TO_ANALYZE

        and direction(r) != "WAIT"

        and 0 < rr(r) < MIN_ESTIMATED_RR

    ]



    near_score = [

        r for r in rows

        if 60 <= score(r) < MIN_SCORE_TO_ANALYZE

        and direction(r) != "WAIT"

        and rr(r) >= MIN_ESTIMATED_RR

    ]



    near_rr = [

        r for r in rows

        if score(r) >= MIN_SCORE_TO_ANALYZE

        and direction(r) != "WAIT"

        and MIN_ESTIMATED_RR * 0.8 <= rr(r) < MIN_ESTIMATED_RR

    ]



    print()

    print("AI 후보 조건별 통과/탈락:")

    print(f"- 방향 있음 LONG/SHORT: {len(non_wait)}개 / {len(rows)}개")

    print(f"- 점수 {MIN_SCORE_TO_ANALYZE} 이상: {len(score_pass)}개 / {len(rows)}개")

    print(f"- RR {MIN_ESTIMATED_RR} 이상: {len(rr_pass)}개 / {len(rows)}개")

    print(f"- 세 조건 모두 통과: {len(all_pass)}개 / {len(rows)}개")



    print()

    print("Near-miss 후보:")

    print(f"- 점수는 높지만 RR 부족: {len(high_score_low_rr)}개")

    print(f"- 점수만 약간 부족, RR 통과: {len(near_score)}개")

    print(f"- RR만 약간 부족, 점수 통과: {len(near_rr)}개")



    show("점수는 높지만 RR 부족 후보 TOP", high_score_low_rr)

    show("점수만 약간 부족하고 RR은 괜찮은 후보 TOP", near_score)

    show("RR만 약간 부족하고 점수는 괜찮은 후보 TOP", near_rr)



    print()

    print("심볼별 최고 근접 후보 TOP:")



    best = {}

    for row in rows:

        sym = symbol(row)

        rank = (

            1 if direction(row) != "WAIT" else 0,

            score(row),

            rr(row),

        )

        if sym not in best or rank > best[sym][0]:

            best[sym] = (rank, row)



    for _, row in sorted(best.values(), key=lambda x: x[0], reverse=True)[:20]:

        print(

            f"- {symbol(row)}: "

            f"{decision(row)}/{grade(row)}, "

            f"방향={direction(row)}, "

            f"점수={score(row):.0f}, "

            f"RR={rr(row):.2f}, "

            f"기록차단={logged_blocked_reason(row)}, "

            f"재계산={recalculated_reason(row)}({reason_ko(recalculated_reason(row))})"

        )



    print()

    print("해석:")

    if all_pass:

        print("- 최근 로그 기준 AI 후보 조건을 통과한 후보가 있었음. 알림/AI 호출 로그를 확인할 가치가 있음.")

    else:

        print("- 최근 로그 기준 AI 후보 세 조건을 모두 통과한 후보는 없음.")



    if high_score_low_rr:

        print("- 점수는 높은데 RR이 낮은 후보가 있음. 진입 금지는 맞지만 review_only로 따로 추적할 가치가 있음.")



    if near_score:

        print("- 점수 기준만 약간 낮은 후보가 있음. MIN_SCORE_TO_ANALYZE 완화 여부를 검토할 수 있음.")



    if near_rr:

        print("- RR 기준만 약간 낮은 후보가 있음. MIN_ESTIMATED_RR 1.20 유지 여부를 검토할 수 있음.")



    if not high_score_low_rr and not near_score and not near_rr:

        print("- 대부분 명확히 기준 미달. 현재는 기다리는 장세로 보는 것이 타당.")





if __name__ == "__main__":

    main()


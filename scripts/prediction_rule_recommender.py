
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import defaultdict



ROOT = Path(__file__).resolve().parents[1]

OUTCOME_LOG = ROOT / "logs/prediction_outcomes.jsonl"



MIN_SAMPLES = 3





def load_jsonl(path):

    if not path.exists():

        return []

    rows = []

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():

        try:

            rows.append(json.loads(line))

        except Exception:

            pass

    return rows





def fnum(x, default=0.0):

    try:

        return float(x)

    except Exception:

        return default





def score_band(score):

    score = fnum(score)

    if score >= 85:

        return "85+"

    if score >= 70:

        return "70-84"

    if score >= 60:

        return "60-69"

    if score >= 45:

        return "45-59"

    if score >= 30:

        return "30-44"

    return "0-29"





def rr_band(rr):

    rr = fnum(rr)

    if rr >= 2.0:

        return "RR 2.0+"

    if rr >= 1.2:

        return "RR 1.2-1.99"

    if rr > 0:

        return "RR 0-1.19"

    return "RR 없음"





def summarize(rows):

    total = len(rows)

    wins = sum(1 for r in rows if r.get("outcome") == "WIN")

    losses = sum(1 for r in rows if r.get("outcome") == "LOSS")

    flats = sum(1 for r in rows if r.get("outcome") == "FLAT")

    avg_ret = sum(fnum(r.get("direction_return_pct")) for r in rows) / total if total else 0

    win_rate = wins / total * 100 if total else 0

    return {

        "total": total,

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "win_rate": win_rate,

        "avg_ret": avg_ret,

    }





def bucket(rows, name, key_fn):

    out = []

    groups = defaultdict(list)



    for r in rows:

        groups[key_fn(r)].append(r)



    for key, items in groups.items():

        s = summarize(items)

        if s["total"] >= MIN_SAMPLES:

            out.append((name, key, s))



    return out





def grade_rule(s):

    if s["total"] < MIN_SAMPLES:

        return "표본부족"

    if s["win_rate"] >= 65 and s["avg_ret"] > 0 and s["losses"] <= s["wins"]:

        return "살릴 조건"

    if s["win_rate"] <= 45 or s["avg_ret"] < 0:

        return "주의/죽일 후보"

    return "관찰"





def main():

    rows = load_jsonl(OUTCOME_LOG)



    print("[BitSwipe Prediction Rule Recommender]")

    print()

    print(f"채점된 outcome: {len(rows)}개")



    if not rows:

        print("아직 추천할 데이터가 없음.")

        return



    candidates = []

    candidates += bucket(rows, "시간축", lambda r: f"{r.get('horizon_min')}m")

    candidates += bucket(rows, "Prediction class", lambda r: r.get("prediction_class", "UNKNOWN"))

    candidates += bucket(rows, "방향", lambda r: r.get("direction", "UNKNOWN"))

    candidates += bucket(rows, "RR", lambda r: rr_band(r.get("rr")))

    candidates += bucket(rows, "점수", lambda r: score_band(r.get("score")))

    candidates += bucket(rows, "심볼", lambda r: r.get("symbol", "UNKNOWN"))

    candidates += bucket(

        rows,

        "조합",

        lambda r: f"{r.get('horizon_min')}m / {r.get('direction')} / {rr_band(r.get('rr'))} / {score_band(r.get('score'))}"

    )



    candidates.sort(

        key=lambda x: (

            0 if grade_rule(x[2]) == "살릴 조건" else 1 if grade_rule(x[2]) == "관찰" else 2,

            -x[2]["win_rate"],

            -x[2]["avg_ret"],

            -x[2]["total"],

        )

    )



    keep = [x for x in candidates if grade_rule(x[2]) == "살릴 조건"]

    avoid = [x for x in candidates if grade_rule(x[2]) == "주의/죽일 후보"]



    print()

    print("추천: 살릴 조건")

    if not keep:

        print("- 아직 없음")

    else:

        for group, key, s in keep[:10]:

            print(

                f"- [{group}] {key}: 승률 {s['win_rate']:.1f}% "

                f"({s['wins']}/{s['total']}), 패배 {s['losses']}, "

                f"중립 {s['flats']}, 평균방향수익 {s['avg_ret']:.3f}%"

            )



    print()

    print("주의: 죽이거나 낮출 후보")

    if not avoid:

        print("- 아직 없음")

    else:

        for group, key, s in avoid[:10]:

            print(

                f"- [{group}] {key}: 승률 {s['win_rate']:.1f}% "

                f"({s['wins']}/{s['total']}), 패배 {s['losses']}, "

                f"중립 {s['flats']}, 평균방향수익 {s['avg_ret']:.3f}%"

            )



    print()

    print("현재 임시 해석:")

    print("- 표본이 작으므로 자동매매 금지")

    print("- 우선 RR 2.0+ / PREDICT_STRONG_RR 중심으로 관찰")

    print("- RR 1.2-1.99는 성과가 쌓일 때까지 파일럿 진입 기준에서 낮게 취급")





if __name__ == "__main__":

    main()


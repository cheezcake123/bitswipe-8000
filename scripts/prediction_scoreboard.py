
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import defaultdict, Counter



ROOT = Path(__file__).resolve().parents[1]

PREDICTION_LOG = ROOT / "logs/prediction_watch.jsonl"

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





def summarize(items):

    total = len(items)

    wins = sum(1 for x in items if x.get("outcome") == "WIN")

    losses = sum(1 for x in items if x.get("outcome") == "LOSS")

    flats = sum(1 for x in items if x.get("outcome") == "FLAT")

    avg_ret = sum(fnum(x.get("direction_return_pct")) for x in items) / total if total else 0



    return {

        "total": total,

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "win_rate": wins / total * 100 if total else 0,

        "avg_ret": avg_ret,

    }





def print_bucket(title, rows, key_fn):

    buckets = defaultdict(list)



    for r in rows:

        buckets[key_fn(r)].append(r)



    summaries = []

    for name, items in buckets.items():

        s = summarize(items)

        if s["total"] >= MIN_SAMPLES:

            summaries.append((name, s))



    summaries.sort(key=lambda x: (x[1]["win_rate"], x[1]["avg_ret"], x[1]["total"]), reverse=True)



    print()

    print(title)



    if not summaries:

        print("- 표본 부족")

        return



    for name, s in summaries[:20]:

        print(

            f"- {name}: "

            f"승률 {s['win_rate']:.1f}% "

            f"({s['wins']}/{s['total']}), "

            f"패배 {s['losses']}개, "

            f"중립 {s['flats']}개, "

            f"평균방향수익 {s['avg_ret']:.3f}%"

        )





def main():

    predictions = load_jsonl(PREDICTION_LOG)

    outcomes = load_jsonl(OUTCOME_LOG)



    print("[BitSwipe Prediction Scoreboard]")

    print()



    print(f"저장된 prediction 후보: {len(predictions)}개")

    print(f"채점 완료 outcome: {len(outcomes)}개")



    if not outcomes:

        print()

        print("아직 채점된 결과가 없음.")

        print("Prediction 후보가 저장되고 1시간/4시간/12시간이 지나야 성적표가 쌓임.")

        return



    print()

    print("전체 outcome 분포:")

    for k, v in Counter(o.get("outcome", "UNKNOWN") for o in outcomes).most_common():

        print(f"- {k}: {v}개")



    print_bucket("시간축별", outcomes, lambda x: f"{x.get('horizon_min')}m")

    print_bucket("Prediction class별", outcomes, lambda x: x.get("prediction_class", "UNKNOWN"))

    print_bucket("방향별", outcomes, lambda x: x.get("direction", "UNKNOWN"))

    print_bucket("RR 구간별", outcomes, lambda x: rr_band(x.get("rr")))

    print_bucket("점수 구간별", outcomes, lambda x: score_band(x.get("score")))

    print_bucket("심볼별", outcomes, lambda x: x.get("symbol", "UNKNOWN"))

    print_bucket(

        "조합별",

        outcomes,

        lambda x: f"{x.get('horizon_min')}m / {x.get('direction')} / {rr_band(x.get('rr'))} / {score_band(x.get('score'))}"

    )



    print()

    print("최근 outcome 20개:")

    for o in outcomes[-20:]:

        print(

            f"- {o.get('symbol')} {o.get('direction')} "

            f"{o.get('horizon_min')}m {o.get('outcome')} "

            f"ret={fnum(o.get('direction_return_pct')):.3f}% "

            f"score={fnum(o.get('score')):.0f} rr={fnum(o.get('rr')):.2f}"

        )





if __name__ == "__main__":

    main()


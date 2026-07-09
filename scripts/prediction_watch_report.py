
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import Counter

from datetime import datetime, timedelta



LOG_PATH = Path("logs/candidates.jsonl")



MIN_PREDICT_RR = 1.20

MIN_STRONG_RR = 2.00

MIN_PREDICT_SCORE = 20

LATEST_WINDOW_SECONDS = 90





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





def load_rows(limit=10000):

    if not LOG_PATH.exists():

        return []



    rows = []

    for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:

        try:

            row = json.loads(line)

            row["_ts"] = parse_ts(row.get("ts"))

            row["_symbol"] = val(row, "symbol", default="?")

            row["_market_type"] = val(row, "market_type", default="UNKNOWN")

            row["_direction"] = val(row, "direction", default="UNKNOWN")

            row["_score"] = fnum(val(row, "score", "rule_score", default=0))

            row["_rr"] = fnum(val(row, "estimated_rr", "rr", "risk_reward", default=0))

            row["_grade"] = val(row, "grade", default="?")

            row["_blocked"] = val(row, "blocked_reason", "block_reason", "reason", default="UNKNOWN")

            row["_reasons"] = val(row, "reasons", default=[]) or []

            rows.append(row)

        except Exception:

            pass



    return [r for r in rows if r.get("_ts")]





def latest_batch(rows):

    if not rows:

        return []



    rows = sorted(rows, key=lambda r: r["_ts"])

    latest = rows[-1]["_ts"]

    cutoff = latest - timedelta(seconds=LATEST_WINDOW_SECONDS)



    return [r for r in rows if r["_ts"] >= cutoff]





def prediction_class(row):

    if row["_market_type"] != "BINANCE":

        return None



    if row["_direction"] not in ("LONG", "SHORT"):

        return None



    if row["_score"] < MIN_PREDICT_SCORE:

        return None



    if row["_rr"] < MIN_PREDICT_RR:

        return None



    if row["_rr"] >= MIN_STRONG_RR:

        return "PREDICT_STRONG_RR"



    return "PREDICT_WATCH_RR"





def reason_text(row):

    reasons = row.get("_reasons") or []

    if isinstance(reasons, list):

        return "; ".join(str(x) for x in reasons[:5])

    return str(reasons)





def main():

    rows = load_rows()



    print("[BitSwipe Prediction Watch]")

    print()



    if not rows:

        print("candidates.jsonl 로그 없음")

        return



    batch = latest_batch(rows)



    if not batch:

        print("최신 스캔 묶음 없음")

        return



    latest_ts = max(r["_ts"] for r in batch)

    earliest_ts = min(r["_ts"] for r in batch)



    print(f"최신 묶음: {earliest_ts.isoformat()} ~ {latest_ts.isoformat()}")

    print(f"검사 rows: {len(batch)}개")

    print()



    watch = []

    for r in batch:

        cls = prediction_class(r)

        if cls:

            r["_prediction_class"] = cls

            watch.append(r)



    watch.sort(key=lambda r: (r["_rr"], r["_score"]), reverse=True)



    print("Prediction Watch 기준:")

    print(f"- 시장: BINANCE only")

    print(f"- 방향: LONG/SHORT")

    print(f"- 최소 점수: {MIN_PREDICT_SCORE}")

    print(f"- 최소 RR: {MIN_PREDICT_RR}")

    print(f"- STRONG RR: {MIN_STRONG_RR}+")

    print()



    if not watch:

        print("현재 Prediction Watch 후보: 0개")

        print()

        print("해석: 지금은 예측 실험 후보도 없음. 방향 또는 RR 조건 미달.")

    else:

        print(f"현재 Prediction Watch 후보: {len(watch)}개")

        print()



        by_class = Counter(r["_prediction_class"] for r in watch)

        print("분류:")

        for k, v in by_class.most_common():

            print(f"- {k}: {v}개")



        print()

        print("후보:")

        for r in watch[:20]:

            print(

                f"- {r['_symbol']} {r['_direction']} "

                f"{r['_prediction_class']}, "

                f"점수={r['_score']:.0f}, "

                f"RR={r['_rr']:.2f}, "

                f"등급={r['_grade']}, "

                f"기존차단={r['_blocked']}"

            )

            rt = reason_text(r)

            if rt:

                print(f"  이유: {rt}")



        print()

        print("주의: 이건 진입 신호가 아니라 예측 실험 후보임. 실제 진입은 Risk Gate를 따로 통과해야 함.")



    print()

    print("최근 전체 분포:")

    print(f"- BINANCE rows: {sum(1 for r in batch if r['_market_type'] == 'BINANCE')}개")

    print(f"- ETF rows: {sum(1 for r in batch if r['_market_type'] == 'ETF')}개")

    print(f"- 방향 있음: {sum(1 for r in batch if r['_direction'] in ('LONG', 'SHORT'))}개")

    print(f"- RR >= {MIN_PREDICT_RR}: {sum(1 for r in batch if r['_rr'] >= MIN_PREDICT_RR)}개")





if __name__ == "__main__":

    main()


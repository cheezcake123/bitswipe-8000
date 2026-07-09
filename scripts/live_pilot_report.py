
#!/usr/bin/env python3

import json

from pathlib import Path

from datetime import datetime, timedelta

from collections import Counter



LOG_PATH = Path("logs/candidates.jsonl")



LATEST_WINDOW_SECONDS = 120



# Prediction 후보: 많이 맞히는 봇을 만들기 위한 넓은 후보

MIN_PREDICT_SCORE = 20

MIN_PREDICT_RR = 1.20



# 실전 파일럿 후보: 초소액 수동 매매만 허용

MIN_PILOT_SCORE = 35

MAX_PILOT_SCORE = 59

MIN_PILOT_RR = 2.00



MAX_RISK_PCT_PER_TRADE = 0.25

MAX_LEVERAGE = 2





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

            r = json.loads(line)

            r["_ts"] = parse_ts(r.get("ts"))

            r["_symbol"] = val(r, "symbol", default="?")

            r["_market_type"] = val(r, "market_type", default="UNKNOWN")

            r["_direction"] = val(r, "direction", default="UNKNOWN")

            r["_score"] = fnum(val(r, "score", "rule_score", default=0))

            r["_rr"] = fnum(val(r, "estimated_rr", "rr", "risk_reward", default=0))

            r["_grade"] = val(r, "grade", default="?")

            r["_blocked"] = val(r, "blocked_reason", "block_reason", "reason", default="UNKNOWN")

            r["_last"] = fnum(val(r, "last", "price", "close", default=0))

            r["_support"] = fnum(val(r, "support", default=0))

            r["_resistance"] = fnum(val(r, "resistance", default=0))

            r["_reasons"] = val(r, "reasons", default=[]) or []

            rows.append(r)

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





def classify(row):

    if row["_market_type"] != "BINANCE":

        return "EXCLUDE_ETF_OR_UNKNOWN"



    if row["_direction"] not in ("LONG", "SHORT"):

        return "NO_TRADE_WAIT"



    if row["_rr"] < MIN_PREDICT_RR:

        return "NO_TRADE_RR_LOW"



    if row["_score"] < MIN_PREDICT_SCORE:

        return "NO_TRADE_SCORE_TOO_LOW"



    # 실전 파일럿은 과거 리포트에서 상대적으로 가능성이 있었던 구간만 허용:

    # 점수 35~59, RR 2.0 이상, BINANCE, 방향 있음

    if (

        MIN_PILOT_SCORE <= row["_score"] <= MAX_PILOT_SCORE

        and row["_rr"] >= MIN_PILOT_RR

    ):

        return "PILOT_ELIGIBLE"



    return "PREDICT_ONLY"





def calc_reference_stop(row):

    direction = row["_direction"]

    entry = row["_last"]

    support = row["_support"]

    resistance = row["_resistance"]



    if entry <= 0:

        return None



    if direction == "LONG":

        if support > 0:

            return support

        return None



    if direction == "SHORT":

        if resistance > 0:

            return resistance

        return None



    return None





def reason_text(row):

    reasons = row.get("_reasons") or []

    if isinstance(reasons, list):

        return "; ".join(str(x) for x in reasons[:5])

    return str(reasons)





def print_candidate(row):

    cls = classify(row)

    stop = calc_reference_stop(row)



    print(

        f"- {row['_symbol']} {row['_direction']} / {cls}\n"

        f"  점수={row['_score']:.0f}, RR={row['_rr']:.2f}, 등급={row['_grade']}, 기존차단={row['_blocked']}\n"

        f"  현재가={row['_last']}, 지지={row['_support']}, 저항={row['_resistance']}"

    )



    if stop:

        if row["_direction"] == "LONG":

            stop_desc = f"롱 기준 참고 손절: 지지 이탈 부근 {stop}"

        else:

            stop_desc = f"숏 기준 참고 손절: 저항 돌파 부근 {stop}"

        print(f"  {stop_desc}")



    rt = reason_text(row)

    if rt:

        print(f"  이유: {rt}")





def main():

    rows = load_rows()



    print("[BitSwipe 실전 파일럿 보고서]")

    print()



    if not rows:

        print("candidates.jsonl 로그 없음")

        return



    batch = latest_batch(rows)



    if not batch:

        print("최신 스캔 묶음 없음")

        return



    latest = max(r["_ts"] for r in batch)

    earliest = min(r["_ts"] for r in batch)



    print(f"최신 묶음: {earliest.isoformat()} ~ {latest.isoformat()}")

    print(f"검사 rows: {len(batch)}개")

    print()



    classes = Counter(classify(r) for r in batch)



    print("분류 요약:")

    for k, v in classes.most_common():

        print(f"- {k}: {v}개")



    print()

    print("파일럿 운용 규칙:")

    print(f"- 자동 진입 금지")

    print(f"- 1회 손실 허용: 계좌의 {MAX_RISK_PCT_PER_TRADE}% 이하")

    print(f"- 레버리지: {MAX_LEVERAGE}배 이하")

    print("- 하루 2연패 시 중단")

    print("- PILOT_ELIGIBLE 외에는 실전 진입 금지")

    print()



    pilot = [r for r in batch if classify(r) == "PILOT_ELIGIBLE"]

    predict = [r for r in batch if classify(r) == "PREDICT_ONLY"]



    pilot.sort(key=lambda r: (r["_rr"], r["_score"]), reverse=True)

    predict.sort(key=lambda r: (r["_rr"], r["_score"]), reverse=True)



    if pilot:

        print("초소액 실전 파일럿 가능 후보:")

        for r in pilot[:10]:

            print_candidate(r)

        print()

        print("판정: 실전 파일럿 후보 있음. 단, 자동매매 금지. 반드시 수동으로 차트 확인 후 극소액만.")

    else:

        print("초소액 실전 파일럿 가능 후보: 0개")

        print("판정: 지금은 실전 진입 금지.")

    

    print()



    if predict:

        print("예측 실험 후보, 실전 진입 금지:")

        for r in predict[:10]:

            print_candidate(r)

    else:

        print("예측 실험 후보: 0개")



    print()

    print("최종 결론:")

    if pilot:

        print("PILOT_ELIGIBLE 후보만 초소액 수동 매매 검토 가능.")

    else:

        print("현재는 실전 매매하지 않는 것이 원칙. 봇이 후보를 줄 때까지 대기.")





if __name__ == "__main__":

    main()


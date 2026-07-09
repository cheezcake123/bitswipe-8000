
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import defaultdict

from datetime import datetime, timedelta



LOG_PATH = Path("logs/candidates.jsonl")



HORIZONS_MIN = [60, 240, 720]

MIN_SAMPLES_TO_SHOW = 3



# 너무 작은 움직임은 수수료/슬리피지에 먹히므로 중립 처리

MIN_MOVE_PCT = 0.10



# 같은 심볼/방향/가격대가 너무 자주 반복되면 하나의 신호로 취급

DEDUP_WINDOW_MIN = 45

PRICE_ROUND_DIGITS = 4





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





def fnum(x, default=None):

    try:

        return float(x)

    except Exception:

        return default





def load_rows(limit=30000):

    if not LOG_PATH.exists():

        return []



    rows = []

    for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:

        try:

            row = json.loads(line)

            row["_ts"] = parse_ts(row.get("ts"))

            row["_symbol"] = val(row, "symbol", default="?")

            row["_direction"] = val(row, "direction", default="UNKNOWN")

            row["_price"] = fnum(val(row, "last", "price", "close", "entry", default=None))

            row["_score"] = fnum(val(row, "score", "rule_score", default=0), 0)

            row["_rr"] = fnum(val(row, "estimated_rr", "rr", "risk_reward", default=0), 0)

            row["_grade"] = val(row, "grade", default="UNKNOWN")

            row["_market_type"] = val(row, "market_type", default="UNKNOWN")

            row["_blocked"] = val(row, "blocked_reason", "block_reason", "reason", default="UNKNOWN")

            rows.append(row)

        except Exception:

            pass



    return [

        r for r in rows

        if r.get("_ts")

        and r.get("_symbol")

        and r.get("_price")

    ]





def score_band(score):

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

    if rr >= 2.0:

        return "RR 2.0+"

    if rr >= 1.2:

        return "RR 1.2-1.99"

    if rr > 0:

        return "RR 0-1.19"

    return "RR 없음"





def direction_return_pct(direction, entry, future):

    raw = (future / entry - 1.0) * 100.0

    if direction == "SHORT":

        raw = -raw

    return raw





def classify_outcome(ret):

    if ret >= MIN_MOVE_PCT:

        return "WIN"

    if ret <= -MIN_MOVE_PCT:

        return "LOSS"

    return "FLAT"





def find_future(rows_by_symbol, symbol, ts, horizon_min):

    target = ts + timedelta(minutes=horizon_min)

    future_rows = rows_by_symbol.get(symbol, [])



    best = None

    best_dt = None



    for r in future_rows:

        rts = r["_ts"]

        if rts <= ts:

            continue



        dt = abs((rts - target).total_seconds())

        if best is None or dt < best_dt:

            best = r

            best_dt = dt



    if best is None:

        return None



    max_gap = max(45 * 60, horizon_min * 60 * 0.5)

    if best_dt > max_gap:

        return None



    return best





def dedup_directional_rows(rows):

    directional = [r for r in rows if r["_direction"] in ("LONG", "SHORT")]

    directional.sort(key=lambda r: r["_ts"])



    kept = []

    last_seen = {}



    for r in directional:

        price_key = round(r["_price"], PRICE_ROUND_DIGITS)

        key = (r["_symbol"], r["_direction"], price_key)

        prev_ts = last_seen.get(key)



        if prev_ts and (r["_ts"] - prev_ts) < timedelta(minutes=DEDUP_WINDOW_MIN):

            continue



        kept.append(r)

        last_seen[key] = r["_ts"]



    return kept





def summarize(items):

    total = len(items)

    if total == 0:

        return None



    wins = sum(1 for x in items if x["outcome"] == "WIN")

    losses = sum(1 for x in items if x["outcome"] == "LOSS")

    flats = sum(1 for x in items if x["outcome"] == "FLAT")

    win_rate = wins / total * 100

    loss_rate = losses / total * 100

    avg_ret = sum(x["ret"] for x in items) / total

    avg_win = sum(x["ret"] for x in items if x["outcome"] == "WIN") / wins if wins else 0

    avg_loss = sum(x["ret"] for x in items if x["outcome"] == "LOSS") / losses if losses else 0



    return {

        "total": total,

        "wins": wins,

        "losses": losses,

        "flats": flats,

        "win_rate": win_rate,

        "loss_rate": loss_rate,

        "avg_ret": avg_ret,

        "avg_win": avg_win,

        "avg_loss": avg_loss,

    }





def print_summary_line(name, items):

    s = summarize(items)

    if not s or s["total"] < MIN_SAMPLES_TO_SHOW:

        return



    print(

        f"- {name}: "

        f"승률 {s['win_rate']:.1f}% "

        f"({s['wins']}/{s['total']}), "

        f"패배 {s['losses']}개, "

        f"중립 {s['flats']}개, "

        f"평균방향수익 {s['avg_ret']:.3f}%, "

        f"평균승리 {s['avg_win']:.3f}%, "

        f"평균패배 {s['avg_loss']:.3f}%"

    )





def print_bucket(title, outcomes, key_fn):

    buckets = defaultdict(list)

    for x in outcomes:

        buckets[key_fn(x)].append(x)



    summaries = []

    for name, items in buckets.items():

        s = summarize(items)

        if s and s["total"] >= MIN_SAMPLES_TO_SHOW:

            summaries.append((name, s, items))



    summaries.sort(key=lambda x: (x[1]["win_rate"], x[1]["avg_ret"], x[1]["total"]), reverse=True)



    print()

    print(title)



    if not summaries:

        print("- 표본 부족")

        return



    for name, s, _ in summaries[:20]:

        print(

            f"- {name}: "

            f"승률 {s['win_rate']:.1f}% "

            f"({s['wins']}/{s['total']}), "

            f"평균방향수익 {s['avg_ret']:.3f}%"

        )





def run_for_market(rows, market_filter_name, market_filter_fn):

    market_rows = [r for r in rows if market_filter_fn(r)]

    market_rows.sort(key=lambda r: r["_ts"])



    rows_by_symbol = defaultdict(list)

    for r in market_rows:

        rows_by_symbol[r["_symbol"]].append(r)



    deduped = dedup_directional_rows(market_rows)



    print()

    print("#" * 70)

    print(f"[{market_filter_name}]")

    print("#" * 70)

    print(f"전체 rows: {len(market_rows)}개")

    print(f"중복 제거 전 방향 예측: {sum(1 for r in market_rows if r['_direction'] in ('LONG', 'SHORT'))}개")

    print(f"중복 제거 후 방향 예측: {len(deduped)}개")



    if not deduped:

        print("- 방향 예측 없음")

        return



    for horizon in HORIZONS_MIN:

        outcomes = []



        for r in deduped:

            future = find_future(rows_by_symbol, r["_symbol"], r["_ts"], horizon)

            if not future:

                continue



            ret = direction_return_pct(r["_direction"], r["_price"], future["_price"])

            outcomes.append({

                "symbol": r["_symbol"],

                "direction": r["_direction"],

                "score": r["_score"],

                "rr": r["_rr"],

                "grade": r["_grade"],

                "blocked": r["_blocked"],

                "market_type": r["_market_type"],

                "ret": ret,

                "outcome": classify_outcome(ret),

                "entry_ts": r["_ts"],

                "entry_price": r["_price"],

                "future_price": future["_price"],

            })



        print()

        print("=" * 60)

        print(f"{horizon}분 뒤 결과, 최소 유효 움직임 {MIN_MOVE_PCT:.2f}%")

        print("=" * 60)



        if not outcomes:

            print("- 표본 없음")

            continue



        print_summary_line("전체", outcomes)



        print_bucket("방향별", outcomes, lambda x: x["direction"])

        print_bucket("점수 구간별", outcomes, lambda x: score_band(x["score"]))

        print_bucket("RR 구간별", outcomes, lambda x: rr_band(x["rr"]))

        print_bucket("등급별", outcomes, lambda x: x["grade"])

        print_bucket("차단 사유별", outcomes, lambda x: x["blocked"])

        print_bucket("심볼별", outcomes, lambda x: x["symbol"])

        print_bucket(

            "조합별",

            outcomes,

            lambda x: f"{x['direction']} / {score_band(x['score'])} / {rr_band(x['rr'])}"

        )



        winners = sorted(outcomes, key=lambda x: x["ret"], reverse=True)[:5]

        losers = sorted(outcomes, key=lambda x: x["ret"])[:5]



        print()

        print("잘 맞은 예시 TOP:")

        for x in winners:

            print(

                f"- {x['symbol']} {x['direction']} "

                f"점수={x['score']:.0f}, RR={x['rr']:.2f}, "

                f"결과={x['outcome']}, 방향수익={x['ret']:.3f}%, "

                f"{x['entry_price']} -> {x['future_price']}"

            )



        print()

        print("틀린 예시 TOP:")

        for x in losers:

            print(

                f"- {x['symbol']} {x['direction']} "

                f"점수={x['score']:.0f}, RR={x['rr']:.2f}, "

                f"결과={x['outcome']}, 방향수익={x['ret']:.3f}%, "

                f"{x['entry_price']} -> {x['future_price']}"

            )





def main():

    rows = load_rows()



    print("[BitSwipe 예측 적중률 리포트 v2]")

    print()



    if not rows:

        print("분석 가능한 rows 없음")

        return



    print(f"분석 rows: {len(rows)}개")

    print(f"분석 범위: {rows[0]['_ts'].isoformat()} ~ {rows[-1]['_ts'].isoformat()}")

    print(f"심볼 수: {len(set(r['_symbol'] for r in rows))}개")

    print(f"최소 유효 움직임: {MIN_MOVE_PCT:.2f}%")

    print(f"중복 제거 윈도우: {DEDUP_WINDOW_MIN}분")

    print()



    market_counts = defaultdict(int)

    for r in rows:

        market_counts[r["_market_type"]] += 1



    print("시장 타입 분포:")

    for k, v in sorted(market_counts.items()):

        print(f"- {k}: {v}개")



    run_for_market(rows, "전체", lambda r: True)

    run_for_market(rows, "BINANCE only", lambda r: r["_market_type"] == "BINANCE")

    run_for_market(rows, "ETF only", lambda r: r["_market_type"] == "ETF")



    print()

    print("해석:")

    print("- 이 리포트는 '많이 맞히는 예측 엔진'을 만들기 위한 검증용이다.")

    print("- 승률이 높은 구간이 반복적으로 나오면 그 조건을 prediction 후보로 승격한다.")

    print("- 실전 진입은 별도 Risk Gate에서 손익비/손절/거래량으로 다시 걸러야 한다.")





if __name__ == "__main__":

    main()


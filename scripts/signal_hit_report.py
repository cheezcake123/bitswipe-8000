
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import defaultdict, Counter

from datetime import datetime, timedelta



LOG_PATH = Path("logs/candidates.jsonl")



HORIZONS_MIN = [60, 240, 720]

MIN_SAMPLES_TO_SHOW = 3





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





def load_rows(limit=20000):

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

            row["_blocked"] = val(row, "blocked_reason", "block_reason", "reason", default="UNKNOWN")

            rows.append(row)

        except Exception:

            pass



    return [r for r in rows if r.get("_ts") and r.get("_symbol") and r.get("_price")]





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





def direction_hit(direction, entry, future):

    if direction == "LONG":

        return future > entry

    if direction == "SHORT":

        return future < entry

    return None





def direction_return_pct(direction, entry, future):

    if not entry:

        return 0.0

    raw = (future / entry - 1.0) * 100.0

    if direction == "SHORT":

        raw = -raw

    return raw





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



    # 너무 멀리 떨어진 값이면 제외. 15분 스캔 기준이라 horizon의 절반 또는 45분까지 허용.

    max_gap = max(45 * 60, horizon_min * 60 * 0.5)

    if best_dt > max_gap:

        return None



    return best





def summarize_bucket(name, items):

    if not items:

        return None



    wins = sum(1 for x in items if x["hit"])

    total = len(items)

    win_rate = wins / total * 100

    avg_ret = sum(x["ret"] for x in items) / total



    return {

        "name": name,

        "total": total,

        "wins": wins,

        "win_rate": win_rate,

        "avg_ret": avg_ret,

    }





def print_bucket_table(title, bucket_items):

    summaries = []

    for name, items in bucket_items.items():

        s = summarize_bucket(name, items)

        if s and s["total"] >= MIN_SAMPLES_TO_SHOW:

            summaries.append(s)



    summaries.sort(key=lambda x: (x["win_rate"], x["avg_ret"], x["total"]), reverse=True)



    print()

    print(title)



    if not summaries:

        print("- 표본 부족")

        return



    for s in summaries[:20]:

        print(

            f"- {s['name']}: "

            f"승률 {s['win_rate']:.1f}% "

            f"({s['wins']}/{s['total']}), "

            f"평균 방향수익 {s['avg_ret']:.3f}%"

        )





def main():

    rows = load_rows()



    print("[BitSwipe 예측 적중률 리포트]")

    print()



    if not rows:

        print("분석 가능한 rows가 없음. candidates.jsonl에 ts/symbol/last 또는 price 필드가 있는지 확인 필요.")

        print()

        print("최근 JSON 키 확인용:")

        if LOG_PATH.exists():

            for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-3:]:

                try:

                    print(sorted(json.loads(line).keys()))

                except Exception:

                    pass

        return



    rows.sort(key=lambda r: r["_ts"])

    rows_by_symbol = defaultdict(list)



    for r in rows:

        rows_by_symbol[r["_symbol"]].append(r)



    print(f"분석 rows: {len(rows)}개")

    print(f"분석 범위: {rows[0]['_ts'].isoformat()} ~ {rows[-1]['_ts'].isoformat()}")

    print(f"심볼 수: {len(rows_by_symbol)}개")

    print()



    directional_rows = [r for r in rows if r["_direction"] in ("LONG", "SHORT")]

    wait_rows = [r for r in rows if r["_direction"] == "WAIT"]



    print("기본 분포:")

    print(f"- LONG/SHORT 예측 rows: {len(directional_rows)}개")

    print(f"- WAIT rows: {len(wait_rows)}개")

    print()



    for horizon in HORIZONS_MIN:

        outcomes = []



        for r in directional_rows:

            future = find_future(rows_by_symbol, r["_symbol"], r["_ts"], horizon)

            if not future:

                continue



            hit = direction_hit(r["_direction"], r["_price"], future["_price"])

            if hit is None:

                continue



            ret = direction_return_pct(r["_direction"], r["_price"], future["_price"])



            outcomes.append({

                "symbol": r["_symbol"],

                "direction": r["_direction"],

                "score": r["_score"],

                "rr": r["_rr"],

                "grade": r["_grade"],

                "blocked": r["_blocked"],

                "hit": hit,

                "ret": ret,

                "entry_ts": r["_ts"],

                "entry_price": r["_price"],

                "future_price": future["_price"],

            })



        print("=" * 60)

        print(f"{horizon}분 뒤 방향 적중률")

        print("=" * 60)



        if not outcomes:

            print("- 표본 없음")

            continue



        wins = sum(1 for x in outcomes if x["hit"])

        total = len(outcomes)

        win_rate = wins / total * 100

        avg_ret = sum(x["ret"] for x in outcomes) / total



        print(f"전체 방향 예측 승률: {win_rate:.1f}% ({wins}/{total})")

        print(f"평균 방향수익: {avg_ret:.3f}%")



        by_symbol = defaultdict(list)

        by_direction = defaultdict(list)

        by_score = defaultdict(list)

        by_rr = defaultdict(list)

        by_grade = defaultdict(list)

        by_blocked = defaultdict(list)

        by_combo = defaultdict(list)



        for x in outcomes:

            by_symbol[x["symbol"]].append(x)

            by_direction[x["direction"]].append(x)

            by_score[score_band(x["score"])].append(x)

            by_rr[rr_band(x["rr"])].append(x)

            by_grade[x["grade"]].append(x)

            by_blocked[x["blocked"]].append(x)

            by_combo[f"{x['direction']} / {score_band(x['score'])} / {rr_band(x['rr'])}"].append(x)



        print_bucket_table("방향별", by_direction)

        print_bucket_table("점수 구간별", by_score)

        print_bucket_table("RR 구간별", by_rr)

        print_bucket_table("등급별", by_grade)

        print_bucket_table("차단 사유별", by_blocked)

        print_bucket_table("심볼별 TOP", by_symbol)

        print_bucket_table("조합별 TOP", by_combo)



        best_examples = sorted(outcomes, key=lambda x: x["ret"], reverse=True)[:8]

        worst_examples = sorted(outcomes, key=lambda x: x["ret"])[:8]



        print()

        print("잘 맞은 예시 TOP:")

        for x in best_examples:

            print(

                f"- {x['symbol']} {x['direction']} "

                f"점수={x['score']:.0f}, RR={x['rr']:.2f}, "

                f"수익방향={x['ret']:.3f}%, "

                f"가격 {x['entry_price']} -> {x['future_price']}"

            )



        print()

        print("틀린 예시 TOP:")

        for x in worst_examples:

            print(

                f"- {x['symbol']} {x['direction']} "

                f"점수={x['score']:.0f}, RR={x['rr']:.2f}, "

                f"수익방향={x['ret']:.3f}%, "

                f"가격 {x['entry_price']} -> {x['future_price']}"

            )



        print()



    print("해석 가이드:")

    print("- 승률이 높아도 평균 방향수익이 낮거나 음수면 실전 진입 기준으로는 위험.")

    print("- 많이 맞히는 봇을 만들려면 먼저 승률 높은 구간을 찾고, 그다음 손익비/손절 기준을 따로 설계해야 함.")

    print("- 지금 단계에서는 진입 알림을 늘리기보다 prediction 후보를 많이 기록하고 검증하는 쪽이 맞음.")





if __name__ == "__main__":

    main()


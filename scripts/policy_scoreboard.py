
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import Counter, defaultdict



ROOT = Path(__file__).resolve().parents[1]

OUTCOME_LOG = ROOT / "logs/prediction_outcomes.jsonl"



STRICT_MIN_SCORE = 30

STRICT_MAX_SCORE = 59

STRICT_MIN_RR = 2.0





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





def is_strict_policy(row):

    return (

        row.get("market_type") == "BINANCE"

        and row.get("direction") == "SHORT"

        and STRICT_MIN_SCORE <= fnum(row.get("score")) <= STRICT_MAX_SCORE

        and fnum(row.get("rr")) >= STRICT_MIN_RR

    )





def summarize(rows):

    total = len(rows)

    wins = sum(1 for r in rows if r.get("outcome") == "WIN")

    losses = sum(1 for r in rows if r.get("outcome") == "LOSS")

    flats = sum(1 for r in rows if r.get("outcome") == "FLAT")

    avg_ret = sum(fnum(r.get("direction_return_pct")) for r in rows) / total if total else 0

    win_rate = wins / total * 100 if total else 0

    return total, wins, losses, flats, win_rate, avg_ret





def print_summary(title, rows):

    total, wins, losses, flats, win_rate, avg_ret = summarize(rows)

    print(title)

    print(f"- 표본: {total}개")

    print(f"- 승률: {win_rate:.1f}% ({wins}/{total})" if total else "- 승률: 표본 없음")

    print(f"- 패배: {losses}개")

    print(f"- 중립: {flats}개")

    print(f"- 평균방향수익: {avg_ret:.3f}%")

    print("")





def main():

    rows = load_jsonl(OUTCOME_LOG)

    strict = [r for r in rows if is_strict_policy(r)]



    print("[BitSwipe Strict Policy Scoreboard]")

    print("")

    print("정책:")

    print("- BINANCE only")

    print("- SHORT only")

    print("- RR 2.0+")

    print("- score 30~59")

    print("")



    print_summary("전체 outcome", rows)

    print_summary("엄격 정책 outcome", strict)



    if not strict:

        print("아직 엄격 정책에 해당하는 채점 결과가 없음.")

        return



    by_horizon = defaultdict(list)

    for r in strict:

        by_horizon[r.get("horizon_min")].append(r)



    print("시간축별 엄격 정책:")

    for h in sorted(by_horizon):

        total, wins, losses, flats, win_rate, avg_ret = summarize(by_horizon[h])

        print(

            f"- {h}m: 승률 {win_rate:.1f}% ({wins}/{total}), "

            f"패배 {losses}, 중립 {flats}, 평균방향수익 {avg_ret:.3f}%"

        )



    print("")

    print("심볼별 엄격 정책:")

    by_symbol = defaultdict(list)

    for r in strict:

        by_symbol[r.get("symbol", "UNKNOWN")].append(r)



    for symbol, items in sorted(by_symbol.items(), key=lambda x: len(x[1]), reverse=True):

        total, wins, losses, flats, win_rate, avg_ret = summarize(items)

        print(

            f"- {symbol}: 승률 {win_rate:.1f}% ({wins}/{total}), "

            f"패배 {losses}, 중립 {flats}, 평균방향수익 {avg_ret:.3f}%"

        )



    print("")

    print("최근 엄격 정책 outcome:")

    for r in strict[-20:]:

        print(

            f"- {r.get('symbol')} {r.get('direction')} {r.get('horizon_min')}m "

            f"{r.get('outcome')} ret={fnum(r.get('direction_return_pct')):.3f}% "

            f"score={fnum(r.get('score')):.0f} rr={fnum(r.get('rr')):.2f}"

        )



    print("")

    if len(strict) < 20:

        print("판정: 표본 부족. 실전 자동매매 금지. 초소액 수동 검토만 유지.")

    else:

        total, wins, losses, flats, win_rate, avg_ret = summarize(strict)

        if win_rate >= 60 and avg_ret > 0:

            print("판정: 관찰상 유효. 그래도 자동매매 전 최소 50~100개 표본 필요.")

        else:

            print("판정: 아직 실전 기준으로 부족. 정책 재검토 필요.")





if __name__ == "__main__":

    main()


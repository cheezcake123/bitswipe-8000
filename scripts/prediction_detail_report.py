
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import Counter, defaultdict

from datetime import datetime, timezone



ROOT = Path(__file__).resolve().parents[1]

PRED_LOG = ROOT / "logs/prediction_watch.jsonl"

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





def is_strict(row):

    return (

        row.get("market_type") == "BINANCE"

        and row.get("direction") == "SHORT"

        and STRICT_MIN_SCORE <= fnum(row.get("score")) <= STRICT_MAX_SCORE

        and fnum(row.get("rr")) >= STRICT_MIN_RR

    )





def summarize_outcomes(rows):

    total = len(rows)

    if not total:

        return "표본 없음"

    wins = sum(1 for r in rows if r.get("outcome") == "WIN")

    losses = sum(1 for r in rows if r.get("outcome") == "LOSS")

    flats = sum(1 for r in rows if r.get("outcome") == "FLAT")

    avg = sum(fnum(r.get("direction_return_pct")) for r in rows) / total

    return f"승률 {wins / total * 100:.1f}% ({wins}/{total}), 패배 {losses}, 중립 {flats}, 평균 {avg:.3f}%"





def main():

    preds = load_jsonl(PRED_LOG)

    outcomes = load_jsonl(OUTCOME_LOG)



    strict_preds = [r for r in preds if is_strict(r)]

    pending = [r for r in preds if r.get("status") == "PENDING"]



    print("[BitSwipe Prediction Detail Report]")

    print("")

    print(f"저장된 prediction: {len(preds)}개")

    print(f"pending prediction: {len(pending)}개")

    print(f"strict 조건 prediction: {len(strict_preds)}개")

    print(f"채점 outcome: {len(outcomes)}개")

    print("")



    print("최근 prediction 15개:")

    for r in preds[-15:]:

        created = r.get("created_at") or r.get("ts") or "?"

        price = r.get("entry_price") or r.get("price") or "?"

        strict_mark = "STRICT" if is_strict(r) else "WATCH"

        print(

            f"- {created} | {r.get('symbol')} {r.get('direction')} | "

            f"{r.get('prediction_class')} | {strict_mark} | "

            f"score={fnum(r.get('score')):.0f} rr={fnum(r.get('rr')):.2f} "

            f"entry={price} reason={r.get('blocked_reason')}"

        )

        rt = r.get("reason_text")

        if rt:

            print(f"  근거: {rt}")



    print("")

    print("strict 조건 prediction:")

    if not strict_preds:

        print("- 없음")

    for r in strict_preds[-20:]:

        created = r.get("created_at") or r.get("ts") or "?"

        price = r.get("entry_price") or r.get("price") or "?"

        print(

            f"- {created} | {r.get('symbol')} {r.get('direction')} "

            f"score={fnum(r.get('score')):.0f} rr={fnum(r.get('rr')):.2f} entry={price}"

        )



    print("")

    print("strict outcome 심볼별 성과:")

    strict_outcomes = [r for r in outcomes if is_strict(r)]

    by_symbol = defaultdict(list)

    for r in strict_outcomes:

        by_symbol[r.get("symbol", "UNKNOWN")].append(r)



    if not by_symbol:

        print("- 표본 없음")

    for symbol, items in sorted(by_symbol.items(), key=lambda x: len(x[1]), reverse=True):

        warning = ""

        losses = sum(1 for r in items if r.get("outcome") == "LOSS")

        wins = sum(1 for r in items if r.get("outcome") == "WIN")

        if losses >= 2 or (len(items) >= 3 and wins / len(items) < 0.5):

            warning = " ⚠️ 주의"

        print(f"- {symbol}: {summarize_outcomes(items)}{warning}")



    print("")

    print("운영 판정:")

    print("- PILOT_ELIGIBLE_STRICT가 떠도 자동진입 금지")

    print("- 60m strict 성과가 약하므로 즉시 추격 진입 금지")

    print("- LINKUSDT/UNIUSDT처럼 손실이 누적된 심볼은 더 보수적으로 검토")





if __name__ == "__main__":

    main()


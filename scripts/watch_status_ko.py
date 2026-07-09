
#!/usr/bin/env python3

import json

from pathlib import Path

from collections import Counter

from datetime import datetime, timedelta



LOG_PATH = Path("logs/candidates.jsonl")

BATCH_GAP_SECONDS = 60



def parse_ts(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except Exception:

        return None



def load_rows(limit=10000):

    if not LOG_PATH.exists():

        return []

    lines = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

    rows = []

    for line in lines[-limit:]:

        try:

            row = json.loads(line)

            row["_parsed_ts"] = parse_ts(row.get("ts"))

            rows.append(row)

        except Exception:

            pass

    return rows



def val(row, *names, default=None):

    for name in names:

        if name in row and row.get(name) is not None:

            return row.get(name)

    return default



def latest_batch(rows):

    timed = [r for r in rows if r.get("_parsed_ts")]

    if not timed:

        return []



    timed.sort(key=lambda r: r["_parsed_ts"])

    latest = timed[-1]["_parsed_ts"]

    cutoff = latest - timedelta(seconds=BATCH_GAP_SECONDS)



    return [r for r in timed if r["_parsed_ts"] >= cutoff]



def main():

    rows = load_rows()



    print("[BitSwipe 감시 상태 요약]")

    print()



    if not rows:

        print("상태: candidates.jsonl 로그가 없거나 읽을 수 없음")

        print("결론: 스캔 결과 저장부터 확인 필요")

        return



    recent_rows = latest_batch(rows)



    if not recent_rows:

        print("상태: 최신 스캔 묶음을 찾지 못함")

        print("결론: candidates.jsonl의 ts 필드 확인 필요")

        return



    latest_time = max(r["_parsed_ts"] for r in recent_rows)

    earliest_time = min(r["_parsed_ts"] for r in recent_rows)



    print(f"최신 스캔 기준 시각: {latest_time.isoformat()}")

    print(f"스캔 묶음 범위: {earliest_time.isoformat()} ~ {latest_time.isoformat()}")

    print(f"최신 스캔 후보 로그 수: {len(recent_rows)}개")

    print()



    decisions = Counter(val(r, "decision", default="UNKNOWN") for r in recent_rows)

    grades = Counter(val(r, "grade", default="UNKNOWN") for r in recent_rows)

    blocked = Counter(val(r, "blocked_reason", "block_reason", "reason", default="NONE") for r in recent_rows)



    ai_eligible = sum(1 for r in recent_rows if bool(val(r, "eligible_for_ai", default=False)))

    ai_called = sum(1 for r in recent_rows if bool(val(r, "ai_called", default=False)))

    alerts = sum(1 for r in recent_rows if bool(val(r, "alert_sent", default=False)))



    print("판정 요약:")

    for k, v in decisions.most_common():

        print(f"- {k}: {v}개")



    print()

    print("등급 요약:")

    for k, v in grades.most_common():

        print(f"- {k}: {v}개")



    print()

    print("AI/알림 요약:")

    print(f"- AI 후보 통과: {ai_eligible}개")

    print(f"- AI 호출: {ai_called}회")

    print(f"- 텔레그램 알림: {alerts}건")



    print()

    print("차단 사유 TOP:")

    for k, v in blocked.most_common(10):

        print(f"- {k}: {v}개")



    print()

    print("상위 후보/최근 심볼 상태:")



    def sort_key(r):

        score = float(val(r, "rule_score", "score", default=0) or 0)

        rr = float(val(r, "rr", "estimated_rr", default=0) or 0)

        grade = str(val(r, "grade", default="Z"))

        return (grade, -score, -rr)



    for r in sorted(recent_rows, key=sort_key)[:30]:

        symbol = val(r, "symbol", default="?")

        decision = val(r, "decision", default="?")

        grade = val(r, "grade", default="?")

        score = val(r, "rule_score", "score", default="?")

        rr = val(r, "rr", "estimated_rr", "risk_reward", default="?")

        blocked_reason = val(r, "blocked_reason", "block_reason", "reason", default="NONE")

        reasons = val(r, "reasons", default=[]) or []



        if isinstance(reasons, list):

            reason_text = "; ".join(str(x) for x in reasons[:4])

        else:

            reason_text = str(reasons)



        print(f"- {symbol}: {decision}/{grade}, 점수 {score}, RR {rr}, 차단={blocked_reason}")

        if reason_text:

            print(f"  이유: {reason_text}")



    print()

    if alerts > 0:

        print("결론: 알림 가능한 후보가 있었음. 텔레그램 수신 여부를 확인해야 함.")

    elif ai_eligible > 0 and ai_called == 0:

        print("결론: 후보는 있었지만 AI 호출 단계에서 막힘. 예산/쿨다운/호출 오류 확인 필요.")

    elif ai_eligible == 0:

        print("결론: 현재는 진입 허가 없음. 룰 단계에서 전부 차단됨.")

    else:

        print("결론: 추가 확인 필요.")



if __name__ == "__main__":

    main()


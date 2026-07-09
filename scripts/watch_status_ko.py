
#!/usr/bin/env python3

import json

import re

from pathlib import Path

from collections import Counter

from datetime import datetime, timedelta



CANDIDATE_LOG_PATH = Path("logs/candidates.jsonl")

SCAN_LOG_PATH = Path("logs/watchlist_scan.log")

STATUS_STATE_PATH = Path("logs/watch_no_candidate_status_state.json")

BATCH_GAP_SECONDS = 60





def parse_ts(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except Exception:

        return None





def load_candidate_rows(limit=10000):

    if not CANDIDATE_LOG_PATH.exists():

        return []

    lines = CANDIDATE_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

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





def latest_scan_log_block(max_lines=1200):

    if not SCAN_LOG_PATH.exists():

        return []



    lines = SCAN_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

    tail = lines[-max_lines:]



    start_idx = None

    for i in range(len(tail) - 1, -1, -1):

        if "SCAN_START" in tail[i]:

            start_idx = i

            break



    if start_idx is None:

        return tail[-80:]



    return tail[start_idx:]





def parse_status_state():

    if not STATUS_STATE_PATH.exists():

        return None



    try:

        return json.loads(STATUS_STATE_PATH.read_text(encoding="utf-8"))

    except Exception:

        return None







def blocked_reason_ko(reason):
    mapping = {
        "NO_CLEAR_DIRECTION": "방향 불명확",
        "LOW_ESTIMATED_RR": "손익비 부족",
        "HIGH_SCORE_LOW_RR_REVIEW_ONLY": "점수는 높지만 손익비 부족",
        "RR_UNAVAILABLE": "손익비 계산 불가",
        "LOW_RULE_SCORE": "룰 점수 부족",
        "NOT_ELIGIBLE_FOR_AI": "AI 검토 조건 미충족",
        "NONE": "없음",
        None: "없음",
    }
    return mapping.get(reason, str(reason))

def summarize_telegram_from_scan_log():

    block = latest_scan_log_block()



    joined = "\n".join(block)



    has_status_attempt = "NO_CANDIDATE_STATUS_ATTEMPT" in joined

    has_status_skip = "NO_CANDIDATE_STATUS_SKIP" in joined

    has_telegram_result = "TELEGRAM_RESULT" in joined

    has_telegram_skip = "TELEGRAM_SKIP" in joined

    has_telegram_error = "TELEGRAM_ERROR" in joined



    status_attempt_line = ""

    status_skip_line = ""

    telegram_result_line = ""

    telegram_error_line = ""



    for line in block:

        if "NO_CANDIDATE_STATUS_ATTEMPT" in line:

            status_attempt_line = line

        elif "NO_CANDIDATE_STATUS_SKIP" in line:

            status_skip_line = line

        elif "TELEGRAM_RESULT" in line:

            telegram_result_line = line

        elif "TELEGRAM_ERROR" in line or "TELEGRAM_SKIP" in line:

            telegram_error_line = line



    telegram_ok = None

    if telegram_result_line:

        m = re.search(r'"ok":\s*(true|false)', telegram_result_line)

        if m:

            telegram_ok = m.group(1) == "true"



    cooldown_remaining = None

    if status_skip_line:

        m = re.search(r"cooldown_remaining=(\d+)", status_skip_line)

        if m:

            cooldown_remaining = int(m.group(1))



    return {

        "has_status_attempt": has_status_attempt,

        "has_status_skip": has_status_skip,

        "has_telegram_result": has_telegram_result,

        "has_telegram_skip": has_telegram_skip,

        "has_telegram_error": has_telegram_error,

        "telegram_ok": telegram_ok,

        "status_attempt_line": status_attempt_line,

        "status_skip_line": status_skip_line,

        "telegram_result_line": telegram_result_line,

        "telegram_error_line": telegram_error_line,

        "cooldown_remaining": cooldown_remaining,

        "block_line_count": len(block),

    }





def print_telegram_summary(latest_rows):

    scan = summarize_telegram_from_scan_log()

    state = parse_status_state()



    candidate_alert_rows = [

        r for r in latest_rows

        if bool(val(r, "alert_sent", default=False)) and bool(val(r, "ai_called", default=False))

    ]



    print("텔레그램 요약:")

    print(f"- 후보 알림: {len(candidate_alert_rows)}건")



    if scan["has_status_attempt"]:

        if scan["telegram_ok"] is True:

            print("- 상태 보고: 전송 성공")

        elif scan["telegram_ok"] is False:

            print("- 상태 보고: 전송 시도했지만 실패")

        elif scan["has_telegram_result"]:

            print("- 상태 보고: 전송 결과 확인 필요")

        elif scan["has_telegram_skip"]:

            print("- 상태 보고: 텔레그램 설정 누락으로 미전송")

        elif scan["has_telegram_error"]:

            print("- 상태 보고: 텔레그램 오류")

        else:

            print("- 상태 보고: 전송 시도 기록 있음")

    elif scan["has_status_skip"]:

        remaining = scan.get("cooldown_remaining")

        if remaining is not None:

            minutes = remaining // 60

            print(f"- 상태 보고: 쿨다운으로 스킵, 남은 시간 약 {minutes}분")

        else:

            print("- 상태 보고: 쿨다운으로 스킵")

    else:

        print("- 상태 보고: 이번 실행에서는 전송/스킵 기록 없음")



    if state:

        sent = state.get("sent")

        last_sent_ts = state.get("last_sent_ts")

        print(f"- 상태 보고 쿨다운 파일: 있음, 마지막 전송 성공={sent}")

        if last_sent_ts:

            print(f"- 마지막 상태 보고 timestamp: {last_sent_ts}")

    else:

        print("- 상태 보고 쿨다운 파일: 없음")





def main():

    rows = load_candidate_rows()



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



    print("판정 요약:")

    for k, v in decisions.most_common():

        print(f"- {k}: {v}개")



    print()

    print("등급 요약:")

    for k, v in grades.most_common():

        print(f"- {k}: {v}개")



    print()

    print("AI 요약:")

    print(f"- AI 후보 통과: {ai_eligible}개")

    print(f"- AI 호출: {ai_called}회")



    print()

    print_telegram_summary(recent_rows)



    print()

    print("차단 사유 TOP:")

    for k, v in blocked.most_common(10):

        print(f"- {k} ({blocked_reason_ko(k)}): {v}개")



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



        print(f"- {symbol}: {decision}/{grade}, 점수 {score}, RR {rr}, 차단={blocked_reason}({blocked_reason_ko(blocked_reason)})")

        if reason_text:

            print(f"  이유: {reason_text}")



    print()

    if ai_called > 0:

        print("결론: AI 검토 후보가 있었음. 결과 알림/분석 로그를 확인해야 함.")

    elif ai_eligible > 0 and ai_called == 0:

        print("결론: 후보는 있었지만 AI 호출 단계에서 막힘. 예산/쿨다운/호출 오류 확인 필요.")

    elif ai_eligible == 0:

        print("결론: 현재는 진입 허가 없음. 룰 단계에서 전부 차단됨.")

    else:

        print("결론: 추가 확인 필요.")





if __name__ == "__main__":

    main()



#!/usr/bin/env python3

import json

import os

import time

from pathlib import Path

from datetime import datetime, timedelta, timezone

from collections import Counter



import requests



ROOT = Path(__file__).resolve().parents[1]

LOG_PATH = ROOT / "logs/candidates.jsonl"

STATE_PATH = ROOT / "logs/live_pilot_notify_state.json"

WATCH_LOG_PATH = ROOT / "logs/watchlist_scan.log"



LATEST_WINDOW_SECONDS = 120



MIN_PREDICT_SCORE = 20

MIN_PREDICT_RR = 1.20



MIN_PILOT_SCORE = 35

MAX_PILOT_SCORE = 59

MIN_PILOT_RR = 2.00



PILOT_NOTIFY_COOLDOWN_SECONDS = int(os.getenv("PILOT_NOTIFY_COOLDOWN_SECONDS", "1800"))





def load_env_file():

    for name in [".env", ".env.local"]:

        path = ROOT / name

        if not path.exists():

            continue



        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():

            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:

                continue

            key, value = line.split("=", 1)

            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))





def write_log(message):

    WATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with WATCH_LOG_PATH.open("a", encoding="utf-8") as f:

        f.write(f"[{ts}] {message}\n")





def compact(obj):

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))





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



    if MIN_PILOT_SCORE <= row["_score"] <= MAX_PILOT_SCORE and row["_rr"] >= MIN_PILOT_RR:

        return "PILOT_ELIGIBLE"



    return "PREDICT_ONLY"





def reason_text(row):

    reasons = row.get("_reasons") or []

    if isinstance(reasons, list):

        return "; ".join(str(x) for x in reasons[:4])

    return str(reasons)





def candidate_key(row):

    return f"{row['_symbol']}:{row['_direction']}:{round(row['_last'], 8)}:{round(row['_rr'], 2)}"





def load_state():

    try:

        if STATE_PATH.exists():

            return json.loads(STATE_PATH.read_text(encoding="utf-8"))

    except Exception:

        pass

    return {}





def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")





def should_notify(candidates):

    if not candidates:

        return False, "no_candidates"



    state = load_state()

    now = time.time()

    last_ts = float(state.get("last_sent_ts") or 0)



    if now - last_ts < PILOT_NOTIFY_COOLDOWN_SECONDS:

        return False, f"cooldown_remaining={int(PILOT_NOTIFY_COOLDOWN_SECONDS - (now - last_ts))}"



    keys = [candidate_key(c) for c in candidates]

    last_keys = state.get("last_keys") or []



    if keys == last_keys:

        return False, "same_candidates"



    return True, "ok"





def mark_sent(candidates):

    state = {

        "last_sent_ts": time.time(),

        "last_keys": [candidate_key(c) for c in candidates],

    }

    save_state(state)





def send_telegram(text):

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    chat_id = os.getenv("TELEGRAM_CHAT_ID")



    if not token or not chat_id:

        write_log("PILOT_TELEGRAM_SKIP missing token or chat_id")

        return False



    try:

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        r = requests.post(

            url,

            data={

                "chat_id": chat_id,

                "text": text,

                "disable_web_page_preview": True,

            },

            timeout=12,

        )



        try:

            payload = r.json()

            safe = {

                "status": r.status_code,

                "ok": bool(payload.get("ok")),

                "message_id": (payload.get("result") or {}).get("message_id"),

                "description": payload.get("description"),

            }

            write_log("PILOT_TELEGRAM_RESULT " + compact(safe))

            return bool(payload.get("ok"))

        except Exception:

            write_log(f"PILOT_TELEGRAM_RESULT status={r.status_code} body_redacted=1")

            return r.status_code == 200



    except Exception as e:

        write_log(f"PILOT_TELEGRAM_ERROR {type(e).__name__}: {e}")

        return False





def format_message(candidates, batch):

    lines = []

    lines.append("[BitSwipe 실전 파일럿 후보]")

    lines.append("")

    lines.append("자동 진입 금지. 초소액 수동 검토만 가능.")

    lines.append("")

    lines.append("파일럿 규칙:")

    lines.append("- 레버리지 1~2배 이하")

    lines.append("- 1회 손실 계좌의 0.25% 이하")

    lines.append("- 하루 2연패 시 중단")

    lines.append("- 차트 직접 확인 전 진입 금지")

    lines.append("")

    lines.append(f"후보 수: {len(candidates)}개")

    lines.append("")



    for c in candidates[:5]:

        lines.append(

            f"{c['_symbol']} {c['_direction']} / PILOT_ELIGIBLE\n"

            f"- 점수: {c['_score']:.0f}\n"

            f"- RR: {c['_rr']:.2f}\n"

            f"- 현재가: {c['_last']}\n"

            f"- 지지: {c['_support']}\n"

            f"- 저항: {c['_resistance']}\n"

            f"- 기존차단: {c['_blocked']}"

        )



        rt = reason_text(c)

        if rt:

            lines.append(f"- 이유: {rt}")



        lines.append("")



    lines.append("주의: 이 메시지는 매수/매도 지시가 아니라 파일럿 후보 알림임.")

    return "\n".join(lines)





def main():

    load_env_file()



    rows = load_rows()

    batch = latest_batch(rows)



    if not batch:

        write_log("PILOT_NOTIFY_SKIP no_latest_batch")

        return



    classes = Counter(classify(r) for r in batch)

    pilots = [r for r in batch if classify(r) == "PILOT_ELIGIBLE"]

    pilots.sort(key=lambda r: (r["_rr"], r["_score"]), reverse=True)



    write_log("PILOT_SCAN " + compact({

        "rows": len(batch),

        "classes": dict(classes),

        "pilots": len(pilots),

    }))



    ok_to_send, reason = should_notify(pilots)



    if not ok_to_send:

        write_log("PILOT_NOTIFY_SKIP " + reason)

        return



    msg = format_message(pilots, batch)

    ok = send_telegram(msg)



    if ok:

        mark_sent(pilots)



    write_log("PILOT_NOTIFY_ATTEMPT " + compact({

        "telegram_ok": bool(ok),

        "pilots": len(pilots),

    }))





if __name__ == "__main__":

    main()


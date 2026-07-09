
#!/usr/bin/env python3

import json

import os

import sys

import time

from pathlib import Path

from datetime import datetime, timezone

from collections import Counter



import requests



ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_LOG = ROOT / "logs/candidates.jsonl"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"

STATE_PATH = ROOT / "logs/pilot_policy_notify_state.json"



LATEST_BATCH_GAP_SECONDS = 10

DEDUP_SECONDS = 30 * 60



STRICT_MIN_SCORE = 30

STRICT_MAX_SCORE = 59

STRICT_MIN_RR = 2.0





def load_env_file(path):

    if not path.exists():

        return

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():

        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:

            continue

        k, v = line.split("=", 1)

        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))





def load_env():

    load_env_file(ROOT / ".env")

    load_env_file(ROOT / ".env.local")





def now_text():

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")





def log(msg):

    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    with WATCH_LOG.open("a", encoding="utf-8") as f:

        f.write(f"[{now_text()}] {msg}\n")





def compact(obj):

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))





def parse_ts(x):

    try:

        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))

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





def load_rows(limit=5000):

    if not CANDIDATE_LOG.exists():

        return []



    rows = []

    lines = CANDIDATE_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]



    for line in lines:

        try:

            r = json.loads(line)

            ts = parse_ts(r.get("ts"))

            price = fnum(val(r, "last", "price", "close", "entry", default=0))

            if not ts or not price:

                continue



            rows.append({

                "ts": ts,

                "symbol": val(r, "symbol", default="?"),

                "market_type": val(r, "market_type", default="UNKNOWN"),

                "direction": val(r, "direction", default="UNKNOWN"),

                "score": fnum(val(r, "score", "rule_score", default=0)),

                "rr": fnum(val(r, "estimated_rr", "rr", "risk_reward", default=0)),

                "price": price,

                "grade": val(r, "grade", default="?"),

                "blocked_reason": val(r, "blocked_reason", "block_reason", "reason", default="UNKNOWN"),

            })

        except Exception:

            pass



    return rows





def latest_batch(rows):

    if not rows:

        return []

    rows = sorted(rows, key=lambda x: x["ts"])

    batch = [rows[-1]]

    for r in reversed(rows[:-1]):

        gap = (batch[-1]["ts"] - r["ts"]).total_seconds()

        if gap > LATEST_BATCH_GAP_SECONDS:

            break

        batch.append(r)

    return list(reversed(batch))





def classify(r):

    if r["market_type"] != "BINANCE":

        return "EXCLUDE_ETF_OR_UNKNOWN"

    if r["direction"] not in ("LONG", "SHORT"):

        return "NO_TRADE_WAIT"

    if r["direction"] == "SHORT" and STRICT_MIN_SCORE <= r["score"] <= STRICT_MAX_SCORE and r["rr"] >= STRICT_MIN_RR:

        return "PILOT_ELIGIBLE_STRICT"

    if r["rr"] >= STRICT_MIN_RR:

        return "WATCH_STRONG_RR_NOT_STRICT"

    return "NO_TRADE_POLICY"





def load_state():

    try:

        if STATE_PATH.exists():

            return json.loads(STATE_PATH.read_text(encoding="utf-8"))

    except Exception:

        pass

    return {"last_by_key": {}}





def save_state(state):

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")





def key_for(r, cls):

    return f"{r['symbol']}:{r['direction']}:{cls}"





def send_telegram(text):

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:

        log("POLICY_TELEGRAM_SKIP missing_token_or_chat_id")

        return False



    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:

        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)

        data = resp.json()

        log("POLICY_TELEGRAM_RESULT " + compact({

            "status": resp.status_code,

            "ok": bool(data.get("ok")),

            "message_id": ((data.get("result") or {}).get("message_id")),

            "description": data.get("description"),

        }))

        return bool(data.get("ok"))

    except Exception as e:

        log("POLICY_TELEGRAM_ERROR " + str(e))

        return False





def build_message(items):

    lines = [

        "[BitSwipe 엄격 파일럿 후보]",

        "",

        "자동 진입 금지. 초소액 수동 검토만 가능.",

        "정책: BINANCE + SHORT + RR 2.0+ + 점수 30~59",

        "1회 손실 허용: 계좌 0.25% 이하 / 레버리지 1~2배 이하",

        "",

    ]

    for r, cls in items[:5]:

        lines.append(f"- {r['symbol']} {r['direction']} / {cls}")

        lines.append(f"  점수 {r['score']:.0f}, RR {r['rr']:.2f}, 가격 {r['price']}")

        lines.append(f"  등급 {r['grade']}, 기존차단 {r['blocked_reason']}")

        lines.append("")

    lines.append("주의: 표본이 작으므로 매매 강제 아님. 차트 확인 후 보류 가능.")

    return "\n".join(lines)





def main():

    dry_run = "--dry-run" in sys.argv

    load_env()



    batch = latest_batch(load_rows())

    classified = [(r, classify(r)) for r in batch]

    counts = Counter(cls for _, cls in classified)

    strict = [(r, cls) for r, cls in classified if cls == "PILOT_ELIGIBLE_STRICT"]



    log("POLICY_SCAN " + compact({

        "batch_rows": len(batch),

        "classes": dict(counts),

        "strict": len(strict),

        "dry_run": dry_run,

    }))



    print("[BitSwipe Strict Pilot Policy]")

    print(f"latest batch rows: {len(batch)}")

    for k, v in counts.most_common():

        print(f"- {k}: {v}")

    print(f"strict candidates: {len(strict)}")



    if dry_run:

        print("dry-run: 텔레그램 전송 안 함")

        return



    if not strict:

        log("POLICY_NOTIFY_SKIP no_strict_candidates")

        return



    state = load_state()

    send_items = []



    for r, cls in strict:

        key = key_for(r, cls)

        last = float((state.get("last_by_key") or {}).get(key, 0))

        if time.time() - last >= DEDUP_SECONDS:

            send_items.append((r, cls))

            state.setdefault("last_by_key", {})[key] = time.time()



    save_state(state)



    if not send_items:

        log("POLICY_NOTIFY_SKIP duplicate_cooldown")

        return



    ok = send_telegram(build_message(send_items))

    log("POLICY_NOTIFY_ATTEMPT " + compact({"sent": len(send_items), "telegram_ok": ok}))





if __name__ == "__main__":

    main()


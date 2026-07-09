
#!/usr/bin/env python3

import json

import time

from pathlib import Path

from datetime import datetime, timedelta, timezone



ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_LOG = ROOT / "logs/candidates.jsonl"

PREDICTION_LOG = ROOT / "logs/prediction_watch.jsonl"

STATE_PATH = ROOT / "logs/prediction_watch_store_state.json"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



LATEST_BATCH_GAP_SECONDS = 10

MIN_PREDICT_SCORE = 20

MIN_PREDICT_RR = 1.20

STRONG_RR = 2.00

DEDUP_SECONDS = 45 * 60





def now_utc_text():

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")





def log(msg):

    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    with WATCH_LOG.open("a", encoding="utf-8") as f:

        f.write(f"[{now_utc_text()}] {msg}\n")





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

    if not CANDIDATE_LOG.exists():

        return []



    rows = []

    for line in CANDIDATE_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:

        try:

            r = json.loads(line)

            r["_ts"] = parse_ts(r.get("ts"))

            r["_symbol"] = val(r, "symbol", default="?")

            r["_market_type"] = val(r, "market_type", default="UNKNOWN")

            r["_direction"] = val(r, "direction", default="UNKNOWN")

            r["_score"] = fnum(val(r, "score", "rule_score", default=0))

            r["_rr"] = fnum(val(r, "estimated_rr", "rr", "risk_reward", default=0))

            r["_grade"] = val(r, "grade", default="?")

            r["_last"] = fnum(val(r, "last", "price", "close", "entry", default=0))

            r["_support"] = fnum(val(r, "support", default=0))

            r["_resistance"] = fnum(val(r, "resistance", default=0))

            r["_blocked"] = val(r, "blocked_reason", "block_reason", "reason", default="UNKNOWN")

            r["_reasons"] = val(r, "reasons", default=[]) or []

            rows.append(r)

        except Exception:

            pass



    return [r for r in rows if r.get("_ts") and r.get("_last")]





def latest_batch(rows):

    if not rows:

        return []

    rows = sorted(rows, key=lambda r: r["_ts"])

    batch = [rows[-1]]

    for r in reversed(rows[:-1]):

        gap = (batch[-1]["_ts"] - r["_ts"]).total_seconds()

        if gap > LATEST_BATCH_GAP_SECONDS:

            break

        batch.append(r)

    return list(reversed(batch))





def prediction_class(r):

    if r["_market_type"] != "BINANCE":

        return None

    if r["_direction"] not in ("LONG", "SHORT"):

        return None

    if r["_score"] < MIN_PREDICT_SCORE:

        return None

    if r["_rr"] < MIN_PREDICT_RR:

        return None

    if r["_rr"] >= STRONG_RR:

        return "PREDICT_STRONG_RR"

    return "PREDICT_WATCH_RR"





def reason_text(r):

    reasons = r.get("_reasons") or []

    if isinstance(reasons, list):

        return "; ".join(str(x) for x in reasons[:6])

    return str(reasons)





def load_state():

    try:

        if STATE_PATH.exists():

            return json.loads(STATE_PATH.read_text(encoding="utf-8"))

    except Exception:

        pass

    return {"last_by_key": {}}





def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")





def dedup_key(r, cls):

    return f"{r['_symbol']}:{r['_direction']}:{cls}"





def should_store(r, cls, state):

    key = dedup_key(r, cls)

    last_ts = float((state.get("last_by_key") or {}).get(key, 0))

    return time.time() - last_ts >= DEDUP_SECONDS





def mark_store(r, cls, state):

    state.setdefault("last_by_key", {})[dedup_key(r, cls)] = time.time()





def make_prediction(r, cls):

    created_at = r["_ts"].isoformat()

    pid = f"{created_at}|{r['_symbol']}|{r['_direction']}|{round(r['_last'], 8)}|{cls}"

    return {

        "prediction_id": pid,

        "created_at": created_at,

        "symbol": r["_symbol"],

        "market_type": r["_market_type"],

        "direction": r["_direction"],

        "prediction_class": cls,

        "score": r["_score"],

        "rr": r["_rr"],

        "grade": r["_grade"],

        "entry_price": r["_last"],

        "support": r["_support"],

        "resistance": r["_resistance"],

        "blocked_reason": r["_blocked"],

        "reason_text": reason_text(r),

        "status": "PENDING",

    }





def append_jsonl(path, rows):

    if not rows:

        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:

        for r in rows:

            f.write(json.dumps(r, ensure_ascii=False) + "\n")





def main():

    rows = load_rows()

    batch = latest_batch(rows)

    state = load_state()



    stored = []

    eligible_seen = 0



    for r in batch:

        cls = prediction_class(r)

        if not cls:

            continue

        eligible_seen += 1

        if not should_store(r, cls, state):

            continue

        stored.append(make_prediction(r, cls))

        mark_store(r, cls, state)



    append_jsonl(PREDICTION_LOG, stored)

    save_state(state)



    log("PREDICTION_STORE " + compact({

        "batch_rows": len(batch),

        "eligible_seen": eligible_seen,

        "stored": len(stored),

    }))



    print(f"stored {len(stored)} prediction candidates")





if __name__ == "__main__":

    main()



#!/usr/bin/env python3

import json

from pathlib import Path

from collections import defaultdict

from datetime import datetime, timedelta, timezone



ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_LOG = ROOT / "logs/candidates.jsonl"

PREDICTION_LOG = ROOT / "logs/prediction_watch.jsonl"

OUTCOME_LOG = ROOT / "logs/prediction_outcomes.jsonl"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"



HORIZONS_MIN = [60, 240, 720]

MIN_MOVE_PCT = 0.10

MIN_PREDICT_SCORE = 20

MIN_PREDICT_RR = 1.20

STRONG_RR = 2.00

DEDUP_MINUTES = 45

MAX_FUTURE_GAP_MIN = 45





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





def load_candidate_rows():

    rows = []

    for r in load_jsonl(CANDIDATE_LOG):

        ts = parse_ts(r.get("ts"))

        symbol = val(r, "symbol", default="?")

        price = fnum(val(r, "last", "price", "close", "entry", default=0))

        if not ts or not symbol or not price:

            continue



        reasons = val(r, "reasons", default=[]) or []

        reason_text = "; ".join(str(x) for x in reasons[:6]) if isinstance(reasons, list) else str(reasons)



        rows.append({

            "ts": ts,

            "symbol": symbol,

            "market_type": val(r, "market_type", default="UNKNOWN"),

            "direction": val(r, "direction", default="UNKNOWN"),

            "score": fnum(val(r, "score", "rule_score", default=0)),

            "rr": fnum(val(r, "estimated_rr", "rr", "risk_reward", default=0)),

            "grade": val(r, "grade", default="?"),

            "entry_price": price,

            "support": fnum(val(r, "support", default=0)),

            "resistance": fnum(val(r, "resistance", default=0)),

            "blocked_reason": val(r, "blocked_reason", "block_reason", "reason", default="UNKNOWN"),

            "reason_text": reason_text,

        })



    return sorted(rows, key=lambda x: x["ts"])





def prediction_class(r):

    if r["market_type"] != "BINANCE":

        return None

    if r["direction"] not in ("LONG", "SHORT"):

        return None

    if r["score"] < MIN_PREDICT_SCORE:

        return None

    if r["rr"] < MIN_PREDICT_RR:

        return None

    if r["rr"] >= STRONG_RR:

        return "PREDICT_STRONG_RR"

    return "PREDICT_WATCH_RR"





def make_prediction(r, cls):

    created_at = r["ts"].isoformat()

    pid = f"{created_at}|{r['symbol']}|{r['direction']}|{round(r['entry_price'], 8)}|{cls}"

    return {

        "prediction_id": pid,

        "created_at": created_at,

        "symbol": r["symbol"],

        "market_type": r["market_type"],

        "direction": r["direction"],

        "prediction_class": cls,

        "score": r["score"],

        "rr": r["rr"],

        "grade": r["grade"],

        "entry_price": r["entry_price"],

        "support": r["support"],

        "resistance": r["resistance"],

        "blocked_reason": r["blocked_reason"],

        "reason_text": r["reason_text"],

        "status": "BACKFILLED",

    }





def existing_ids():

    return set(r.get("prediction_id") for r in load_jsonl(PREDICTION_LOG) if r.get("prediction_id"))





def existing_outcome_keys():

    keys = set()

    for r in load_jsonl(OUTCOME_LOG):

        pid = r.get("prediction_id")

        h = r.get("horizon_min")

        if pid and h:

            keys.add(f"{pid}|{h}")

    return keys





def dedup_predictions(preds):

    kept = []

    last_seen = {}

    for p in sorted(preds, key=lambda x: x["created_at"]):

        ts = parse_ts(p["created_at"])

        key = (p["symbol"], p["direction"], p["prediction_class"])

        prev = last_seen.get(key)

        if prev and (ts - prev).total_seconds() < DEDUP_MINUTES * 60:

            continue

        kept.append(p)

        last_seen[key] = ts

    return kept





def build_price_index(rows):

    by = defaultdict(list)

    for r in rows:

        by[r["symbol"]].append({"ts": r["ts"], "price": r["entry_price"]})

    return by





def find_future(by_symbol, symbol, target_ts):

    best = None

    best_gap = None

    for r in by_symbol.get(symbol, []):

        if r["ts"] < target_ts:

            continue

        gap = abs((r["ts"] - target_ts).total_seconds())

        if best is None or gap < best_gap:

            best = r

            best_gap = gap

    if best is None:

        return None

    if best_gap > MAX_FUTURE_GAP_MIN * 60:

        return None

    return best





def direction_ret_pct(direction, entry, future):

    raw = (future / entry - 1.0) * 100.0

    if direction == "SHORT":

        raw = -raw

    return raw





def classify(ret):

    if ret >= MIN_MOVE_PCT:

        return "WIN"

    if ret <= -MIN_MOVE_PCT:

        return "LOSS"

    return "FLAT"





def make_outcome(p, h, future):

    entry = fnum(p.get("entry_price"))

    future_price = fnum(future["price"])

    ret = direction_ret_pct(p.get("direction"), entry, future_price)

    return {

        "prediction_id": p.get("prediction_id"),

        "created_at": p.get("created_at"),

        "evaluated_at": now_utc_text(),

        "horizon_min": h,

        "symbol": p.get("symbol"),

        "market_type": p.get("market_type"),

        "direction": p.get("direction"),

        "prediction_class": p.get("prediction_class"),

        "score": p.get("score"),

        "rr": p.get("rr"),

        "grade": p.get("grade"),

        "entry_price": entry,

        "future_ts": future["ts"].isoformat(),

        "future_price": future_price,

        "direction_return_pct": ret,

        "outcome": classify(ret),

        "min_move_pct": MIN_MOVE_PCT,

        "source": "backfill",

    }





def append_jsonl(path, rows):

    if not rows:

        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:

        for r in rows:

            f.write(json.dumps(r, ensure_ascii=False) + "\n")





def main():

    rows = load_candidate_rows()

    ids = existing_ids()



    raw = []

    for r in rows:

        cls = prediction_class(r)

        if not cls:

            continue

        p = make_prediction(r, cls)

        if p["prediction_id"] not in ids:

            raw.append(p)



    preds = dedup_predictions(raw)

    append_jsonl(PREDICTION_LOG, preds)



    by_symbol = build_price_index(rows)

    done = existing_outcome_keys()

    outcomes = []



    for p in preds:

        created = parse_ts(p.get("created_at"))

        if not created:

            continue



        for h in HORIZONS_MIN:

            key = f"{p['prediction_id']}|{h}"

            if key in done:

                continue



            target = created + timedelta(minutes=h)

            future = find_future(by_symbol, p["symbol"], target)

            if not future:

                continue



            outcomes.append(make_outcome(p, h, future))

            done.add(key)



    append_jsonl(OUTCOME_LOG, outcomes)



    msg = {"candidate_rows": len(rows), "new_predictions": len(preds), "new_outcomes": len(outcomes)}

    log("PREDICTION_BACKFILL " + compact(msg))



    print("[Prediction Backfill]")

    print(f"candidate rows: {len(rows)}")

    print(f"new predictions: {len(preds)}")

    print(f"new outcomes: {len(outcomes)}")





if __name__ == "__main__":

    main()


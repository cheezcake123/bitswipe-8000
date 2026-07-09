
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





def load_jsonl(path, limit=None):

    if not path.exists():

        return []

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    if limit:

        lines = lines[-limit:]

    rows = []

    for line in lines:

        try:

            rows.append(json.loads(line))

        except Exception:

            pass

    return rows





def load_prices():

    by_symbol = defaultdict(list)

    for r in load_jsonl(CANDIDATE_LOG, limit=50000):

        ts = parse_ts(r.get("ts"))

        symbol = val(r, "symbol")

        price = fnum(val(r, "last", "price", "close", "entry", default=0))

        if ts and symbol and price:

            by_symbol[symbol].append({"ts": ts, "price": price})

    for sym in by_symbol:

        by_symbol[sym].sort(key=lambda x: x["ts"])

    return by_symbol





def done_keys():

    keys = set()

    for r in load_jsonl(OUTCOME_LOG):

        pid = r.get("prediction_id")

        h = r.get("horizon_min")

        if pid and h:

            keys.add(f"{pid}|{h}")

    return keys





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





def make_outcome(pred, horizon, future):

    entry = fnum(pred.get("entry_price"))

    future_price = fnum(future.get("price"))

    ret = direction_ret_pct(pred.get("direction"), entry, future_price)

    return {

        "prediction_id": pred.get("prediction_id"),

        "created_at": pred.get("created_at"),

        "evaluated_at": now_utc_text(),

        "horizon_min": horizon,

        "symbol": pred.get("symbol"),

        "market_type": pred.get("market_type"),

        "direction": pred.get("direction"),

        "prediction_class": pred.get("prediction_class"),

        "score": pred.get("score"),

        "rr": pred.get("rr"),

        "grade": pred.get("grade"),

        "entry_price": entry,

        "future_ts": future["ts"].isoformat(),

        "future_price": future_price,

        "direction_return_pct": ret,

        "outcome": classify(ret),

        "min_move_pct": MIN_MOVE_PCT,

    }





def append_jsonl(path, rows):

    if not rows:

        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:

        for r in rows:

            f.write(json.dumps(r, ensure_ascii=False) + "\n")





def main():

    predictions = load_jsonl(PREDICTION_LOG)

    if not predictions:

        log("PREDICTION_EVAL_SKIP no_predictions")

        print("no predictions")

        return



    by_symbol = load_prices()

    done = done_keys()

    now = datetime.now(timezone.utc)



    outcomes = []

    pending = 0



    for p in predictions:

        pid = p.get("prediction_id")

        created_at = parse_ts(p.get("created_at"))

        if not pid or not created_at:

            continue



        for h in HORIZONS_MIN:

            key = f"{pid}|{h}"

            if key in done:

                continue



            target = created_at + timedelta(minutes=h)

            if now < target:

                pending += 1

                continue



            future = find_future(by_symbol, p.get("symbol"), target)

            if not future:

                pending += 1

                continue



            outcomes.append(make_outcome(p, h, future))

            done.add(key)



    append_jsonl(OUTCOME_LOG, outcomes)



    log("PREDICTION_EVAL " + compact({

        "predictions": len(predictions),

        "new_outcomes": len(outcomes),

        "pending": pending,

    }))



    print(f"new outcomes: {len(outcomes)}")

    print(f"pending: {pending}")





if __name__ == "__main__":

    main()


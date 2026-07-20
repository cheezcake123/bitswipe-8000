from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pandas as pd

from strategy_arena.basis_execution import fetch_funding
from strategy_arena.funding_carry_v1 import FundingCarryV1Config


def _parse_z(value):
    try:
        meta = ast.literal_eval(value) if isinstance(value, str) else value
        return meta.get("funding_zscore") if isinstance(meta, dict) else None
    except (ValueError, SyntaxError):
        return None


def attach_actual_entry_z(trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True, format="mixed")
    t["signal_hour"] = t["entry_time"].dt.floor("h") - pd.Timedelta(hours=1)
    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True, format="mixed")
    s["signal_hour"] = s["timestamp"].dt.floor("h")
    s["funding_zscore"] = s["metadata"].map(_parse_z)
    entries = s[s["signal"].isin(["LONG", "SHORT"])][["signal_hour", "signal", "funding_zscore"]]
    return t.merge(entries, left_on=["signal_hour", "side"], right_on=["signal_hour", "signal"], how="left")


def build_carry_intervals(funding: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    threshold = FundingCarryV1Config().entry_funding_rate
    intervals = []
    active = False
    start = None
    for row in f.itertuples(index=False):
        ts, rate = row.timestamp, float(row.funding_rate)
        if not active and rate >= threshold:
            active, start = True, ts
        elif active and rate <= 0.0:
            intervals.append((start, ts))
            active, start = False, None
    if active and start is not None:
        intervals.append((start, f["timestamp"].iloc[-1] + pd.Timedelta(hours=8)))
    return intervals


def in_any_interval(ts: pd.Timestamp, intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> bool:
    return any(start <= ts < end for start, end in intervals)


def main() -> None:
    p = argparse.ArgumentParser(description="Correct entry-level environment/overlap reporting for Funding Extreme long validation")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--output-dir", default="data/arena/funding_extreme_long_validation")
    args = p.parse_args()

    out = Path(args.output_dir)
    trades = pd.read_csv(out / "full_2020_2026" / "net_trades.csv")
    signals = pd.read_csv(out / "full_2020_2026" / "signals.csv")
    actual = attach_actual_entry_z(trades, signals)
    actual["year"] = actual["entry_time"].dt.year

    yearly = []
    for year, g in actual.groupby("year"):
        yearly.append({
            "year": int(year),
            "actual_trade_entries": int(len(g)),
            "long_trade_entries": int((g["side"] == "LONG").sum()),
            "short_trade_entries": int((g["side"] == "SHORT").sum()),
            "mean_abs_entry_zscore": float(g["funding_zscore"].abs().mean()),
            "median_abs_entry_zscore": float(g["funding_zscore"].abs().median()),
        })
    pd.DataFrame(yearly).to_csv(out / "yearly_extreme_environment.csv", index=False)

    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    intervals = build_carry_intervals(funding)
    actual["signal_decision_time"] = actual["entry_time"] - pd.Timedelta(milliseconds=1)
    actual["carry_active"] = actual["signal_decision_time"].map(lambda ts: in_any_interval(ts, intervals))
    short = actual[actual["side"] == "SHORT"]
    long = actual[actual["side"] == "LONG"]
    overlap = {
        "funding_carry_episodes": int(len(intervals)),
        "funding_extreme_trade_entries": int(len(actual)),
        "all_entries_during_carry": int(actual["carry_active"].sum()),
        "all_entry_overlap_rate": float(actual["carry_active"].mean()) if len(actual) else 0.0,
        "short_entries_during_carry_same_perp_direction": int(short["carry_active"].sum()),
        "short_entry_overlap_rate": float(short["carry_active"].mean()) if len(short) else 0.0,
        "long_entries_during_carry_opposite_perp_direction": int(long["carry_active"].sum()),
        "long_entry_conflict_rate": float(long["carry_active"].mean()) if len(long) else 0.0,
        "note": "Entry-level overlap. Funding Carry holds Spot LONG + Perp SHORT. Funding Extreme SHORT reinforces the perpetual short leg; Funding Extreme LONG opposes it and requires Strategy Selector coordination.",
    }

    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["funding_carry_relationship"] = overlap
    summary["yearly_extreme_environment_definition"] = "Actual completed trade entries, not repeated same-direction signals while a position is already open."
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"funding_carry_relationship": overlap, "yearly_extreme_environment": yearly}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

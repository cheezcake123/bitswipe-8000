from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.forward_oos_selector_foundation import (
    DEFAULT_LEAGUE_ROOT,
    FORWARD_OOS_START_UTC,
    AppendOnlyLeagueStore,
    RegimeDetectorV1,
    StrategySelectorFoundation,
    collector_health_snapshot,
    load_default_registry,
)
from strategy_arena.market_store import AppendOnlyMarketStore

LIVE_FUTURES_SOURCE = "binance_usdm_rest"
LIVE_SPOT_SOURCE = "binance_spot_rest"


def _hourly_with_known_funding(store: AppendOnlyMarketStore, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Historical rows may provide backward-looking warm-up only.
    historical = store.read("futures_ohlcv", symbol=symbol, end=FORWARD_OOS_START_UTC - pd.Timedelta(milliseconds=1))
    live = store.read("futures_ohlcv", symbol=symbol, source=LIVE_FUTURES_SOURCE, start=FORWARD_OOS_START_UTC)
    if live.empty:
        return pd.DataFrame(), live

    # Bound warm-up for regime calculations while keeping it strictly pre-OOS.
    warmup = historical.sort_values("timestamp").drop_duplicates("timestamp").tail(60 * 24 * 60)
    combined = pd.concat([warmup, live], ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
    hourly = (
        combined.set_index("timestamp")
        .resample("1h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    hourly["decision_timestamp"] = hourly["timestamp"] + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)

    funding_history = store.read("funding_rate", symbol=symbol, end=FORWARD_OOS_START_UTC - pd.Timedelta(milliseconds=1))
    funding_live = store.read("funding_rate", symbol=symbol, source=LIVE_FUTURES_SOURCE, start=FORWARD_OOS_START_UTC)
    funding = pd.concat([funding_history, funding_live], ignore_index=True) if not funding_history.empty or not funding_live.empty else pd.DataFrame()
    if funding.empty:
        hourly["funding_rate"] = np.nan
        hourly["funding_timestamp"] = pd.NaT
    else:
        f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
        hourly = pd.merge_asof(
            hourly.sort_values("decision_timestamp"),
            f.rename(columns={"timestamp": "funding_timestamp"}),
            left_on="decision_timestamp",
            right_on="funding_timestamp",
            direction="backward",
            allow_exact_matches=True,
        )
    return hourly.sort_values("timestamp").reset_index(drop=True), live


def run_live_snapshot(store_root: str, league_root: str, symbol: str = "BTCUSDT", risk_gate_passed: bool = False) -> dict:
    registry = load_default_registry()
    store = AppendOnlyMarketStore(store_root)
    league = AppendOnlyLeagueStore(league_root)
    health = collector_health_snapshot(store)
    hourly, live_minutes = _hourly_with_known_funding(store, symbol)

    if live_minutes.empty or hourly.empty:
        result = {
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "accepted_forward_source": LIVE_FUTURES_SOURCE,
            "forward_live_rows_available": 0,
            "selector_decisions_written": 0,
            "collector_health": health,
            "orders_enabled": False,
            "paper_trading_enabled": False,
            "note": "No Forward OOS rows from the live append-only collector source are available.",
        }
    else:
        latest_live = pd.to_datetime(live_minutes["timestamp"], utc=True).max()
        context = hourly[hourly["timestamp"] <= latest_live].copy()
        regime = RegimeDetectorV1().detect(context)
        decisions = StrategySelectorFoundation(registry).evaluate(regime, risk_gate_passed=risk_gate_passed)
        league.append("selector_decisions", decisions)
        result = {
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "accepted_forward_source": LIVE_FUTURES_SOURCE,
            "forward_live_rows_available": int(len(live_minutes)),
            "latest_forward_timestamp": latest_live.isoformat(),
            "latest_regime": asdict(regime),
            "selector_decisions_written": len(decisions),
            "risk_gate_passed": bool(risk_gate_passed),
            "collector_health": health,
            "orders_enabled": False,
            "paper_trading_enabled": False,
        }
    target = Path(league_root) / "latest_live_foundation_snapshot.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one live-source-only Forward OOS selector foundation snapshot; no orders")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--league-root", default=DEFAULT_LEAGUE_ROOT)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--risk-gate-passed", action="store_true", help="Eligibility observation only; still cannot create Paper or Live orders")
    args = parser.parse_args()
    print(json.dumps(run_live_snapshot(args.store_root, args.league_root, args.symbol, args.risk_gate_passed), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

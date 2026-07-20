from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine
from strategy_arena.basis_execution import BasisExecutionConfig, simulate as simulate_basis
from strategy_arena.basis_research import BasisResearchConfig, add_research_features
from strategy_arena.forward_oos_live_runner import LIVE_FUTURES_SOURCE, LIVE_SPOT_SOURCE
from strategy_arena.forward_oos_runtime import validate_frozen_runtime_parameters
from strategy_arena.forward_oos_selector_foundation import (
    DEFAULT_LEAGUE_ROOT,
    FORWARD_OOS_START_UTC,
    AppendOnlyLeagueStore,
    ConflictManager,
    ForwardScorecard,
    PositionRequest,
    RegimeDetectorV1,
    SignalOverlapAnalyzer,
    StrategySelectorFoundation,
    collector_health_snapshot,
    load_default_registry,
)
from strategy_arena.funding_aligned_momentum_backtest import prepare_data as prepare_fam_data
from strategy_arena.funding_aligned_momentum_v1 import FundingAlignedMomentumV1
from strategy_arena.funding_carry_v1 import FundingCarryV1Config, simulate as simulate_funding_carry
from strategy_arena.market_store import AppendOnlyMarketStore
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1
from strategy_arena.trend_breakout_backtest import add_precomputed_trend_features
from strategy_arena.trend_breakout_v1 import VolatilityAdjustedTrendBreakoutV1
from strategy_arena.volatility_compression_backtest import add_precomputed_compression_features
from strategy_arena.volatility_compression_v1 import VolatilityCompressionBreakoutV1

SYMBOL = "BTCUSDT"
HISTORICAL_FUTURES_SOURCE = "binance_vision_usdm"
HISTORICAL_SPOT_SOURCE = "binance_vision_spot"
WARMUP_DAYS = 400
BASIS_WARMUP_DAYS = 31

NET_CONFIG = BacktestConfig(
    initial_equity=10_000.0,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0005,
    slippage_rate=0.0005,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)


def _preferred_history(
    store: AppendOnlyMarketStore,
    dataset: str,
    symbol: str,
    preferred_source: str | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    frame = pd.DataFrame()
    if preferred_source:
        frame = store.read(dataset, symbol=symbol, source=preferred_source, start=start, end=end)
    if frame.empty:
        frame = store.read(dataset, symbol=symbol, start=start, end=end)
    return frame


def _bounded_series(
    store: AppendOnlyMarketStore,
    dataset: str,
    symbol: str,
    *,
    preferred_history_source: str | None,
    live_source: str,
    warmup_days: int,
) -> pd.DataFrame:
    history_start = FORWARD_OOS_START_UTC - pd.Timedelta(days=warmup_days)
    history_end = FORWARD_OOS_START_UTC - pd.Timedelta(milliseconds=1)
    historical = _preferred_history(
        store,
        dataset,
        symbol,
        preferred_history_source,
        history_start,
        history_end,
    )
    live = store.read(dataset, symbol=symbol, source=live_source, start=FORWARD_OOS_START_UTC)
    if historical.empty and live.empty:
        return pd.DataFrame()
    out = pd.concat([historical, live], ignore_index=True)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    return out.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def build_forward_market_context(store: AppendOnlyMarketStore, symbol: str = SYMBOL) -> dict[str, pd.DataFrame | dict]:
    futures = _bounded_series(
        store,
        "futures_ohlcv",
        symbol,
        preferred_history_source=HISTORICAL_FUTURES_SOURCE,
        live_source=LIVE_FUTURES_SOURCE,
        warmup_days=WARMUP_DAYS,
    )
    spot = _bounded_series(
        store,
        "spot_ohlcv",
        symbol,
        preferred_history_source=HISTORICAL_SPOT_SOURCE,
        live_source=LIVE_SPOT_SOURCE,
        warmup_days=WARMUP_DAYS,
    )
    funding = _bounded_series(
        store,
        "funding_rate",
        symbol,
        preferred_history_source=None,
        live_source=LIVE_FUTURES_SOURCE,
        warmup_days=WARMUP_DAYS,
    )
    oi = _bounded_series(
        store,
        "open_interest",
        symbol,
        preferred_history_source=None,
        live_source=LIVE_FUTURES_SOURCE,
        warmup_days=WARMUP_DAYS,
    )

    live_futures = store.read("futures_ohlcv", symbol=symbol, source=LIVE_FUTURES_SOURCE, start=FORWARD_OOS_START_UTC)
    live_spot = store.read("spot_ohlcv", symbol=symbol, source=LIVE_SPOT_SOURCE, start=FORWARD_OOS_START_UTC)
    live_funding = store.read("funding_rate", symbol=symbol, source=LIVE_FUTURES_SOURCE, start=FORWARD_OOS_START_UTC)
    live_oi = store.read("open_interest", symbol=symbol, source=LIVE_FUTURES_SOURCE, start=FORWARD_OOS_START_UTC)

    data_quality = {
        "accepted_forward_futures_source": LIVE_FUTURES_SOURCE,
        "accepted_forward_spot_source": LIVE_SPOT_SOURCE,
        "live_futures_rows": int(len(live_futures)),
        "live_spot_rows": int(len(live_spot)),
        "live_funding_events": int(len(live_funding)),
        "live_oi_rows": int(len(live_oi)),
    }
    if futures.empty:
        return {
            "hourly": pd.DataFrame(),
            "minute_pair": pd.DataFrame(),
            "funding": funding,
            "oi": oi,
            "data_quality": data_quality,
        }

    futures = futures.copy()
    futures["close_time"] = pd.to_datetime(futures["close_time"], utc=True)
    hourly = (
        futures.set_index("timestamp")
        .resample("1h")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "close_time": "max",
                "symbol": "count",
            }
        )
        .rename(columns={"symbol": "minute_bars"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    hourly["symbol"] = symbol
    hourly["source"] = "forward_oos_mixed_warmup_live"
    hourly["decision_timestamp"] = hourly["close_time"]
    hourly["complete_hour"] = hourly["minute_bars"] == 60

    if funding.empty:
        hourly["funding_rate"] = np.nan
        hourly["funding_available_at"] = pd.NaT
        hourly["funding_payment_rate"] = 0.0
    else:
        f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
        f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        hourly = pd.merge_asof(
            hourly.sort_values("decision_timestamp"),
            f.rename(columns={"timestamp": "funding_available_at"}),
            left_on="decision_timestamp",
            right_on="funding_available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        payment = f.assign(payment_hour=f["timestamp"].dt.floor("h")).groupby("payment_hour")["funding_rate"].sum()
        hourly["funding_payment_rate"] = hourly["timestamp"].map(payment).fillna(0.0).astype(float)

    if oi.empty:
        hourly["open_interest"] = np.nan
        hourly["oi_available_at"] = pd.NaT
    else:
        o = oi[["timestamp", "open_interest"]].copy().sort_values("timestamp")
        o["timestamp"] = pd.to_datetime(o["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        hourly = pd.merge_asof(
            hourly.sort_values("decision_timestamp"),
            o.rename(columns={"timestamp": "oi_available_at"}),
            left_on="decision_timestamp",
            right_on="oi_available_at",
            direction="backward",
            allow_exact_matches=True,
        )

    minute_pair = pd.DataFrame()
    if not spot.empty and not futures.empty:
        spot_pair = spot[["timestamp", "open", "high", "close"]].rename(
            columns={"open": "spot_open", "high": "spot_high", "close": "spot_close"}
        )
        futures_pair = futures[["timestamp", "open", "high", "close"]].rename(
            columns={"open": "perp_open", "high": "perp_high", "close": "perp_close"}
        )
        minute_pair = (
            spot_pair.merge(futures_pair, on="timestamp", how="inner", validate="one_to_one")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    forward_hours = hourly[hourly["timestamp"] >= FORWARD_OOS_START_UTC]
    expected_hours = 0
    if not forward_hours.empty:
        expected_hours = int((forward_hours["timestamp"].max() - FORWARD_OOS_START_UTC) / pd.Timedelta(hours=1)) + 1
    data_quality.update(
        {
            "forward_hour_rows": int(len(forward_hours)),
            "forward_incomplete_hours": int((~forward_hours["complete_hour"]).sum()) if not forward_hours.empty else 0,
            "expected_forward_hours": expected_hours,
            "missing_forward_hours": max(0, expected_hours - len(forward_hours)),
            "minute_pair_rows": int(len(minute_pair[minute_pair["timestamp"] >= FORWARD_OOS_START_UTC])) if not minute_pair.empty else 0,
        }
    )
    return {
        "hourly": hourly.sort_values("timestamp").reset_index(drop=True),
        "minute_pair": minute_pair,
        "funding": funding,
        "oi": oi,
        "data_quality": data_quality,
    }


def _context_frame(hourly: pd.DataFrame, minute_pair: pd.DataFrame) -> pd.DataFrame:
    out = hourly[["timestamp", "decision_timestamp", "close", "funding_rate", "open_interest"]].copy()
    out["seven_day_return"] = out["close"] / out["close"].shift(168) - 1.0
    out["trend_regime"] = np.select(
        [out["seven_day_return"] >= 0.05, out["seven_day_return"] <= -0.05],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[out["seven_day_return"].isna(), "trend_regime"] = "UNKNOWN"
    logret = np.log(out["close"] / out["close"].shift(1))
    out["volatility"] = logret.rolling(24, min_periods=24).std(ddof=0) * np.sqrt(24)
    out["volatility_reference"] = out["volatility"].shift(1).rolling(30 * 24, min_periods=7 * 24).median()
    out["vol_regime"] = np.where(out["volatility"] >= out["volatility_reference"], "HIGH_VOLATILITY", "LOW_VOLATILITY")
    out.loc[out["volatility_reference"].isna(), "vol_regime"] = "UNKNOWN"
    threshold = FundingCarryV1Config().entry_funding_rate
    out["funding_regime"] = np.select(
        [out["funding_rate"] >= threshold, out["funding_rate"] < 0],
        ["HIGH_POSITIVE_FUNDING", "NEGATIVE_FUNDING"],
        default="NORMAL_FUNDING",
    )
    out.loc[out["funding_rate"].isna(), "funding_regime"] = "UNKNOWN"
    out["market_regime"] = out[["trend_regime", "vol_regime", "funding_regime"]].agg("|".join, axis=1)

    out["basis_bps"] = np.nan
    if not minute_pair.empty:
        basis = minute_pair[["timestamp", "spot_close", "perp_close"]].copy()
        basis["basis_bps"] = (basis["perp_close"] - basis["spot_close"]) / basis["spot_close"] * 10_000.0
        out = pd.merge_asof(
            out.sort_values("decision_timestamp"),
            basis[["timestamp", "basis_bps"]].rename(columns={"timestamp": "basis_timestamp"}),
            left_on="decision_timestamp",
            right_on="basis_timestamp",
            direction="backward",
            tolerance=pd.Timedelta(minutes=2),
        )
    return out.sort_values("decision_timestamp").reset_index(drop=True)


def _common_results(hourly: pd.DataFrame, funding: pd.DataFrame) -> dict[str, object]:
    if hourly.empty:
        return {}
    end = hourly["timestamp"].max()
    results = {}
    results["funding_extreme_reversal:v1"] = BacktestEngine(NET_CONFIG).run(
        hourly,
        FundingExtremeReversalV1(),
        FORWARD_OOS_START_UTC,
        end,
    )
    results["oi_momentum:v1"] = BacktestEngine(NET_CONFIG).run(
        hourly,
        OIMomentumV1(),
        FORWARD_OOS_START_UTC,
        end,
    )
    trend_data = add_precomputed_trend_features(hourly.copy())
    results["volatility_adjusted_trend_breakout:v1"] = BacktestEngine(NET_CONFIG).run(
        trend_data,
        VolatilityAdjustedTrendBreakoutV1(),
        FORWARD_OOS_START_UTC,
        end,
    )
    compression_data = add_precomputed_compression_features(hourly.copy())
    results["volatility_compression_breakout:v1"] = BacktestEngine(NET_CONFIG).run(
        compression_data,
        VolatilityCompressionBreakoutV1(),
        FORWARD_OOS_START_UTC,
        end,
    )
    fam_data = prepare_fam_data(hourly.copy(), funding)
    results["funding_aligned_momentum:v1"] = BacktestEngine(NET_CONFIG).run(
        fam_data,
        FundingAlignedMomentumV1(),
        FORWARD_OOS_START_UTC,
        end,
    )
    return results


def _standardize_signals(strategy_key: str, result, registry, context: pd.DataFrame) -> pd.DataFrame:
    signals = result.signals.copy()
    if signals.empty:
        return signals
    signals["timestamp"] = pd.to_datetime(signals["timestamp"], utc=True)
    signals = signals[signals["timestamp"] >= FORWARD_OOS_START_UTC].copy()
    if signals.empty:
        return signals
    record = registry.records[strategy_key]
    context_merge = context.rename(columns={"decision_timestamp": "timestamp"})[
        ["timestamp", "market_regime", "funding_rate", "open_interest", "basis_bps", "volatility"]
    ].sort_values("timestamp")
    signals = pd.merge_asof(signals.sort_values("timestamp"), context_merge, on="timestamp", direction="backward")
    signals["strategy_name"] = record.strategy_name
    signals["strategy_version"] = record.strategy_version
    signals["strategy_fingerprint"] = record.fingerprint
    signals["status"] = record.status
    signals["verdict"] = record.verdict
    signals["allowed_for_selector"] = record.allowed_for_selector
    signals["allowed_for_paper"] = record.allowed_for_paper
    signals["shadow_only"] = not record.allowed_for_selector
    signals["metadata_json"] = signals["metadata"].apply(lambda value: json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str))
    signals["actual_order_created"] = False
    signals["paper_order_created"] = False
    return signals.drop(columns=["metadata"], errors="ignore")


def _standardize_common_trades(strategy_key: str, result, registry) -> pd.DataFrame:
    trades = result.trades.copy()
    if trades.empty:
        return trades
    if "exit_reason" in trades.columns:
        trades = trades[trades["exit_reason"] != "end_of_test"].copy()
    if trades.empty:
        return trades
    record = registry.records[strategy_key]
    trades["timestamp"] = pd.to_datetime(trades["exit_time"], utc=True)
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades = trades[trades["entry_time"] >= FORWARD_OOS_START_UTC].copy()
    trades["strategy_name"] = record.strategy_name
    trades["strategy_version"] = record.strategy_version
    trades["strategy_fingerprint"] = record.fingerprint
    trades["hypothetical_only"] = True
    trades["actual_order_created"] = False
    trades["paper_order_created"] = False
    return trades


def _pair_data_for_forward(minute_pair: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if minute_pair.empty:
        return pd.DataFrame(), pd.DataFrame()
    history = minute_pair[
        (minute_pair["timestamp"] >= FORWARD_OOS_START_UTC - pd.Timedelta(days=BASIS_WARMUP_DAYS))
    ].copy()
    aligned = history.copy()
    aligned["basis_abs"] = aligned["perp_close"] - aligned["spot_close"]
    aligned["basis_pct"] = aligned["basis_abs"] / aligned["spot_close"]
    aligned["basis_bps"] = aligned["basis_pct"] * 10_000.0
    featured = add_research_features(aligned, BasisResearchConfig())
    return history, featured


def _basis_forward_data(featured: pd.DataFrame) -> pd.DataFrame:
    if featured.empty:
        return featured
    before = featured[featured["timestamp"] < FORWARD_OOS_START_UTC]
    forward = featured[featured["timestamp"] >= FORWARD_OOS_START_UTC].copy().reset_index(drop=True)
    if forward.empty:
        return forward
    active_before = False
    for z in before["basis_zscore"].dropna():
        if not active_before and z >= 2.0:
            active_before = True
        elif active_before and abs(z) <= 1.0:
            active_before = False
    if active_before:
        reset_positions = forward.index[forward["basis_zscore"].abs() <= 1.0]
        if len(reset_positions):
            first_reset = int(reset_positions[0])
            forward.loc[:first_reset, "basis_zscore"] = np.nan
        else:
            forward["basis_zscore"] = np.nan
    return forward


def _pair_strategy_results(minute_pair: pd.DataFrame, funding: pd.DataFrame, registry) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    signals: dict[str, pd.DataFrame] = {}
    trades: dict[str, pd.DataFrame] = {}
    history, featured = _pair_data_for_forward(minute_pair)
    if history.empty:
        return signals, trades
    data_eval = history[history["timestamp"] >= FORWARD_OOS_START_UTC].copy().reset_index(drop=True)
    funding_eval = funding[funding["timestamp"] >= FORWARD_OOS_START_UTC].copy().reset_index(drop=True) if not funding.empty else funding
    if not data_eval.empty:
        carry_trades, _, _ = simulate_funding_carry(
            history,
            funding,
            FundingCarryV1Config(),
            evaluation_start=FORWARD_OOS_START_UTC,
            evaluation_end=history["timestamp"].max(),
        )
        if not carry_trades.empty:
            completed = carry_trades[carry_trades["exit_reason"] != "end_of_test"].copy()
            record = registry.records["funding_carry:v1"]
            rows = []
            for trade in completed.to_dict("records"):
                rows.append({
                    "timestamp": trade["signal_time"],
                    "strategy_name": record.strategy_name,
                    "strategy_version": record.strategy_version,
                    "strategy_fingerprint": record.fingerprint,
                    "signal": "PAIR_LONG_SPOT_SHORT_PERP",
                    "confidence": None,
                    "entry_reason": f"Observed funding >= frozen {FundingCarryV1Config().entry_funding_rate:.4f}",
                    "exit_reason": "",
                    "shadow_only": False,
                    "actual_order_created": False,
                    "paper_order_created": False,
                })
                rows.append({
                    "timestamp": trade["exit_signal_time"],
                    "strategy_name": record.strategy_name,
                    "strategy_version": record.strategy_version,
                    "strategy_fingerprint": record.fingerprint,
                    "signal": "EXIT",
                    "confidence": None,
                    "entry_reason": "",
                    "exit_reason": trade["exit_reason"],
                    "shadow_only": False,
                    "actual_order_created": False,
                    "paper_order_created": False,
                })
            signals["funding_carry:v1"] = pd.DataFrame(rows)
            completed["timestamp"] = pd.to_datetime(completed["exit_perp_time"], utc=True)
            completed["entry_time"] = pd.to_datetime(completed["entry_spot_time"], utc=True)
            completed["exit_time"] = pd.to_datetime(completed["exit_perp_time"], utc=True)
            completed["gross_pnl"] = completed["gross_economic_pnl"]
            completed["strategy_name"] = record.strategy_name
            completed["strategy_version"] = record.strategy_version
            completed["strategy_fingerprint"] = record.fingerprint
            completed["hypothetical_only"] = True
            completed["actual_order_created"] = False
            completed["paper_order_created"] = False
            trades["funding_carry:v1"] = completed

    basis_eval = _basis_forward_data(featured)
    if not basis_eval.empty:
        basis_trades, _, _ = simulate_basis(basis_eval, funding_eval, BasisExecutionConfig())
        if not basis_trades.empty:
            record = registry.records["basis_mean_reversion:v1"]
            rows = []
            for trade in basis_trades.to_dict("records"):
                rows.append({
                    "timestamp": trade["signal_time"],
                    "strategy_name": record.strategy_name,
                    "strategy_version": record.strategy_version,
                    "strategy_fingerprint": record.fingerprint,
                    "signal": "PAIR_LONG_SPOT_SHORT_PERP",
                    "confidence": None,
                    "entry_reason": "Frozen positive basis z-score >= 2.0",
                    "exit_reason": "",
                    "shadow_only": True,
                    "actual_order_created": False,
                    "paper_order_created": False,
                })
                rows.append({
                    "timestamp": trade["exit_signal_time"],
                    "strategy_name": record.strategy_name,
                    "strategy_version": record.strategy_version,
                    "strategy_fingerprint": record.fingerprint,
                    "signal": "EXIT",
                    "confidence": None,
                    "entry_reason": "",
                    "exit_reason": "Frozen basis reset |z| <= 1.0",
                    "shadow_only": True,
                    "actual_order_created": False,
                    "paper_order_created": False,
                })
            signals["basis_mean_reversion:v1"] = pd.DataFrame(rows)
            basis_trades["timestamp"] = pd.to_datetime(basis_trades["exit_perp_time"], utc=True)
            basis_trades["entry_time"] = pd.to_datetime(basis_trades["entry_spot_time"], utc=True)
            basis_trades["exit_time"] = pd.to_datetime(basis_trades["exit_perp_time"], utc=True)
            basis_trades["strategy_name"] = record.strategy_name
            basis_trades["strategy_version"] = record.strategy_version
            basis_trades["strategy_fingerprint"] = record.fingerprint
            basis_trades["hypothetical_only"] = True
            basis_trades["actual_order_created"] = False
            basis_trades["paper_order_created"] = False
            trades["basis_mean_reversion:v1"] = basis_trades
    return signals, trades


def _append_unique(store: AppendOnlyLeagueStore, dataset: str, frame: pd.DataFrame, keys: list[str]) -> int:
    if frame.empty:
        return 0
    data = frame.copy()
    for column in [key for key in keys if "time" in key or key == "timestamp"]:
        if column in data.columns:
            data[column] = pd.to_datetime(data[column], utc=True)
    existing = store.read(dataset)
    if not existing.empty and all(key in existing.columns for key in keys):
        existing_keys = set(tuple(str(row[key]) for key in keys) for _, row in existing[keys].iterrows())
        mask = []
        for _, row in data.iterrows():
            key = tuple(str(row[name]) for name in keys)
            mask.append(key not in existing_keys)
        data = data.loc[mask].copy()
    if data.empty:
        return 0
    store.append(dataset, data.to_dict("records"))
    return int(len(data))


def _active_position_requests(signals: pd.DataFrame) -> list[PositionRequest]:
    if signals.empty:
        return []
    requests = []
    for (name, version), group in signals.sort_values("timestamp").groupby(["strategy_name", "strategy_version"]):
        spot = perp = 0.0
        for signal in group["signal"].astype(str):
            if signal == "LONG":
                spot, perp = 0.0, 1.0
            elif signal == "SHORT":
                spot, perp = 0.0, -1.0
            elif signal == "PAIR_LONG_SPOT_SHORT_PERP":
                spot, perp = 1.0, -1.0
            elif signal == "EXIT":
                spot = perp = 0.0
        if spot or perp:
            requests.append(PositionRequest(str(name), str(version), spot_btc=spot, perpetual_btc=perp, margin_usage=0.0, reason="normalized_forward_oos_active_signal"))
    return requests


def run_forward_oos_league_once(
    store_root: str,
    league_root: str = DEFAULT_LEAGUE_ROOT,
    symbol: str = SYMBOL,
    risk_gate_passed: bool = False,
) -> dict:
    registry = load_default_registry()
    runtime_check = validate_frozen_runtime_parameters(registry)
    if not runtime_check["pass"]:
        raise RuntimeError(f"Frozen strategy parameter drift detected: {runtime_check}")

    market_store = AppendOnlyMarketStore(store_root)
    league_store = AppendOnlyLeagueStore(league_root)
    market = build_forward_market_context(market_store, symbol)
    hourly = market["hourly"]
    minute_pair = market["minute_pair"]
    funding = market["funding"]
    data_quality = market["data_quality"]
    health = collector_health_snapshot(market_store)

    if hourly.empty or hourly[hourly["timestamp"] >= FORWARD_OOS_START_UTC].empty:
        summary = {
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "status": "NO_LIVE_FORWARD_DATA",
            "data_quality": data_quality,
            "collector_health": health,
            "runtime_frozen_parameter_check": runtime_check,
            "orders_enabled": False,
            "paper_trading_enabled": False,
        }
        Path(league_root).mkdir(parents=True, exist_ok=True)
        (Path(league_root) / "latest_league_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return summary

    context = _context_frame(hourly, minute_pair)
    common = _common_results(hourly, funding)
    pair_signals, pair_trades = _pair_strategy_results(minute_pair, funding, registry)

    appended_signals = 0
    appended_trades = 0
    for key, result in common.items():
        signal_frame = _standardize_signals(key, result, registry, context)
        trade_frame = _standardize_common_trades(key, result, registry)
        appended_signals += _append_unique(league_store, "signals", signal_frame, ["strategy_name", "strategy_version", "timestamp"])
        appended_trades += _append_unique(league_store, "hypothetical_trades", trade_frame, ["strategy_name", "strategy_version", "entry_time", "exit_time"])

    for key, frame in pair_signals.items():
        if not frame.empty:
            context_merge = context.rename(columns={"decision_timestamp": "timestamp"})[
                ["timestamp", "market_regime", "funding_rate", "open_interest", "basis_bps", "volatility"]
            ].sort_values("timestamp")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            frame = pd.merge_asof(frame.sort_values("timestamp"), context_merge, on="timestamp", direction="backward")
            appended_signals += _append_unique(league_store, "signals", frame, ["strategy_name", "strategy_version", "timestamp"])
    for key, frame in pair_trades.items():
        appended_trades += _append_unique(league_store, "hypothetical_trades", frame, ["strategy_name", "strategy_version", "entry_time", "exit_time"])

    all_signals = league_store.read("signals")
    all_trades = league_store.read("hypothetical_trades")
    latest_timestamp = pd.to_datetime(hourly[hourly["timestamp"] >= FORWARD_OOS_START_UTC]["timestamp"], utc=True).max()

    score_rows = []
    for record in registry.records.values():
        trades = all_trades[
            (all_trades.get("strategy_name") == record.strategy_name)
            & (all_trades.get("strategy_version") == record.strategy_version)
        ].copy() if not all_trades.empty else pd.DataFrame()
        score = ForwardScorecard.from_trades(trades)
        score_rows.append({
            "timestamp": latest_timestamp,
            "strategy_name": record.strategy_name,
            "strategy_version": record.strategy_version,
            "strategy_fingerprint": record.fingerprint,
            **score,
        })
    league_store.append("scorecards", score_rows)

    overlap = SignalOverlapAnalyzer.summarize(all_signals)
    if not overlap.empty:
        overlap["timestamp"] = latest_timestamp
        league_store.append("overlap", overlap.to_dict("records"))

    requests = _active_position_requests(all_signals)
    conflicts = ConflictManager.analyze(requests)
    conflict_row = {
        "timestamp": latest_timestamp,
        "analysis_json": json.dumps(conflicts, ensure_ascii=False, sort_keys=True, default=str),
        "has_conflict": conflicts["has_conflict"],
        "spot_exposure_btc": conflicts["spot_exposure_btc"],
        "perpetual_exposure_btc": conflicts["perpetual_exposure_btc"],
        "net_btc_delta": conflicts["net_btc_delta"],
        "gross_exposure_btc": conflicts["gross_exposure_btc"],
        "margin_usage": conflicts["margin_usage"],
    }
    league_store.append("conflicts", [conflict_row])

    latest_context = hourly[hourly["timestamp"] <= latest_timestamp]
    regime = RegimeDetectorV1().detect(latest_context)
    selector_rows = StrategySelectorFoundation(registry).evaluate(regime, risk_gate_passed=risk_gate_passed)
    league_store.append("selector_decisions", selector_rows)

    summary = {
        "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
        "status": "RECORDED",
        "latest_forward_timestamp": latest_timestamp.isoformat(),
        "new_signal_rows": appended_signals,
        "new_completed_hypothetical_trades": appended_trades,
        "total_signal_rows": int(len(all_signals)),
        "total_completed_hypothetical_trades": int(len(all_trades)),
        "latest_regime": asdict(regime),
        "selector_candidates": [record.key for record in registry.selector_eligible()],
        "selector_decisions": selector_rows,
        "conflict_snapshot": conflicts,
        "data_quality": data_quality,
        "collector_health": health,
        "runtime_frozen_parameter_check": runtime_check,
        "orders_enabled": False,
        "paper_trading_enabled": False,
        "auto_promotion_enabled": False,
    }
    (Path(league_root) / "latest_league_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen Strategy Arena Forward OOS League once; no orders")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--league-root", default=DEFAULT_LEAGUE_ROOT)
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--risk-gate-passed", action="store_true", help="Hypothetical selector eligibility only")
    args = parser.parse_args()
    result = run_forward_oos_league_once(args.store_root, args.league_root, args.symbol, args.risk_gate_passed)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.basis_execution import fetch_funding
from strategy_arena.basis_research import BasisResearchConfig, add_research_features, build_aligned_basis
from strategy_arena.funding_carry_research import backfill_monthly_robust
from strategy_arena.market_store import AppendOnlyMarketStore


@dataclass(frozen=True)
class FundingCarryV1Config:
    initial_equity: float = 100_000.0
    entry_funding_rate: float = 0.0002  # 2 bp/event. Fixed after relationship study; no performance sweep.
    exit_funding_rate: float = 0.0
    spot_taker_fee: float = 0.0010
    perp_taker_fee: float = 0.0005
    spot_half_spread: float = 0.00005
    perp_half_spread: float = 0.00005
    spot_slippage: float = 0.00005
    perp_slippage: float = 0.00005
    perp_leg_delay_bars: int = 1
    perp_leverage: float = 1.0
    maintenance_margin_rate: float = 0.005
    rebalancing_cost_rate: float = 0.0  # Same BTC quantity is held on both legs; scheduled rebalancing is not required in v1.


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if not np.isfinite(value) else value
    return value


def _price_frame(store: AppendOnlyMarketStore, symbol: str) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "close", "symbol", "source"]
    spot = store.read("spot_ohlcv", symbol=symbol, source="binance_vision_spot", columns=cols)
    perp = store.read("futures_ohlcv", symbol=symbol, source="binance_vision_usdm", columns=cols)
    spot = spot.rename(columns={"open": "spot_open", "high": "spot_high", "close": "spot_close"})
    perp = perp.rename(columns={"open": "perp_open", "high": "perp_high", "close": "perp_close"})
    data = spot[["timestamp", "spot_open", "spot_high", "spot_close"]].merge(
        perp[["timestamp", "perp_open", "perp_high", "perp_close"]],
        on="timestamp",
        how="inner",
        validate="one_to_one",
    ).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    return data


def _market_context(data: pd.DataFrame) -> pd.DataFrame:
    aligned = data[["timestamp", "spot_close", "perp_close"]].copy()
    aligned["basis_abs"] = aligned["perp_close"] - aligned["spot_close"]
    aligned["basis_pct"] = aligned["basis_abs"] / aligned["spot_close"]
    aligned["basis_bps"] = aligned["basis_pct"] * 10_000.0
    return add_research_features(aligned, BasisResearchConfig())


def _next_index(timestamps_ns: np.ndarray, ts: pd.Timestamp) -> int:
    target = pd.Timestamp(ts).value
    return int(np.searchsorted(timestamps_ns, target, side="right"))


def _funding_cash(
    funding: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    qty: float,
    data: pd.DataFrame,
    high_threshold: float,
) -> dict:
    events = funding[(funding["timestamp"] > start) & (funding["timestamp"] <= end)].copy()
    if events.empty:
        return {
            "funding_income": 0.0, "funding_cost": 0.0, "funding_net": 0.0,
            "high_funding_income": 0.0, "normal_funding_net": 0.0, "funding_events": 0,
        }
    events = events.reset_index(drop=True)
    if "mark_price" not in events.columns:
        events["mark_price"] = np.nan
    missing = events["mark_price"].isna()
    if missing.any():
        marks = pd.merge_asof(
            events.loc[missing, ["timestamp"]].sort_values("timestamp"),
            data[["timestamp", "perp_close"]].sort_values("timestamp"),
            on="timestamp",
            direction="backward",
            tolerance=pd.Timedelta(minutes=1),
        )["perp_close"].to_numpy()
        events.loc[missing, "mark_price"] = marks
    cash = qty * events["mark_price"].astype(float) * events["funding_rate"].astype(float)
    income = float(cash[cash > 0].sum())
    cost = float((-cash[cash < 0]).sum())
    high_income = float(cash[(events["funding_rate"] >= high_threshold) & (cash > 0)].sum())
    normal_net = float(cash[events["funding_rate"] < high_threshold].sum())
    return {
        "funding_income": income,
        "funding_cost": cost,
        "funding_net": income - cost,
        "high_funding_income": high_income,
        "normal_funding_net": normal_net,
        "funding_events": int(len(events)),
    }


def _transaction_costs(
    qty: float,
    spot_entry: float,
    perp_entry: float,
    spot_exit: float,
    perp_exit: float,
    cfg: FundingCarryV1Config,
) -> dict:
    fee = qty * (
        spot_entry * cfg.spot_taker_fee + spot_exit * cfg.spot_taker_fee
        + perp_entry * cfg.perp_taker_fee + perp_exit * cfg.perp_taker_fee
    )
    spread = qty * (
        spot_entry * cfg.spot_half_spread + spot_exit * cfg.spot_half_spread
        + perp_entry * cfg.perp_half_spread + perp_exit * cfg.perp_half_spread
    )
    slippage = qty * (
        spot_entry * cfg.spot_slippage + spot_exit * cfg.spot_slippage
        + perp_entry * cfg.perp_slippage + perp_exit * cfg.perp_slippage
    )
    turnover = qty * (spot_entry + spot_exit + perp_entry + perp_exit)
    rebalance = turnover * cfg.rebalancing_cost_rate
    return {
        "fee_cost": float(fee),
        "spread_cost": float(spread),
        "slippage_cost": float(slippage),
        "rebalancing_cost": float(rebalance),
        "turnover_notional": float(turnover),
    }


def _entry_context(featured: pd.DataFrame, signal_time: pd.Timestamp) -> dict:
    idx = featured["timestamp"].searchsorted(pd.Timestamp(signal_time), side="right") - 1
    if idx < 0:
        return {"trend_regime": "UNCLASSIFIED", "vol_regime": "UNCLASSIFIED", "basis_signal_bps": np.nan}
    row = featured.iloc[int(idx)]
    return {
        "trend_regime": str(row.get("trend_regime", "UNCLASSIFIED")),
        "vol_regime": str(row.get("vol_regime", "UNCLASSIFIED")),
        "basis_signal_bps": float(row["basis_bps"]),
    }


def simulate(
    data: pd.DataFrame,
    funding: pd.DataFrame,
    cfg: FundingCarryV1Config,
    evaluation_start: pd.Timestamp | str | None = None,
    evaluation_end: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = data.copy().sort_values("timestamp").reset_index(drop=True)
    funding = funding.copy().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    funding["timestamp"] = pd.to_datetime(funding["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    start = pd.Timestamp(evaluation_start) if evaluation_start is not None else data["timestamp"].min()
    end = pd.Timestamp(evaluation_end) if evaluation_end is not None else data["timestamp"].max()
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    data_eval = data[(data["timestamp"] >= start) & (data["timestamp"] <= end)].copy().reset_index(drop=True)
    funding_eval = funding[(funding["timestamp"] >= start) & (funding["timestamp"] <= end)].copy().reset_index(drop=True)
    if data_eval.empty:
        return pd.DataFrame(), pd.DataFrame(), {"number_of_carry_episodes": 0}

    featured = _market_context(data_eval)
    timestamps_ns = data_eval["timestamp"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    equity = cfg.initial_equity
    trades: list[dict] = []
    active: dict | None = None

    for event in funding_eval.itertuples(index=False):
        rate = float(event.funding_rate)
        event_time = pd.Timestamp(event.timestamp)
        if active is None:
            if rate < cfg.entry_funding_rate:
                continue
            spot_i = _next_index(timestamps_ns, event_time)
            perp_i = spot_i + cfg.perp_leg_delay_bars
            if perp_i >= len(data_eval):
                break
            srow = data_eval.iloc[spot_i]
            prow = data_eval.iloc[perp_i]
            one_leg_notional = equity / 2.0
            qty = one_leg_notional / float(srow["spot_open"])
            context = _entry_context(featured, event_time)
            active = {
                "signal_time": event_time,
                "trigger_funding_rate": rate,
                "entry_spot_i": spot_i,
                "entry_perp_i": perp_i,
                "entry_spot_time": srow["timestamp"],
                "entry_perp_time": prow["timestamp"],
                "spot_entry": float(srow["spot_open"]),
                "perp_entry": float(prow["perp_open"]),
                "sync_perp_entry": float(srow["perp_open"]),
                "qty": qty,
                "one_leg_notional": one_leg_notional,
                "equity_before": equity,
                **context,
            }
            continue

        if rate > cfg.exit_funding_rate:
            continue

        exit_signal_time = event_time
        spot_exit_i = _next_index(timestamps_ns, event_time)
        perp_exit_i = spot_exit_i + cfg.perp_leg_delay_bars
        if spot_exit_i >= len(data_eval):
            break
        perp_exit_i = min(perp_exit_i, len(data_eval) - 1)
        active = _close_trade(
            active, data_eval, funding_eval, cfg, spot_exit_i, perp_exit_i,
            exit_signal_time, "funding_non_positive", equity, trades,
        )
        equity = trades[-1]["equity_after"]

    if active is not None:
        final_i = len(data_eval) - 1
        spot_exit_i = max(active["entry_perp_i"], final_i - cfg.perp_leg_delay_bars)
        perp_exit_i = final_i
        _close_trade(
            active, data_eval, funding_eval, cfg, spot_exit_i, perp_exit_i,
            data_eval.iloc[spot_exit_i]["timestamp"], "end_of_test", equity, trades,
        )
        equity = trades[-1]["equity_after"]

    trades_df = pd.DataFrame(trades)
    equity_df = build_hourly_equity_curve(data_eval, funding_eval, trades_df, cfg)
    metrics = summarize(trades_df, equity_df, cfg, start, end)
    return trades_df, equity_df, metrics


def _close_trade(
    active: dict,
    data: pd.DataFrame,
    funding: pd.DataFrame,
    cfg: FundingCarryV1Config,
    spot_exit_i: int,
    perp_exit_i: int,
    exit_signal_time: pd.Timestamp,
    exit_reason: str,
    equity: float,
    trades: list[dict],
):
    # Conservative isolated-margin liquidation check on the perpetual short leg.
    liq_price = active["perp_entry"] * (1.0 + 1.0 / cfg.perp_leverage - cfg.maintenance_margin_rate)
    window = data.iloc[active["entry_perp_i"] : perp_exit_i + 1]
    breached = window["perp_high"] >= liq_price
    liquidation = bool(breached.any())
    if liquidation:
        liq_idx = int(breached[breached].index[0])
        spot_exit_i = liq_idx
        perp_exit_i = liq_idx
        exit_reason = "conservative_perp_liquidation"

    srow = data.iloc[spot_exit_i]
    prow = data.iloc[perp_exit_i]
    spot_exit = float(srow["spot_close"] if liquidation else srow["spot_open"])
    perp_exit = float(liq_price if liquidation else prow["perp_open"])
    qty = active["qty"]

    raw_basis_pnl = qty * ((spot_exit - active["spot_entry"]) + (active["perp_entry"] - perp_exit))
    sync_perp_exit = float(srow["perp_close"] if liquidation else srow["perp_open"])
    synchronous_basis_pnl = qty * (
        (spot_exit - active["spot_entry"]) + (active["sync_perp_entry"] - sync_perp_exit)
    )
    execution_mismatch_pnl = raw_basis_pnl - synchronous_basis_pnl
    funding_cash = _funding_cash(
        funding,
        active["entry_perp_time"],
        prow["timestamp"],
        qty,
        data,
        cfg.entry_funding_rate,
    )
    costs = _transaction_costs(
        qty, active["spot_entry"], active["perp_entry"], spot_exit, perp_exit, cfg,
    )
    gross_economic_pnl = raw_basis_pnl + funding_cash["funding_net"]
    total_cost = costs["fee_cost"] + costs["spread_cost"] + costs["slippage_cost"] + costs["rebalancing_cost"]
    net_pnl = gross_economic_pnl - total_cost
    equity_after = equity + net_pnl
    basis_entry_bps = (active["perp_entry"] - active["spot_entry"]) / active["spot_entry"] * 10_000
    basis_exit_bps = (perp_exit - spot_exit) / spot_exit * 10_000

    trades.append({
        **active,
        "exit_signal_time": pd.Timestamp(exit_signal_time),
        "exit_spot_time": srow["timestamp"],
        "exit_perp_time": prow["timestamp"],
        "exit_reason": exit_reason,
        "liquidation": liquidation,
        "spot_exit": spot_exit,
        "perp_exit": perp_exit,
        "raw_basis_pnl": raw_basis_pnl,
        "synchronous_basis_pnl": synchronous_basis_pnl,
        "execution_mismatch_pnl": execution_mismatch_pnl,
        **funding_cash,
        **costs,
        "gross_economic_pnl": gross_economic_pnl,
        "net_pnl": net_pnl,
        "equity_after": equity_after,
        "basis_entry_bps": basis_entry_bps,
        "basis_exit_bps": basis_exit_bps,
        "basis_convergence_bps": basis_entry_bps - basis_exit_bps,
        "holding_hours": float((pd.Timestamp(prow["timestamp"]) - active["entry_perp_time"]) / pd.Timedelta(hours=1)),
    })
    return None


def build_hourly_equity_curve(data: pd.DataFrame, funding: pd.DataFrame, trades: pd.DataFrame, cfg: FundingCarryV1Config) -> pd.DataFrame:
    hourly = (
        data.set_index("timestamp")[["spot_close", "perp_close"]]
        .resample("1h").last().dropna().reset_index()
    )
    if trades.empty:
        hourly["equity"] = cfg.initial_equity
        return hourly[["timestamp", "equity"]]
    curve = []
    trade_rows = list(trades.to_dict("records"))
    j = 0
    realized_equity = cfg.initial_equity
    for row in hourly.itertuples(index=False):
        ts = pd.Timestamp(row.timestamp)
        while j < len(trade_rows) and ts >= pd.Timestamp(trade_rows[j]["exit_perp_time"]):
            realized_equity = float(trade_rows[j]["equity_after"])
            j += 1
        if j >= len(trade_rows):
            curve.append({"timestamp": ts, "equity": realized_equity})
            continue
        t = trade_rows[j]
        if ts < pd.Timestamp(t["entry_perp_time"]):
            curve.append({"timestamp": ts, "equity": realized_equity})
            continue
        qty = float(t["qty"])
        basis_mtm = qty * ((float(row.spot_close) - float(t["spot_entry"])) + (float(t["perp_entry"]) - float(row.perp_close)))
        f = _funding_cash(funding, pd.Timestamp(t["entry_perp_time"]), ts, qty, data, cfg.entry_funding_rate)
        entry_cost = qty * (
            float(t["spot_entry"]) * (cfg.spot_taker_fee + cfg.spot_half_spread + cfg.spot_slippage)
            + float(t["perp_entry"]) * (cfg.perp_taker_fee + cfg.perp_half_spread + cfg.perp_slippage)
        )
        curve.append({"timestamp": ts, "equity": float(t["equity_before"]) + basis_mtm + f["funding_net"] - entry_cost})
    return pd.DataFrame(curve)


def _profit_factor(pnl: pd.Series) -> float | None:
    if pnl.empty:
        return None
    wins = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses == 0:
        return math.inf if wins > 0 else None
    return wins / losses


def summarize(trades: pd.DataFrame, equity: pd.DataFrame, cfg: FundingCarryV1Config, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    if trades.empty:
        return {"number_of_carry_episodes": 0, "ending_equity": cfg.initial_equity, "net_return": 0.0}
    ending = float(trades.iloc[-1]["equity_after"])
    years = max((end - start).total_seconds() / (365.25 * 86400), 1 / 365.25)
    net_return = ending / cfg.initial_equity - 1.0
    cagr = (ending / cfg.initial_equity) ** (1 / years) - 1 if ending > 0 else -1.0
    peak = equity["equity"].cummax()
    mdd = float((equity["equity"] / peak - 1).min()) if not equity.empty else 0.0
    daily = equity.set_index("timestamp")["equity"].resample("1D").last().ffill().pct_change().dropna()
    std = float(daily.std(ddof=0)) if len(daily) else 0.0
    sharpe = float(daily.mean() / std * math.sqrt(365)) if std > 0 else 0.0
    downside = daily[daily < 0]
    dstd = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = float(daily.mean() / dstd * math.sqrt(365)) if dstd > 0 else 0.0
    transaction_cost = float(trades[["fee_cost", "spread_cost", "slippage_cost", "rebalancing_cost"]].sum().sum())
    pre_cost = float(trades["gross_economic_pnl"].sum())
    turnover = float(trades["turnover_notional"].sum())
    positive = trades[trades["net_pnl"] > 0]["net_pnl"].sort_values(ascending=False)
    total_positive = float(positive.sum())
    concentration = {
        f"top{n}_share_of_positive_pnl": (float(positive.head(n).sum() / total_positive) if total_positive > 0 else None)
        for n in (1, 3, 5)
    }
    top_removed = {}
    ordered = trades["net_pnl"].sort_values(ascending=False)
    for n in (1, 3, 5):
        top_removed[f"net_return_without_top_{n}"] = float((trades["net_pnl"].sum() - ordered.head(n).sum()) / cfg.initial_equity)
    return {
        "number_of_carry_episodes": int(len(trades)),
        "average_holding_hours": float(trades["holding_hours"].mean()),
        "median_holding_hours": float(trades["holding_hours"].median()),
        "funding_only_return": float(trades["funding_net"].sum() / cfg.initial_equity),
        "funding_plus_basis_return": float(pre_cost / cfg.initial_equity),
        "gross_return": float(pre_cost / cfg.initial_equity),
        "net_return": net_return,
        "cagr": cagr,
        "maximum_drawdown": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "profit_factor": _profit_factor(trades["net_pnl"]),
        "win_rate": float((trades["net_pnl"] > 0).mean()),
        "average_gross_pnl": float(trades["gross_economic_pnl"].mean()),
        "average_net_pnl": float(trades["net_pnl"].mean()),
        "funding_income": float(trades["funding_income"].sum()),
        "funding_cost": float(trades["funding_cost"].sum()),
        "funding_net": float(trades["funding_net"].sum()),
        "basis_pnl": float(trades["raw_basis_pnl"].sum()),
        "synchronous_basis_pnl": float(trades["synchronous_basis_pnl"].sum()),
        "execution_mismatch_pnl": float(trades["execution_mismatch_pnl"].sum()),
        "total_fee": float(trades["fee_cost"].sum()),
        "total_spread_cost": float(trades["spread_cost"].sum()),
        "total_slippage": float(trades["slippage_cost"].sum()),
        "rebalancing_cost": float(trades["rebalancing_cost"].sum()),
        "transaction_cost_total": transaction_cost,
        "turnover_notional": turnover,
        "turnover_multiple_initial_equity": turnover / cfg.initial_equity,
        "break_even_cost_bps_per_traded_notional": pre_cost / turnover * 10_000 if turnover else None,
        "break_even_cost_multiplier_vs_modeled": pre_cost / transaction_cost if transaction_cost > 0 else None,
        "ending_equity": ending,
        "liquidations": int(trades["liquidation"].sum()),
        "high_funding_income": float(trades["high_funding_income"].sum()),
        "normal_funding_net": float(trades["normal_funding_net"].sum()),
        **concentration,
        **top_removed,
    }


def _regime_summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if trades.empty:
        return pd.DataFrame()
    for col in ("trend_regime", "vol_regime"):
        for value, g in trades.groupby(col, dropna=False):
            rows.append({
                "dimension": col,
                "regime": str(value),
                "episodes": int(len(g)),
                "gross_economic_pnl": float(g["gross_economic_pnl"].sum()),
                "net_pnl": float(g["net_pnl"].sum()),
                "funding_net": float(g["funding_net"].sum()),
                "basis_pnl": float(g["raw_basis_pnl"].sum()),
                "win_rate": float((g["net_pnl"] > 0).mean()),
                "profit_factor": _profit_factor(g["net_pnl"]),
            })
    rows.extend([
        {
            "dimension": "funding_regime_contribution", "regime": "HIGH_FUNDING",
            "episodes": int(len(trades)), "gross_economic_pnl": np.nan, "net_pnl": np.nan,
            "funding_net": float(trades["high_funding_income"].sum()), "basis_pnl": np.nan,
            "win_rate": np.nan, "profit_factor": np.nan,
        },
        {
            "dimension": "funding_regime_contribution", "regime": "NORMAL_OR_NEGATIVE_FUNDING",
            "episodes": int(len(trades)), "gross_economic_pnl": np.nan, "net_pnl": np.nan,
            "funding_net": float(trades["normal_funding_net"].sum()), "basis_pnl": np.nan,
            "win_rate": np.nan, "profit_factor": np.nan,
        },
    ])
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Funding Carry v1 fixed-rule research simulator; no orders, no live trading")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--store-root", default="data/arena/funding_carry_v1_store")
    p.add_argument("--output-dir", default="data/arena/funding_carry_v1")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    cfg = FundingCarryV1Config()
    store = AppendOnlyMarketStore(args.store_root)
    backfill = backfill_monthly_robust(
        BasisResearchConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month), store, args.workers
    )
    aligned, alignment = build_aligned_basis(store, args.symbol)
    data = _price_frame(store, args.symbol)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    start = pd.Timestamp("2020-01-01T00:00:00Z")
    end = pd.Timestamp("2026-06-30T23:59:00Z")

    trades, equity, overall = simulate(data, funding, cfg, start, end)
    yearly = []
    for year in range(2020, 2027):
        ys = pd.Timestamp(f"{year}-01-01T00:00:00Z")
        ye = min(pd.Timestamp(f"{year}-12-31T23:59:00Z"), end)
        if ys > end:
            continue
        yt, yeq, ym = simulate(data, funding, cfg, ys, ye)
        yearly.append({"year": year, **ym})

    rt, req, recent = simulate(data, funding, cfg, pd.Timestamp("2024-01-01T00:00:00Z"), end)
    regimes = _regime_summary(trades)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "funding_carry_trades.csv", index=False)
    equity.to_csv(out / "funding_carry_equity_hourly.csv", index=False)
    pd.DataFrame(yearly).to_csv(out / "funding_carry_yearly_independent.csv", index=False)
    rt.to_csv(out / "funding_carry_recent_2024_2026_trades.csv", index=False)
    regimes.to_csv(out / "funding_carry_regime_attribution.csv", index=False)

    summary = {
        "strategy": "funding_carry",
        "version": "v1",
        "period": [start.isoformat(), end.isoformat()],
        "config": asdict(cfg),
        "data_alignment": alignment,
        "funding_events": int(len(funding[(funding["timestamp"] >= start) & (funding["timestamp"] <= end)])),
        "overall": {k: _safe(v) for k, v in overall.items()},
        "recent_2024_2026": {k: _safe(v) for k, v in recent.items()},
        "yearly_independent": [{k: _safe(v) for k, v in row.items()} for row in yearly],
        "methodology": {
            "entry": "After a realized funding event >= +2 bp is observed at fundingTime, buy spot at the next available 1m open; short perpetual one minute later.",
            "exit": "After a realized funding event <= 0 is observed, close spot at the next available 1m open and perpetual one minute later; conservative liquidation can force an earlier exit.",
            "lookahead": "The trigger funding payment is never collected retroactively. Only funding events after the perpetual hedge entry and through exit are booked.",
            "position": "50% equity spot notional + equal BTC perpetual short notional backed by remaining 50% equity at 1x leverage.",
            "rebalancing": "Equal BTC quantity is fixed on both legs, so scheduled rebalancing cost is zero. Partial-fill hedge repair cannot be inferred from 1m archive data and remains an unmodeled operational risk.",
            "leg_mismatch": "Spot executes one minute before the perpetual hedge on entry and exit.",
            "parameter_optimization": False,
            "live_trading": False,
            "orders_created": False,
        },
        "risks_not_fully_modeled": [
            "exchange default/custody risk",
            "API outage and order rejection",
            "partial fills and emergency hedge repair",
            "borrow/transfer constraints",
            "intraminute spread and slippage spikes beyond fixed assumptions",
        ],
        "backfill": backfill,
    }
    (out / "funding_carry_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "episodes": overall.get("number_of_carry_episodes"),
        "net_return": overall.get("net_return"),
        "recent_net_return": recent.get("net_return"),
        "output_dir": str(out),
        "live_trading": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

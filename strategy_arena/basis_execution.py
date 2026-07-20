from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategy_arena.basis_research import BasisResearchConfig, add_research_features, backfill_monthly, build_aligned_basis
from strategy_arena.market_store import AppendOnlyMarketStore

FUNDING_MONTHLY = "https://data.binance.vision/data/futures/um/monthly/fundingRate"


@dataclass(frozen=True)
class BasisExecutionConfig:
    initial_equity: float = 100_000.0
    z_window_minutes: int = 30 * 24 * 60
    entry_z: float = 2.0
    reset_z: float = 1.0
    spot_taker_fee: float = 0.0010
    perp_taker_fee: float = 0.0005
    spot_half_spread: float = 0.00005
    perp_half_spread: float = 0.00005
    spot_slippage: float = 0.00005
    perp_slippage: float = 0.00005
    perp_leg_delay_bars: int = 1


@dataclass
class Trade:
    signal_time: pd.Timestamp
    entry_spot_time: pd.Timestamp
    entry_perp_time: pd.Timestamp
    exit_signal_time: pd.Timestamp
    exit_spot_time: pd.Timestamp
    exit_perp_time: pd.Timestamp
    entry_z: float
    exit_z: float
    one_leg_notional: float
    quantity: float
    gross_pnl: float
    synchronous_gross_pnl: float
    execution_mismatch_pnl: float
    fee_cost: float
    spread_cost: float
    slippage_cost: float
    funding_income: float
    funding_cost: float
    funding_net: float
    net_pnl: float
    gross_return_on_pair_capital: float
    net_return_on_pair_capital: float
    basis_entry_bps: float
    basis_exit_bps: float
    basis_convergence_bps: float
    holding_minutes: int


def _month_range(start: str, end: str) -> list[str]:
    return [str(x) for x in pd.period_range(pd.Period(start, freq="M"), pd.Period(end, freq="M"), freq="M")]


def fetch_funding(symbol: str, start_month: str, end_month: str) -> pd.DataFrame:
    frames = []
    for month in _month_range(start_month, end_month):
        url = f"{FUNDING_MONTHLY}/{symbol}/{symbol}-fundingRate-{month}.zip"
        r = requests.get(url, timeout=60)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            csvs = [n for n in zf.namelist() if n.endswith('.csv')]
            if not csvs:
                continue
            with zf.open(csvs[0]) as fh:
                raw = pd.read_csv(fh)
        tcol = next((c for c in ["calc_time", "fundingTime", "funding_time", "timestamp"] if c in raw.columns), None)
        rcol = next((c for c in ["last_funding_rate", "fundingRate", "funding_rate"] if c in raw.columns), None)
        if not tcol or not rcol:
            raise RuntimeError(f"Unexpected funding columns: {list(raw.columns)}")
        numeric = pd.to_numeric(raw[tcol], errors="coerce")
        if numeric.notna().all():
            unit = "us" if numeric.abs().median() > 1e14 else "ms"
            ts = pd.to_datetime(numeric.astype("int64"), unit=unit, utc=True).astype("datetime64[ms, UTC]")
        else:
            ts = pd.to_datetime(raw[tcol], utc=True).astype("datetime64[ms, UTC]")
        frames.append(pd.DataFrame({
            "timestamp": ts,
            "funding_rate": pd.to_numeric(raw[rcol], errors="raise"),
            "mark_price": pd.to_numeric(raw["mark_price"], errors="coerce") if "mark_price" in raw.columns else np.nan,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "funding_rate", "mark_price"])
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _price_frame(store: AppendOnlyMarketStore, symbol: str) -> pd.DataFrame:
    cols = ["timestamp", "open", "close", "symbol", "source"]
    spot = store.read("spot_ohlcv", symbol=symbol, source="binance_vision_spot", columns=cols)
    perp = store.read("futures_ohlcv", symbol=symbol, source="binance_vision_usdm", columns=cols)
    spot = spot.rename(columns={"open": "spot_open", "close": "spot_close"})[["timestamp", "spot_open", "spot_close"]]
    perp = perp.rename(columns={"open": "perp_open", "close": "perp_close"})[["timestamp", "perp_open", "perp_close"]]
    return spot.merge(perp, on="timestamp", how="inner", validate="one_to_one").sort_values("timestamp").reset_index(drop=True)


def _funding_for_trade(funding: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, qty: float, perp_prices: pd.DataFrame) -> tuple[float, float]:
    if funding.empty:
        return 0.0, 0.0
    events = funding[(funding["timestamp"] > start) & (funding["timestamp"] <= end)].copy()
    if events.empty:
        return 0.0, 0.0
    if events["mark_price"].isna().any():
        marks = pd.merge_asof(
            events[["timestamp"]].sort_values("timestamp"),
            perp_prices[["timestamp", "perp_close"]].sort_values("timestamp"),
            on="timestamp", direction="backward"
        )["perp_close"]
        events["mark_price"] = events["mark_price"].fillna(marks)
    # Short perpetual receives positive funding and pays negative funding.
    cash = qty * events["mark_price"] * events["funding_rate"]
    return float(cash[cash > 0].sum()), float((-cash[cash < 0]).sum())


def simulate(data: pd.DataFrame, funding: pd.DataFrame, cfg: BasisExecutionConfig) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    equity = cfg.initial_equity
    trades: list[Trade] = []
    equity_rows = [{"timestamp": data.iloc[0]["timestamp"], "equity": equity}]
    active = False
    pending_entry_idx: int | None = None
    entry_meta: dict | None = None
    i = 0
    delay = cfg.perp_leg_delay_bars

    while i < len(data):
        row = data.iloc[i]
        z = row["basis_zscore"]
        if pd.isna(z):
            i += 1
            continue

        if not active and pending_entry_idx is None and z >= cfg.entry_z:
            pending_entry_idx = i

        if pending_entry_idx is not None:
            spot_i = pending_entry_idx + 1
            perp_i = spot_i + delay
            if perp_i >= len(data):
                break
            srow = data.iloc[spot_i]
            prow = data.iloc[perp_i]
            one_leg_notional = equity / 2.0
            qty = one_leg_notional / float(srow["spot_open"])
            entry_meta = {
                "signal_i": pending_entry_idx,
                "spot_i": spot_i,
                "perp_i": perp_i,
                "signal_time": data.iloc[pending_entry_idx]["timestamp"],
                "entry_z": float(data.iloc[pending_entry_idx]["basis_zscore"]),
                "one_leg_notional": one_leg_notional,
                "qty": qty,
                "spot_raw": float(srow["spot_open"]),
                "perp_raw": float(prow["perp_open"]),
                "perp_sync_raw": float(srow["perp_open"]),
            }
            active = True
            pending_entry_idx = None
            i = perp_i + 1
            continue

        if active and entry_meta is not None and abs(z) <= cfg.reset_z:
            exit_signal_i = i
            exit_spot_i = exit_signal_i + 1
            exit_perp_i = exit_spot_i + delay
            if exit_perp_i >= len(data):
                break
            es = data.iloc[exit_spot_i]
            ep = data.iloc[exit_perp_i]
            qty = entry_meta["qty"]
            n = entry_meta["one_leg_notional"]

            spot_entry_raw = entry_meta["spot_raw"]
            perp_entry_raw = entry_meta["perp_raw"]
            spot_exit_raw = float(es["spot_open"])
            perp_exit_raw = float(ep["perp_open"])
            sync_perp_entry = entry_meta["perp_sync_raw"]
            sync_perp_exit = float(es["perp_open"])

            gross_spot = qty * (spot_exit_raw - spot_entry_raw)
            gross_perp = qty * (perp_entry_raw - perp_exit_raw)
            gross = gross_spot + gross_perp
            sync_gross = qty * ((spot_exit_raw - spot_entry_raw) + (sync_perp_entry - sync_perp_exit))
            mismatch = gross - sync_gross

            spot_entry_fill = spot_entry_raw * (1 + cfg.spot_half_spread + cfg.spot_slippage)
            spot_exit_fill = spot_exit_raw * (1 - cfg.spot_half_spread - cfg.spot_slippage)
            perp_entry_fill = perp_entry_raw * (1 - cfg.perp_half_spread - cfg.perp_slippage)
            perp_exit_fill = perp_exit_raw * (1 + cfg.perp_half_spread + cfg.perp_slippage)
            after_microstructure = qty * ((spot_exit_fill - spot_entry_fill) + (perp_entry_fill - perp_exit_fill))

            spread_cost = qty * (
                spot_entry_raw * cfg.spot_half_spread + spot_exit_raw * cfg.spot_half_spread +
                perp_entry_raw * cfg.perp_half_spread + perp_exit_raw * cfg.perp_half_spread
            )
            slippage_cost = qty * (
                spot_entry_raw * cfg.spot_slippage + spot_exit_raw * cfg.spot_slippage +
                perp_entry_raw * cfg.perp_slippage + perp_exit_raw * cfg.perp_slippage
            )
            fee_cost = qty * (
                spot_entry_fill * cfg.spot_taker_fee + spot_exit_fill * cfg.spot_taker_fee +
                perp_entry_fill * cfg.perp_taker_fee + perp_exit_fill * cfg.perp_taker_fee
            )
            finc, fcost = _funding_for_trade(
                funding,
                data.iloc[entry_meta["perp_i"]]["timestamp"],
                data.iloc[exit_perp_i]["timestamp"],
                qty,
                data,
            )
            funding_net = finc - fcost
            net = after_microstructure - fee_cost + funding_net
            pair_capital = 2 * n
            basis_entry = (perp_entry_raw - spot_entry_raw) / spot_entry_raw * 10_000
            basis_exit = (perp_exit_raw - spot_exit_raw) / spot_exit_raw * 10_000
            holding = int((data.iloc[exit_perp_i]["timestamp"] - data.iloc[entry_meta["perp_i"]]["timestamp"]) / pd.Timedelta(minutes=1))

            trades.append(Trade(
                signal_time=entry_meta["signal_time"],
                entry_spot_time=data.iloc[entry_meta["spot_i"]]["timestamp"],
                entry_perp_time=data.iloc[entry_meta["perp_i"]]["timestamp"],
                exit_signal_time=data.iloc[exit_signal_i]["timestamp"],
                exit_spot_time=data.iloc[exit_spot_i]["timestamp"],
                exit_perp_time=data.iloc[exit_perp_i]["timestamp"],
                entry_z=entry_meta["entry_z"],
                exit_z=float(z),
                one_leg_notional=n,
                quantity=qty,
                gross_pnl=gross,
                synchronous_gross_pnl=sync_gross,
                execution_mismatch_pnl=mismatch,
                fee_cost=fee_cost,
                spread_cost=spread_cost,
                slippage_cost=slippage_cost,
                funding_income=finc,
                funding_cost=fcost,
                funding_net=funding_net,
                net_pnl=net,
                gross_return_on_pair_capital=gross / pair_capital,
                net_return_on_pair_capital=net / pair_capital,
                basis_entry_bps=basis_entry,
                basis_exit_bps=basis_exit,
                basis_convergence_bps=basis_entry - basis_exit,
                holding_minutes=holding,
            ))
            equity += net
            equity_rows.append({"timestamp": data.iloc[exit_perp_i]["timestamp"], "equity": equity})
            active = False
            entry_meta = None
            i = exit_perp_i + 1
            continue
        i += 1

    trades_df = pd.DataFrame([asdict(t) for t in trades])
    equity_df = pd.DataFrame(equity_rows)
    return trades_df, equity_df, summarize(trades_df, equity_df, cfg)


def _profit_factor(values: pd.Series) -> float | None:
    if values.empty:
        return None
    wins = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses == 0:
        return None if wins == 0 else math.inf
    return float(wins / losses)


def _summary_block(df: pd.DataFrame, initial_equity: float) -> dict:
    if df.empty:
        return {"trades": 0}
    gross = float(df["gross_pnl"].sum())
    net = float(df["net_pnl"].sum())
    pair_capital_sum = float((2 * df["one_leg_notional"]).sum())
    one_leg_sum = float(df["one_leg_notional"].sum())
    pre_transaction = float((df["gross_pnl"] + df["funding_net"]).sum())
    return {
        "trades": int(len(df)),
        "gross_return": gross / initial_equity,
        "net_return": net / initial_equity,
        "average_trade_gross_edge_bps_on_pair_capital": float(df["gross_return_on_pair_capital"].mean() * 10_000),
        "average_trade_net_edge_bps_on_pair_capital": float(df["net_return_on_pair_capital"].mean() * 10_000),
        "total_fee": float(df["fee_cost"].sum()),
        "total_spread_cost": float(df["spread_cost"].sum()),
        "total_slippage": float(df["slippage_cost"].sum()),
        "total_execution_mismatch_pnl": float(df["execution_mismatch_pnl"].sum()),
        "total_funding_income": float(df["funding_income"].sum()),
        "total_funding_cost": float(df["funding_cost"].sum()),
        "total_funding_net": float(df["funding_net"].sum()),
        "profit_factor_net": _profit_factor(df["net_pnl"]),
        "mean_holding_minutes": float(df["holding_minutes"].mean()),
        "median_holding_minutes": float(df["holding_minutes"].median()),
        "holding_p25_minutes": float(df["holding_minutes"].quantile(.25)),
        "holding_p75_minutes": float(df["holding_minutes"].quantile(.75)),
        "holding_p95_minutes": float(df["holding_minutes"].quantile(.95)),
        "mean_basis_convergence_bps": float(df["basis_convergence_bps"].mean()),
        "break_even_total_transaction_cost_bps_on_one_leg_notional": pre_transaction / one_leg_sum * 10_000 if one_leg_sum else None,
        "break_even_total_transaction_cost_bps_on_pair_capital": pre_transaction / pair_capital_sum * 10_000 if pair_capital_sum else None,
    }


def summarize(trades: pd.DataFrame, equity: pd.DataFrame, cfg: BasisExecutionConfig) -> dict:
    overall = _summary_block(trades, cfg.initial_equity)
    if not equity.empty:
        peak = equity["equity"].cummax()
        overall["maximum_drawdown"] = float((equity["equity"] / peak - 1).min())
        overall["ending_equity"] = float(equity.iloc[-1]["equity"])
    yearly = []
    if not trades.empty:
        tmp = trades.copy()
        tmp["year"] = pd.to_datetime(tmp["signal_time"], utc=True).dt.year
        for year, group in tmp.groupby("year"):
            row = {"year": int(year), **_summary_block(group, cfg.initial_equity)}
            yearly.append(row)
        recent = tmp[tmp["year"] >= 2024]
    else:
        recent = trades
    return {
        "assumptions": asdict(cfg),
        "overall": overall,
        "yearly": yearly,
        "recent_2024_2026": _summary_block(recent, cfg.initial_equity),
        "conclusion_rule": {
            "A": "Net edge remains clearly positive after modeled costs across recent years",
            "B": "Net edge survives only in selected years/conditions",
            "C": "Statistical convergence exists but modeled net trading edge is negligible or negative",
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Research-only two-leg cash-and-carry basis cost survival test; no orders")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2020-01")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--store-root", default="data/arena/market_store")
    p.add_argument("--output-dir", default="data/arena/basis_execution")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    store = AppendOnlyMarketStore(args.store_root)
    research_cfg = BasisResearchConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month)
    backfill_monthly(research_cfg, store, workers=args.workers)
    aligned, diagnostics = build_aligned_basis(store, args.symbol)
    prices = _price_frame(store, args.symbol)
    data = prices.merge(aligned[["timestamp", "basis_pct", "basis_bps"]], on="timestamp", how="inner", validate="one_to_one")
    data = add_research_features(data, research_cfg)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)

    cfg = BasisExecutionConfig()
    trades, equity, summary = simulate(data, funding, cfg)
    summary["data_diagnostics"] = diagnostics
    summary["funding_events"] = int(len(funding))
    summary["live_trading"] = False
    summary["strategy_parameters_changed"] = False

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "basis_cash_and_carry_trades.csv", index=False)
    equity.to_csv(out / "basis_cash_and_carry_equity.csv", index=False)
    pd.DataFrame(summary["yearly"]).to_csv(out / "basis_cash_and_carry_yearly.csv", index=False)
    (out / "basis_cash_and_carry_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

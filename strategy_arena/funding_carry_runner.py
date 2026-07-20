from __future__ import annotations

import numpy as np
import pandas as pd

import strategy_arena.funding_carry_v1 as carry


def fast_build_hourly_equity_curve(
    data: pd.DataFrame,
    funding: pd.DataFrame,
    trades: pd.DataFrame,
    cfg: carry.FundingCarryV1Config,
) -> pd.DataFrame:
    """Vectorized hourly MTM curve. This changes runtime only, not signals or trade PnL."""
    hourly = (
        data.set_index("timestamp")[["spot_close", "perp_close"]]
        .resample("1h").last().dropna().reset_index()
    )
    if trades.empty:
        hourly["equity"] = cfg.initial_equity
        return hourly[["timestamp", "equity"]]

    equity_values = np.full(len(hourly), cfg.initial_equity, dtype=float)
    hourly_ns = hourly["timestamp"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    realized = cfg.initial_equity
    cursor = 0

    f = funding.copy().sort_values("timestamp").reset_index(drop=True)
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")

    for trade in trades.itertuples(index=False):
        entry = pd.Timestamp(trade.entry_perp_time)
        exit_ = pd.Timestamp(trade.exit_perp_time)
        a = int(np.searchsorted(hourly_ns, entry.value, side="left"))
        b = int(np.searchsorted(hourly_ns, exit_.value, side="left"))
        equity_values[cursor:a] = realized

        if b > a:
            marks = hourly.iloc[a:b]
            qty = float(trade.qty)
            basis_mtm = qty * (
                (marks["spot_close"].to_numpy(dtype=float) - float(trade.spot_entry))
                + (float(trade.perp_entry) - marks["perp_close"].to_numpy(dtype=float))
            )

            events = f[(f["timestamp"] > entry) & (f["timestamp"] <= exit_)].copy().reset_index(drop=True)
            if events.empty:
                funding_mtm = np.zeros(len(marks), dtype=float)
            else:
                if "mark_price" not in events.columns:
                    events["mark_price"] = np.nan
                missing = events["mark_price"].isna()
                if missing.any():
                    fallback = pd.merge_asof(
                        events.loc[missing, ["timestamp"]].sort_values("timestamp"),
                        data[["timestamp", "perp_close"]].sort_values("timestamp"),
                        on="timestamp", direction="backward", tolerance=pd.Timedelta(minutes=1),
                    )["perp_close"].to_numpy()
                    events.loc[missing, "mark_price"] = fallback
                cash = qty * events["mark_price"].to_numpy(dtype=float) * events["funding_rate"].to_numpy(dtype=float)
                prefix = np.concatenate([[0.0], np.cumsum(cash)])
                event_ns = events["timestamp"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
                mark_ns = marks["timestamp"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
                counts = np.searchsorted(event_ns, mark_ns, side="right")
                funding_mtm = prefix[counts]

            entry_cost = qty * (
                float(trade.spot_entry) * (cfg.spot_taker_fee + cfg.spot_half_spread + cfg.spot_slippage)
                + float(trade.perp_entry) * (cfg.perp_taker_fee + cfg.perp_half_spread + cfg.perp_slippage)
            )
            equity_values[a:b] = float(trade.equity_before) + basis_mtm + funding_mtm - entry_cost

        realized = float(trade.equity_after)
        cursor = b

    equity_values[cursor:] = realized
    hourly["equity"] = equity_values
    return hourly[["timestamp", "equity"]]


# The simulator resolves this global at runtime, so signals, fills and PnL logic are unchanged.
carry.build_hourly_equity_curve = fast_build_hourly_equity_curve


if __name__ == "__main__":
    carry.main()

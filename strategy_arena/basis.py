from __future__ import annotations

import pandas as pd

from strategy_arena.market_store import AppendOnlyMarketStore


def _prepare_price(frame: pd.DataFrame, value_name: str, available_name: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[available_name, value_name])
    out = frame[["timestamp", "price"]].copy().sort_values("timestamp")
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    return out.rename(columns={"timestamp": available_name, "price": value_name})


def build_spot_perpetual_basis_dataset(
    store: AppendOnlyMarketStore,
    symbol: str = "BTCUSDT",
    *,
    start=None,
    end=None,
    frequency: str = "1min",
    tolerance: str = "2min",
) -> pd.DataFrame:
    """Build backward-only Spot/Perpetual and Mark/Index basis series.

    spot_perp_basis_* is based on Binance spot last price vs Binance USD-M
    perpetual last price. mark_index_basis_* is kept separate because mark/index
    is an exchange valuation construct and is not interchangeable with spot/perp.
    """
    perp = store.read("perpetual_price", symbol=symbol, source="binance_usdm_rest", start=start, end=end)
    spot = store.read("spot_price", symbol=symbol, source="binance_spot_rest", start=start, end=end)
    mark = store.read("mark_price", symbol=symbol, source="binance_usdm_rest", start=start, end=end)
    index = store.read("index_price", symbol=symbol, source="binance_usdm_rest", start=start, end=end)
    if perp.empty:
        return pd.DataFrame()

    anchor = _prepare_price(perp, "perpetual_price", "timestamp").rename(columns={"timestamp": "perpetual_available_at"})
    anchor = anchor.rename(columns={"perpetual_available_at": "timestamp"})
    anchor["timestamp"] = anchor["timestamp"].dt.floor(frequency)
    anchor = anchor.drop_duplicates("timestamp", keep="last").sort_values("timestamp")

    tol = pd.Timedelta(tolerance)
    for frame, value_name, available_name in [
        (spot, "spot_price", "spot_available_at"),
        (mark, "mark_price", "mark_available_at"),
        (index, "index_price", "index_available_at"),
    ]:
        prepared = _prepare_price(frame, value_name, available_name)
        anchor = pd.merge_asof(
            anchor.sort_values("timestamp"),
            prepared,
            left_on="timestamp",
            right_on=available_name,
            direction="backward",
            tolerance=tol,
            allow_exact_matches=True,
        )

    anchor["spot_perp_basis_abs"] = anchor["perpetual_price"] - anchor["spot_price"]
    anchor["spot_perp_basis_pct"] = anchor["spot_perp_basis_abs"] / anchor["spot_price"]
    anchor["mark_index_basis_abs"] = anchor["mark_price"] - anchor["index_price"]
    anchor["mark_index_basis_pct"] = anchor["mark_index_basis_abs"] / anchor["index_price"]
    anchor["symbol"] = symbol.upper()

    future_columns = ["spot_available_at", "mark_available_at", "index_available_at"]
    for column in future_columns:
        future = anchor[column].notna() & (anchor[column] > anchor["timestamp"])
        if future.any():
            raise RuntimeError(f"Look-ahead detected in basis alignment: {column}")
    return anchor.reset_index(drop=True)

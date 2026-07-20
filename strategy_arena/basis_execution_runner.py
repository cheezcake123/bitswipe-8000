from __future__ import annotations

import io
import zipfile

import pandas as pd

import strategy_arena.basis_research as basis_research
from strategy_arena import basis_execution


def _robust_read_kline_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith('.csv')]
        if not csvs:
            raise RuntimeError('No CSV found in Binance archive')
        with zf.open(csvs[0]) as fh:
            raw = pd.read_csv(fh)
        aliases = {
            'open_time': 'timestamp',
            'quote_asset_volume': 'quote_volume',
            'number_of_trades': 'trades',
            'num_trades': 'trades',
            'taker_buy_base_asset_volume': 'taker_buy_base',
            'taker_buy_volume': 'taker_buy_base',
            'taker_buy_quote_asset_volume': 'taker_buy_quote',
            'taker_buy_quote_volume': 'taker_buy_quote',
        }
        raw = raw.rename(columns={k: v for k, v in aliases.items() if k in raw.columns})
        if 'timestamp' not in raw.columns:
            with zf.open(csvs[0]) as fh:
                raw = pd.read_csv(fh, header=None, names=basis_research.KLINE_COLUMNS)

    required = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time']
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise RuntimeError(f'Missing required kline columns: {missing}; got={list(raw.columns)}')
    for col in basis_research.KLINE_COLUMNS:
        if col not in raw.columns:
            raw[col] = pd.NA
    unit = basis_research._epoch_unit(raw['timestamp'])
    close_unit = basis_research._epoch_unit(raw['close_time'])
    raw['timestamp'] = pd.to_datetime(pd.to_numeric(raw['timestamp'], errors='raise'), unit=unit, utc=True).astype('datetime64[ms, UTC]')
    raw['close_time'] = pd.to_datetime(pd.to_numeric(raw['close_time'], errors='raise'), unit=close_unit, utc=True).astype('datetime64[ms, UTC]')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        raw[col] = pd.to_numeric(raw[col], errors='raise')
    return raw[basis_research.KLINE_COLUMNS].sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)


def _funding_for_trade_fixed(funding: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, qty: float, perp_prices: pd.DataFrame) -> tuple[float, float]:
    if funding.empty:
        return 0.0, 0.0
    events = funding[(funding['timestamp'] > start) & (funding['timestamp'] <= end)].copy().reset_index(drop=True)
    if events.empty:
        return 0.0, 0.0
    if events['mark_price'].isna().any():
        marks = pd.merge_asof(
            events[['timestamp']].sort_values('timestamp'),
            perp_prices[['timestamp', 'perp_close']].sort_values('timestamp'),
            on='timestamp', direction='backward'
        )['perp_close'].reset_index(drop=True)
        events['mark_price'] = events['mark_price'].reset_index(drop=True).fillna(marks)
    cash = qty * events['mark_price'] * events['funding_rate']
    return float(cash[cash > 0].sum()), float((-cash[cash < 0]).sum())


# The imported functions resolve these module globals at runtime.
basis_research._read_kline_zip = _robust_read_kline_zip
basis_execution._funding_for_trade = _funding_for_trade_fixed


if __name__ == '__main__':
    basis_execution.main()

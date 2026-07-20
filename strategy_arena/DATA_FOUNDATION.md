# Strategy Arena Historical Data Foundation

This layer is research-only. It does not contain order placement or live-trading code.

## Append-only layout

```text
data/arena/market_store/
  futures_ohlcv/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  spot_ohlcv/source=binance_spot_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  funding_rate/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  open_interest/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  liquidation/source=binance_usdm_forceorder_ws/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  perpetual_price/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  spot_price/source=binance_spot_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  mark_price/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
  index_price/source=binance_usdm_rest/symbol=BTCUSDT/date=YYYY-MM-DD/part-*.parquet
```

Existing `part-*.parquet` files are never rewritten by `AppendOnlyMarketStore`.
New batches are written atomically as new files. Ordinary datasets de-duplicate on
`timestamp + symbol + source`. Liquidation events additionally use `event_id` so two
distinct forced orders with the same exchange timestamp are not accidentally dropped.

## Collection plan

| Dataset | Source | Stored cadence | Notes |
|---|---|---:|---|
| Futures OHLCV | Binance USD-M REST `/fapi/v1/klines` | 1m | Closed candles only |
| Spot OHLCV | Binance Spot REST `/api/v3/klines` | 1m | Closed candles only |
| Funding Rate | Binance USD-M REST `/fapi/v1/fundingRate` | actual event | Stored at exchange funding timestamp, never forward-created |
| Open Interest | Binance USD-M REST `/fapi/v1/openInterest` | 5m snapshot | Long-term accumulation starts locally after collector deployment |
| Liquidation | Binance USD-M public WebSocket `<symbol>@forceOrder` | event | Public market-wide forced-order snapshots; no account API key |
| Perpetual Price | Binance USD-M REST `/fapi/v1/ticker/price` | 1m snapshot | Last traded perpetual price |
| Spot Price | Binance Spot REST `/api/v3/ticker/price` | 1m snapshot | Last traded spot price |
| Mark Price | Binance USD-M REST `/fapi/v1/premiumIndex` | 1m snapshot | Kept separate from spot price |
| Index Price | Binance USD-M REST `/fapi/v1/premiumIndex` | 1m snapshot | Kept separate from spot price |

The daemon schedule is intentionally simple and reproducible:

- every 60 seconds: closed 1m spot/futures candles and price snapshots
- every 5 minutes: open-interest snapshot
- every 60 minutes: re-fetch recent actual funding events; append-only de-duplication prevents duplicates
- continuously: optional public liquidation WebSocket

Start the research collector manually or under an external process supervisor:

```bash
python -m strategy_arena.collector_daemon --symbol BTCUSDT --with-liquidations
```

Adding the code does **not** automatically start collection on a production server.
A deployment step must explicitly start and supervise the daemon. This is deliberate:
no hidden background process is enabled by merging the research code.

## Spot / perpetual basis definitions

`strategy_arena.basis.build_spot_perpetual_basis_dataset` produces separate fields:

- `spot_perp_basis_abs = perpetual_price - spot_price`
- `spot_perp_basis_pct = spot_perp_basis_abs / spot_price`
- `mark_index_basis_abs = mark_price - index_price`
- `mark_index_basis_pct = mark_index_basis_abs / index_price`

These definitions must not be mixed. Spot/perpetual basis is relevant to cash-and-carry
and relative-value research. Mark/index basis describes the exchange's derivative pricing
construct and is a different signal.

All joins are backward-only with a tolerance. A price published after the research
timestamp cannot be attached to an earlier row.

## Backtest compatibility

The existing Funding Extreme Reversal v1 and OI Momentum v1 strategy parameters are
not changed by this foundation. The existing 28-day archive E2E test remains part of CI,
and new unit tests verify append-only behavior, duplicate handling, basis separation,
and preservation of the v1 parameters.

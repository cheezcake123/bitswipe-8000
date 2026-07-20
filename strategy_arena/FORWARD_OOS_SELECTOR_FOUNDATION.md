# Forward OOS League + Strategy Selector Foundation

This layer is supervision infrastructure only. It creates no exchange orders, does not start Paper Trading, does not auto-promote strategies, and does not allow AI to override frozen strategy signals.

## Immutable Forward OOS boundary

- Historical/backtest cutoff: `2026-06-30T23:59:59.999Z`
- Forward OOS start: `2026-07-01T00:00:00Z`
- Pre-start data may be used only as backward-looking warm-up context.
- Forward League records reject pre-start timestamps.
- Forward scoring accepts post-boundary live Collector sources only: `binance_usdm_rest` for Futures/Funding/OI and `binance_spot_rest` for Spot.
- Binance Vision archive rows remain history/warm-up only even if appended later.

## Frozen Strategy Registry

Registry: `strategy_arena/frozen_strategy_registry.json`

Current policy:

- Funding Carry v1: verdict B, `allowed_for_selector=true`, `allowed_for_paper=false`, frozen +2bp activation.
- Funding Extreme Reversal v1: C, Shadow.
- Basis Mean Reversion v1: C, Shadow.
- Volatility-Adjusted Trend Breakout v1: C, Shadow.
- Volatility Compression Breakout v1: C, Shadow.
- Funding-Aligned Momentum v1: C, Shadow.
- OI Momentum v1: `pending_data`, Shadow/observation only.

All Paper eligibility is false. C strategies are retained rather than deleted. `validate_frozen_runtime_parameters()` compares current dataclass defaults against registry parameters so parameter drift fails validation.

## Flow

```text
Append-only Market Store
  -> Live-source boundary filter
  -> Frozen Strategy adapters / existing implementations
  -> Forward OOS signal recorder
  -> Regime Detector v1
  -> Eligible Strategy Filter
  -> Conflict Manager
  -> Risk Gate
  -> Hypothetical Decision
```

No actual order object is created.

## Regime Detector v1

Backward-only rules:

- Bull: trailing 7-day BTC return >= +5%
- Bear: trailing 7-day BTC return <= -5%
- Sideways: otherwise
- High/Low Volatility: trailing 24h realized volatility versus median of prior 30d values
- High Positive Funding: latest actually observed Funding >= +2bp/event
- Negative Funding: latest actually observed Funding < 0
- Normal Funding: otherwise
- Unknown/Uncertain: insufficient history or missing data

Trend, volatility and Funding tags coexist.

## Funding Carry selector rule

Funding Carry is the only current selector candidate:

```text
latest actually observed Funding >= 0.0002
AND registry verdict == B
AND allowed_for_selector == true
AND Risk Gate passed
=> eligible_hypothetical_only
```

The +2bp threshold is imported from the frozen Funding Carry configuration and is not optimized here.

## Shadow League

Shadow strategies continue to record future frozen-strategy outputs with:

- timestamp
- signal
- confidence
- entry/exit reasons
- market regime
- Funding
- OI
- Basis
- volatility
- frozen strategy fingerprint

Strong future results do not auto-promote a strategy. Re-evaluation and explicit user approval are required.

## Conflict Manager

Detection only; no automatic resolution or resizing.

It calculates:

- requested Spot BTC exposure
- requested Perpetual BTC exposure
- Net BTC delta
- Gross exposure
- same-direction duplicate exposure
- opposite-direction conflict
- aggregate margin usage

This exposes conflicts such as Funding Carry `Spot LONG + Perp SHORT` versus another strategy requesting Perpetual LONG.

## Forward OOS storage

Default root:

```text
data/arena/forward_oos_league/
```

Append-only Parquet batches:

```text
signals/date=YYYY-MM-DD/part-*.parquet
hypothetical_fills/date=YYYY-MM-DD/part-*.parquet
hypothetical_trades/date=YYYY-MM-DD/part-*.parquet
selector_decisions/date=YYYY-MM-DD/part-*.parquet
overlap/date=YYYY-MM-DD/part-*.parquet
scorecards/date=YYYY-MM-DD/part-*.parquet
```

`league_metadata.json` permanently records the Forward OOS boundary and confirms orders, Paper Trading and automatic promotion are disabled.

## Scorecards and overlap

Forward scorecards include sample size alongside hypothetical trade count, Gross/Net return, MDD, Sharpe, Profit Factor, Win Rate, fees, slippage, Funding and average holding time. Small-sample risk-adjusted metrics must not be over-interpreted.

Overlap analysis records same-timestamp overlap, same-direction overlap, opposite-direction conflict and subset-like behavior.

## Promotion gates

`strategy_arena/promotion_gates.json` defines `C -> B`, `B -> A`, and `Paper Eligible` review structure. Numeric thresholds are intentionally unset so they cannot be fitted to existing OOS. Automatic promotion and automatic Paper start are disabled.

## Run one live-source-only snapshot

```bash
python -m strategy_arena.forward_oos_live_runner \
  --store-root data/arena/market_store \
  --league-root data/arena/forward_oos_league
```

The default Risk Gate is false. `--risk-gate-passed` changes only hypothetical eligibility recording; Paper and Live order creation remain impossible in this foundation.

## Collector production verification

Repository access alone cannot prove the production systemd process is running. On the production server:

```bash
sudo systemctl status bitswipe-market-collector --no-pager
journalctl -u bitswipe-market-collector -n 100 --no-pager
find data/arena/market_store -name 'part-*.parquet' | tail
find data/arena/market_store -name 'part-*.parquet' | wc -l
```

If not installed/running:

```bash
cd /path/to/bitswipe-8000
source .venv/bin/activate
pip install -r requirements.txt
python -m strategy_arena.collector_cli --symbol BTCUSDT --store-root data/arena/market_store
bash scripts/install_market_collector_service.sh "$(pwd)"
sudo systemctl start bitswipe-market-collector
sudo systemctl status bitswipe-market-collector --no-pager
journalctl -u bitswipe-market-collector -n 100 --no-pager
```

The packaged service already launches the daemon with `--with-liquidations`. Verify both systemd/journal liveness and new `liquidation` Parquet rows; stored rows alone do not prove the WebSocket is currently connected.

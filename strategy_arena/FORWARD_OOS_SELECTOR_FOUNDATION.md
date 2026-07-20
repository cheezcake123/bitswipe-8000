# Forward OOS League + Strategy Selector Foundation

This layer is supervision infrastructure only. It creates no exchange orders, does not start Paper Trading, does not auto-promote strategies, and does not allow AI to override frozen strategy signals.

## Immutable time boundary

- Historical/backtest cutoff: `2026-06-30T23:59:59.999Z`
- Forward OOS start: `2026-07-01T00:00:00Z`
- Pre-start data may be used only as backward-looking warm-up context.
- Forward League observations, hypothetical fills/trades and selector decisions reject timestamps before the Forward OOS start.
- `league_metadata.json` stores the start and refuses later mutation.
- Forward scoring accepts only live Collector sources after the boundary: Futures/Funding/OI use `binance_usdm_rest`; Spot uses `binance_spot_rest`. Binance Vision archive rows remain warm-up/history only and cannot enter Forward OOS performance.

## Frozen Strategy Registry

The registry is `strategy_arena/frozen_strategy_registry.json`.

Current selector policy:

- `funding_carry:v1`: verdict B, selector allowed, Paper forbidden, activation requires the frozen actual-observed Funding `>= 0.0002` rule plus Risk Gate.
- All C strategies remain registered as Shadow Strategies. They may accumulate Forward OOS observations but cannot receive selector allocation.
- `oi_momentum:v1` remains `pending_data` and cannot be selected.
- All strategies have `allowed_for_paper=false`.

`strategy_arena.forward_oos_runtime.validate_frozen_runtime_parameters()` checks runtime dataclass defaults against registry parameters for the existing strategy implementations. This layer does not copy or alter their trading formulas.

## Data flow

```text
Append-only Market Store
  -> Live-source boundary filter
  -> Frozen Strategy adapters / existing implementations
  -> Forward OOS signal recorder
  -> Regime Detector v1
  -> Eligible Strategy Filter
  -> Conflict Manager
  -> Risk Gate
  -> Hypothetical selector decision
```

No actual order object is created by this flow.

## Regime Detector v1

Backward-only rules:

- Bull: trailing 7-day BTC return >= +5%
- Bear: trailing 7-day BTC return <= -5%
- Sideways: otherwise
- High Volatility / Low Volatility: trailing 24h realized volatility compared with the median of prior 30d realized-volatility observations
- High Positive Funding: latest actually observed Funding >= +2bp/event
- Negative Funding: latest actually observed Funding < 0
- Normal Funding: otherwise
- Unknown / Uncertain: insufficient historical context or missing Funding

Trend, volatility and Funding tags coexist, e.g. `BULL | HIGH_VOLATILITY | HIGH_POSITIVE_FUNDING`.

## Funding Carry selector rule

Funding Carry is the only current selector-eligible strategy.

```text
latest actually observed Funding >= 0.0002
AND registry verdict == B
AND allowed_for_selector == true
AND Risk Gate passed
=> eligible_hypothetical_only
```

The +2bp threshold is imported from the frozen Funding Carry configuration and is not re-optimized here.

## Shadow League

Shadow strategies:

- Funding Extreme Reversal v1
- Basis Mean Reversion v1
- Volatility-Adjusted Trend Breakout v1
- Volatility Compression Breakout v1
- Funding-Aligned Momentum v1
- OI Momentum v1 (`pending_data`)

Their future signals can be recorded with the frozen strategy fingerprint, timestamp, confidence, reasons and contemporaneous market context. Strong future results do not automatically change verdict or eligibility.

## Conflict Manager

The first version detects and records only. It does not resolve or resize strategies.

It calculates:

- requested Spot BTC exposure
- requested Perpetual BTC exposure
- Net BTC delta
- Gross exposure
- same-direction duplicate exposure
- opposite-direction conflicts
- aggregate margin usage

This specifically makes Funding Carry `Spot LONG + Perp SHORT` conflicts visible against future Perpetual LONG/SHORT requests.

## Forward OOS storage

Default root:

```text
data/arena/forward_oos_league/
```

Immutable Parquet batches are written under:

```text
signals/date=YYYY-MM-DD/part-*.parquet
hypothetical_fills/date=YYYY-MM-DD/part-*.parquet
hypothetical_trades/date=YYYY-MM-DD/part-*.parquet
selector_decisions/date=YYYY-MM-DD/part-*.parquet
overlap/date=YYYY-MM-DD/part-*.parquet
scorecards/date=YYYY-MM-DD/part-*.parquet
```

`league_metadata.json` permanently records the Forward OOS boundary.

## Scorecards and overlap

Forward scorecards always include sample size. The framework tracks number of hypothetical trades, Gross/Net return, MDD, Sharpe, Profit Factor, Win Rate, fee, slippage, Funding and average holding time when hypothetical trades exist.

Signal overlap analysis records same-timestamp overlap, same-direction overlap, opposite-direction conflicts and subset-like behavior. These statistics are descriptive only.

## Promotion gates

`strategy_arena/promotion_gates.json` defines the structure for `C -> B`, `B -> A`, and `Paper Eligible` review.

Numeric thresholds are intentionally `null`: they must be chosen prospectively rather than fitted to the already observed OOS. Automatic promotion is disabled and explicit user approval is mandatory.

## Running a live-source-only foundation snapshot

Read-only with respect to exchange/account state:

```bash
python -m strategy_arena.forward_oos_live_runner \
  --store-root data/arena/market_store \
  --league-root data/arena/forward_oos_league
```

The runner accepts post-boundary Futures rows only from `binance_usdm_rest`. Historical archive data can provide pre-boundary warm-up context but cannot be counted as Forward OOS. By default the Risk Gate is false, so no strategy becomes eligible. Supplying `--risk-gate-passed` can only change the recorded hypothetical eligibility decision; Paper and Live order creation remain disabled.

## Collector deployment check

Repository access alone cannot prove the production systemd process is running. On the production server, verify:

```bash
sudo systemctl status bitswipe-market-collector --no-pager
journalctl -u bitswipe-market-collector -n 100 --no-pager
find data/arena/market_store -name 'part-*.parquet' | tail
find data/arena/market_store -name 'part-*.parquet' | wc -l
```

If the service has not been installed:

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

To include public liquidation events, ensure the installed systemd ExecStart uses the daemon's `--with-liquidations` flag; then verify new `liquidation` Parquet rows and the journal. Data presence proves past events were stored, not that the WebSocket is currently connected, so journal/process status is required for liveness.

# Forward OOS League + Strategy Selector Foundation

This layer is supervision infrastructure only. It creates no exchange orders, does not start Paper Trading, does not auto-promote strategies, and does not allow AI to override frozen strategy signals.

## Frozen Forward OOS boundaries

- Historical/backtest cutoff: `2026-06-30T23:59:59.999Z`
- Planned Forward OOS start: `2026-07-01T00:00:00Z`
- Operational Forward OOS start: `2026-07-21T15:00:00Z`
- Audit reason: `collector_not_running_at_planned_boundary`.
- The planned boundary is preserved for provenance and is not used for scoring.
- The operational boundary is frozen because the production Collector first supplied a complete live-collected hour there, before any Forward OOS strategy performance or selector result was recorded.
- Any market data timestamped between the planned and operational boundaries is history/warm-up only. Later REST backfill in that interval can never become Forward OOS performance retroactively.
- Forward League records reject timestamps before the operational boundary.
- Forward scoring accepts post-operational-boundary live Collector sources only: `binance_usdm_rest` for Futures/Funding/OI and `binance_spot_rest` for Spot.
- Binance Vision archive rows remain history/warm-up only even if appended later.
- A legacy `league_metadata.json` containing only the original planned boundary may migrate to the planned/operational structure only if no append-only League records exist yet. Otherwise migration fails closed.

## Frozen Strategy Registry

Registry: `strategy_arena/frozen_strategy_registry.json`

- Funding Carry v1: B, selector allowed, Paper forbidden, frozen +2bp activation.
- Funding Extreme Reversal v1: C, Shadow.
- Basis Mean Reversion v1: C, Shadow.
- Volatility-Adjusted Trend Breakout v1: C, Shadow.
- Volatility Compression Breakout v1: C, Shadow.
- Funding-Aligned Momentum v1: C, Shadow.
- OI Momentum v1: `pending_data`, Shadow/observation only.

All Paper eligibility is false. `validate_frozen_runtime_parameters()` compares current dataclass defaults with the registry and stops the League if parameter drift is detected.

## Forward League flow

```text
Append-only Market Store
  -> Live-source boundary filter
  -> Frozen existing strategy implementations
  -> Forward signals + completed hypothetical trades
  -> Forward scorecards + signal overlap
  -> Regime Detector v1
  -> Eligible Strategy Filter
  -> Conflict Manager
  -> Risk Gate
  -> Hypothetical Selector Decision
```

`forward_oos_league_runner.py` replays the frozen implementations from the frozen operational Forward boundary and appends only new records. Common-engine strategies discard artificial `end_of_test` closures, so a currently open hypothetical position is not falsely booked as a completed trade merely because the League process stops or restarts.

The separate `forward_oos_league_daemon.py` can repeat this research-only run every five minutes. It always calls the selector with the Risk Gate closed and cannot create Paper or Live orders. It is not automatically installed or started by this PR.

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

C and pending-data strategies continue to record future frozen-strategy signals and completed hypothetical trades. Recorded signal context includes timestamp, signal, confidence, entry/exit reason, regime, Funding, OI, Basis, volatility and a frozen-strategy fingerprint.

Strong future results do not auto-promote a strategy. Re-evaluation and explicit user approval are mandatory.

## Conflict Manager

Detection only; no automatic resolution or resizing. It records requested Spot exposure, Perpetual exposure, Net BTC delta, Gross exposure, same-direction duplication, opposite-direction conflicts and normalized margin usage. This makes structures such as Funding Carry `Spot LONG + Perp SHORT` versus a Perpetual LONG request visible before any future Paper allocation.

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
conflicts/date=YYYY-MM-DD/part-*.parquet
overlap/date=YYYY-MM-DD/part-*.parquet
scorecards/date=YYYY-MM-DD/part-*.parquet
```

`league_metadata.json` permanently records both `planned_forward_oos_start_utc` and `operational_forward_oos_start_utc`, the audit reason for the operational freeze, and confirms orders, Paper Trading and automatic promotion are disabled. `latest_league_run.json` records the latest data-quality, Collector-health, regime, selector and conflict snapshot.

Forward scorecards always include sample size alongside hypothetical trade count, Gross/Net return, MDD, Sharpe, Profit Factor, Win Rate, fees, slippage, Funding and average holding time. Small-sample risk-adjusted metrics must not be over-interpreted.

Overlap analysis records same-timestamp overlap, same-direction overlap, opposite-direction conflict and subset-like behavior.

## Promotion gates

`strategy_arena/promotion_gates.json` defines `C -> B`, `B -> A`, and `Paper Eligible` review structure. Numeric thresholds are intentionally unset so they cannot be fitted to existing OOS. Automatic promotion and automatic Paper start are disabled.

## Run the Forward League once

```bash
python -m strategy_arena.forward_oos_league_runner \
  --store-root data/arena/market_store \
  --league-root data/arena/forward_oos_league
```

## Optional research-only League loop

Do not install/start this automatically. After Collector operation is verified, it can be run separately from the web server and collector:

```bash
python -m strategy_arena.forward_oos_league_daemon \
  --store-root data/arena/market_store \
  --league-root data/arena/forward_oos_league \
  --interval-seconds 300
```

This loop records Forward OOS only. It passes `risk_gate_passed=false`; therefore even Funding Carry remains inactive at the selector stage until a future explicit risk-gate/Paper workflow is approved.

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

The packaged Collector service already launches with `--with-liquidations`. Verify both systemd/journal liveness and newly written `liquidation` Parquet rows; stored rows alone do not prove the WebSocket is currently connected.

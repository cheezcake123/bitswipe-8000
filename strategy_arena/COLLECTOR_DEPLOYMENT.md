# BitSwipe Market Collector Deployment

This service is research-only. It collects public market data and never places orders.
It runs separately from the existing BitSwipe FastAPI/web service.

## Why a separate process?

If the web server restarts, the market-data collector should keep running. Likewise, if the
collector fails, it should not take down the website. systemd supervises the collector as an
independent Linux service named `bitswipe-market-collector`.

## Before installation

Assume the repository is checked out on the server and the Python virtual environment already
contains `requirements.txt` dependencies.

```bash
cd /path/to/bitswipe-8000
source .venv/bin/activate
pip install -r requirements.txt
```

Test one read-only collection cycle before installing the service:

```bash
python -m strategy_arena.collector_cli --symbol BTCUSDT --store-root data/arena/market_store
```

Check that Parquet files appear under:

```text
data/arena/market_store/
```

This command only reads public market endpoints. It does not require an API key.

## Install the systemd service

From the repository root:

```bash
bash scripts/install_market_collector_service.sh "$(pwd)"
```

The installer:

1. creates `/etc/bitswipe/market-collector.env` if missing;
2. installs `/etc/systemd/system/bitswipe-market-collector.service`;
3. enables the service for boot;
4. deliberately does **not** start it automatically during installation;
5. does not restart or modify the existing BitSwipe web service.

## Start collection

```bash
sudo systemctl start bitswipe-market-collector
```

Verify:

```bash
sudo systemctl status bitswipe-market-collector --no-pager
journalctl -u bitswipe-market-collector -n 100 --no-pager
```

Watch logs continuously:

```bash
journalctl -u bitswipe-market-collector -f
```

Confirm data is growing:

```bash
find data/arena/market_store -name 'part-*.parquet' | tail
find data/arena/market_store -name 'part-*.parquet' | wc -l
```

After five to ten minutes you should normally see spot/futures candles, price snapshots and
open-interest files. Funding rows only appear when actual funding events have occurred. Public
liquidation rows appear only when Binance emits force-order stream events.

## Stop without affecting the website

```bash
sudo systemctl stop bitswipe-market-collector
```

The BitSwipe FastAPI/web server is a different process and should remain running.

## Restart collector only

```bash
sudo systemctl restart bitswipe-market-collector
```

## Disable automatic start on boot

```bash
sudo systemctl disable --now bitswipe-market-collector
```

## Configuration

Edit:

```text
/etc/bitswipe/market-collector.env
```

Default example:

```text
BITSWIPE_COLLECTOR_SYMBOL=BTCUSDT
BITSWIPE_MARKET_STORE_ROOT=/path/to/bitswipe-8000/data/arena/market_store
```

After editing:

```bash
sudo systemctl restart bitswipe-market-collector
```

## Safety boundary

The collector uses public market-data REST/WebSocket endpoints only. The service definition has
no Binance API key, secret, account endpoint or order endpoint. Live trading remains outside this
process and is not implemented by Strategy Arena.

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone

import pandas as pd
import websockets

from strategy_arena.market_store import AppendOnlyMarketStore

BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"


def normalize_force_order(payload: dict, symbol: str) -> dict:
    order = payload.get("o") or {}
    event_ms = int(order.get("T") or payload.get("E") or 0)
    if event_ms <= 0:
        raise ValueError("forceOrder event has no valid timestamp")
    fingerprint = "|".join(str(order.get(k, "")) for k in ("s", "S", "o", "f", "q", "p", "ap", "X", "l", "z", "T"))
    event_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    qty = float(order.get("z") or order.get("q") or 0)
    price = float(order.get("ap") or order.get("p") or 0)
    return {
        "timestamp": pd.to_datetime(event_ms, unit="ms", utc=True),
        "symbol": str(order.get("s") or symbol).upper(),
        "source": "binance_usdm_forceorder_ws",
        "event_id": event_id,
        "side": str(order.get("S") or ""),
        "order_type": str(order.get("o") or ""),
        "time_in_force": str(order.get("f") or ""),
        "status": str(order.get("X") or ""),
        "original_quantity": float(order.get("q") or 0),
        "executed_quantity": float(order.get("z") or 0),
        "last_filled_quantity": float(order.get("l") or 0),
        "order_price": float(order.get("p") or 0),
        "average_price": float(order.get("ap") or 0),
        "liquidation_notional": qty * price,
        "long_liquidation_usd": qty * price if str(order.get("S")) == "SELL" else 0.0,
        "short_liquidation_usd": qty * price if str(order.get("S")) == "BUY" else 0.0,
        "received_at_utc": pd.Timestamp(datetime.now(timezone.utc)).floor("ms"),
    }


async def collect_liquidations(
    symbol: str = "BTCUSDT",
    store_root: str = "data/arena/market_store",
    flush_size: int = 25,
) -> None:
    """Continuously record public liquidation events. No account keys or orders are used."""
    store = AppendOnlyMarketStore(store_root)
    stream = f"{symbol.lower()}@forceOrder"
    url = f"{BINANCE_FUTURES_WS}/{stream}"
    buffer: list[dict] = []
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as websocket:
                async for raw in websocket:
                    payload = json.loads(raw)
                    buffer.append(normalize_force_order(payload, symbol))
                    if len(buffer) >= flush_size:
                        store.append("liquidation", pd.DataFrame(buffer))
                        buffer.clear()
        except asyncio.CancelledError:
            if buffer:
                store.append("liquidation", pd.DataFrame(buffer))
            raise
        except Exception as exc:
            if buffer:
                store.append("liquidation", pd.DataFrame(buffer))
                buffer.clear()
            print(f"[liquidation-stream] reconnect after {type(exc).__name__}: {exc}")
            await asyncio.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record Binance public forceOrder events; no live trading")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--flush-size", type=int, default=25)
    args = parser.parse_args()
    asyncio.run(collect_liquidations(args.symbol, args.store_root, max(1, args.flush_size)))


if __name__ == "__main__":
    main()

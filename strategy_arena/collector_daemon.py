from __future__ import annotations

import argparse
import asyncio
import time

from strategy_arena.collectors import BinanceMarketCollector
from strategy_arena.liquidation_stream import collect_liquidations
from strategy_arena.market_store import AppendOnlyMarketStore


async def run_rest_schedule(symbol: str, store_root: str) -> None:
    store = AppendOnlyMarketStore(store_root)
    collector = BinanceMarketCollector(store)
    next_minute = next_oi = next_funding = 0.0

    while True:
        now = time.monotonic()
        try:
            if now >= next_minute:
                await asyncio.to_thread(collector.collect_futures_ohlcv, symbol)
                await asyncio.to_thread(collector.collect_spot_ohlcv, symbol)
                await asyncio.to_thread(collector.collect_prices, symbol)
                next_minute = now + 60
            if now >= next_oi:
                await asyncio.to_thread(collector.collect_open_interest, symbol)
                next_oi = now + 300
            if now >= next_funding:
                await asyncio.to_thread(collector.collect_funding_events, symbol)
                next_funding = now + 3600
        except Exception as exc:
            print(f"[market-collector] {type(exc).__name__}: {exc}")
        await asyncio.sleep(1)


async def run(symbol: str, store_root: str, include_liquidations: bool) -> None:
    tasks = [asyncio.create_task(run_rest_schedule(symbol, store_root))]
    if include_liquidations:
        tasks.append(asyncio.create_task(collect_liquidations(symbol, store_root, flush_size=1)))
    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously accumulate public market data; no order execution")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--with-liquidations", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.store_root, args.with_liquidations))


if __name__ == "__main__":
    main()

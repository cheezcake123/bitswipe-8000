from __future__ import annotations

import argparse
import json

from strategy_arena.basis import build_spot_perpetual_basis_dataset
from strategy_arena.collectors import BinanceMarketCollector
from strategy_arena.market_store import AppendOnlyMarketStore


def main() -> None:
    parser = argparse.ArgumentParser(description="BitSwipe append-only market data collector; public data only")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect-once", help="Collect REST market snapshots/candles once")
    collect.add_argument("--symbol", default="BTCUSDT")
    collect.add_argument("--store-root", default="data/arena/market_store")

    inventory = sub.add_parser("inventory", help="Show local append-only store coverage")
    inventory.add_argument("--store-root", default="data/arena/market_store")

    basis = sub.add_parser("build-basis", help="Build backward-aligned spot/perpetual basis dataset")
    basis.add_argument("--symbol", default="BTCUSDT")
    basis.add_argument("--store-root", default="data/arena/market_store")
    basis.add_argument("--output", default="data/arena/datasets/BTCUSDT_basis_1m.parquet")

    args = parser.parse_args()
    store = AppendOnlyMarketStore(args.store_root)

    if args.command == "collect-once":
        result = BinanceMarketCollector(store).collect_once(args.symbol)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "inventory":
        print(store.inventory().to_string(index=False))
        return

    if args.command == "build-basis":
        frame = build_spot_perpetual_basis_dataset(store, args.symbol)
        if frame.empty:
            raise SystemExit("No perpetual price data in store yet")
        frame.to_parquet(args.output, index=False)
        print(json.dumps({"rows": len(frame), "output": args.output, "live_trading": False}, indent=2))


if __name__ == "__main__":
    main()

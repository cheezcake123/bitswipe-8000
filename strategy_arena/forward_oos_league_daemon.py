from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone

from strategy_arena.forward_oos_league_runner import run_forward_oos_league_once


class ForwardOOSLeagueDaemon:
    """Separate research supervision loop. It cannot place Paper or Live orders."""

    def __init__(self, store_root: str, league_root: str, symbol: str = "BTCUSDT", interval_seconds: int = 300):
        self.store_root = store_root
        self.league_root = league_root
        self.symbol = symbol
        self.interval_seconds = max(60, int(interval_seconds))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while self.running:
            started = datetime.now(timezone.utc)
            try:
                result = run_forward_oos_league_once(
                    self.store_root,
                    self.league_root,
                    self.symbol,
                    risk_gate_passed=False,
                )
                print(json.dumps({"timestamp": started.isoformat(), "result": result}, ensure_ascii=False, default=str), flush=True)
            except Exception as exc:
                print(json.dumps({"timestamp": started.isoformat(), "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + self.interval_seconds
            while self.running and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def main() -> None:
    parser = argparse.ArgumentParser(description="Frozen Strategy Arena Forward OOS League daemon; no Paper or Live orders")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--league-root", default="data/arena/forward_oos_league")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    ForwardOOSLeagueDaemon(args.store_root, args.league_root, args.symbol, args.interval_seconds).run_forever()


if __name__ == "__main__":
    main()

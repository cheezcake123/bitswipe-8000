from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notifier.watch_alert import maybe_send_watch_alert
from watchlist_scanner import scan_watchlist


def _parse_csv(value: str | None):
    if not value:
        return None
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the lightweight BitSwipe multi-asset WATCH scanner once."
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Attempt Telegram sends. BITSWIPE_WATCH_ENABLED must also be true.",
    )
    parser.add_argument(
        "--crypto",
        help="Comma-separated crypto symbols. Defaults to BITSWIPE_WATCH_CRYPTO_SYMBOLS.",
    )
    parser.add_argument(
        "--tradfi",
        help="Comma-separated ETF/trad-fi symbols. Defaults to BITSWIPE_WATCH_TRADFI_SYMBOLS.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    dry_run = not args.send
    scan = scan_watchlist(
        crypto_symbols=_parse_csv(args.crypto),
        tradfi_symbols=_parse_csv(args.tradfi),
    )
    alert_results = [
        maybe_send_watch_alert(event, dry_run=dry_run)
        for event in scan.get("events", [])
    ]
    output = {
        **scan,
        "dry_run": dry_run,
        "telegram_attempted": not dry_run,
        "alert_results": alert_results,
        "llm_calls": 0,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

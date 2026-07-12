from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "watchlist_scan_once.py"
INTERVAL = max(60, int(os.getenv("WATCH_SCAN_INTERVAL_SECONDS", "900")))
TIMEOUT = max(30, int(os.getenv("WATCH_SCAN_TIMEOUT_SECONDS", "840")))


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[watchlist-loop] {now} {message}", flush=True)


def main() -> int:
    log(f"started interval={INTERVAL}s timeout={TIMEOUT}s")
    while True:
        started = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(SCAN)],
                cwd=str(ROOT),
                timeout=TIMEOUT,
                check=False,
            )
            if result.returncode == 0:
                log(f"scan ok elapsed={int(time.monotonic() - started)}s")
            else:
                log(f"scan failed returncode={result.returncode}")
        except subprocess.TimeoutExpired:
            log(f"scan failed reason=timeout limit={TIMEOUT}s")
        except Exception as exc:
            log(f"scan failed reason={type(exc).__name__} detail={exc}")
        elapsed = time.monotonic() - started
        delay = max(1, INTERVAL - elapsed)
        log(f"next scan in about {int(delay)}s")
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())

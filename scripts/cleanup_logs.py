#!/usr/bin/env python3

"""

BitSwipe log cleanup utility.



Purpose:

- Keep JSONL validation logs from growing forever.

- Trim large plain log files.

- Optionally delete old backup folders.



Safe by default:

- Without --apply, this script only prints what it would do.

"""



from __future__ import annotations



import argparse

import os

import shutil

import tempfile

import time

from collections import deque

from pathlib import Path





ROOT = Path(__file__).resolve().parents[1]



DEFAULT_JSONL_LIMITS = {

    ROOT / "logs" / "candidates.jsonl": 50_000,

    ROOT / "logs" / "final_verdicts.jsonl": 20_000,

}



DEFAULT_TEXT_LOG_LIMITS_MB = {

    ROOT / "logs" / "watchlist_scan.log": 5,

    ROOT / "logs" / "btc_watch.log": 3,

}





def human_size(num_bytes: int) -> str:

    units = ["B", "KB", "MB", "GB"]

    value = float(num_bytes)

    for unit in units:

        if value < 1024 or unit == units[-1]:

            return f"{value:.1f}{unit}"

        value /= 1024

    return f"{num_bytes}B"





def file_size(path: Path) -> int:

    try:

        return path.stat().st_size

    except FileNotFoundError:

        return 0





def count_lines(path: Path) -> int:

    if not path.exists():

        return 0



    total = 0

    with path.open("rb") as f:

        for chunk in iter(lambda: f.read(1024 * 1024), b""):

            total += chunk.count(b"\n")

    return total





def trim_jsonl_by_lines(path: Path, keep_lines: int, apply: bool) -> dict:

    before_size = file_size(path)



    if not path.exists():

        return {

            "path": str(path.relative_to(ROOT)),

            "exists": False,

            "action": "missing_skip",

        }



    total_lines = count_lines(path)



    if total_lines <= keep_lines:

        return {

            "path": str(path.relative_to(ROOT)),

            "exists": True,

            "action": "keep",

            "lines_before": total_lines,

            "lines_after": total_lines,

            "size_before": human_size(before_size),

            "size_after": human_size(before_size),

        }



    if apply:

        tail = deque(maxlen=keep_lines)



        with path.open("r", encoding="utf-8", errors="replace") as f:

            for line in f:

                tail.append(line)



        path.parent.mkdir(parents=True, exist_ok=True)



        fd, tmp_name = tempfile.mkstemp(

            prefix=path.name + ".",

            suffix=".tmp",

            dir=str(path.parent),

        )



        with os.fdopen(fd, "w", encoding="utf-8") as tmp:

            tmp.writelines(tail)



        shutil.move(tmp_name, path)



    after_size = file_size(path) if apply else before_size



    return {

        "path": str(path.relative_to(ROOT)),

        "exists": True,

        "action": "trim_jsonl_lines" if apply else "would_trim_jsonl_lines",

        "lines_before": total_lines,

        "lines_after": keep_lines,

        "size_before": human_size(before_size),

        "size_after": human_size(after_size),

    }





def trim_text_log_by_size(path: Path, max_mb: int, apply: bool) -> dict:

    before_size = file_size(path)

    max_bytes = max_mb * 1024 * 1024



    if not path.exists():

        return {

            "path": str(path.relative_to(ROOT)),

            "exists": False,

            "action": "missing_skip",

        }



    if before_size <= max_bytes:

        return {

            "path": str(path.relative_to(ROOT)),

            "exists": True,

            "action": "keep",

            "max_size": f"{max_mb}MB",

            "size_before": human_size(before_size),

            "size_after": human_size(before_size),

        }



    if apply:

        with path.open("rb") as f:

            f.seek(max(0, before_size - max_bytes))

            data = f.read()



        # Avoid starting in the middle of a line when possible.

        newline_pos = data.find(b"\n")

        if newline_pos != -1 and newline_pos + 1 < len(data):

            data = data[newline_pos + 1 :]



        fd, tmp_name = tempfile.mkstemp(

            prefix=path.name + ".",

            suffix=".tmp",

            dir=str(path.parent),

        )



        with os.fdopen(fd, "wb") as tmp:

            tmp.write(data)



        shutil.move(tmp_name, path)



    after_size = file_size(path) if apply else before_size



    return {

        "path": str(path.relative_to(ROOT)),

        "exists": True,

        "action": "trim_text_log_size" if apply else "would_trim_text_log_size",

        "max_size": f"{max_mb}MB",

        "size_before": human_size(before_size),

        "size_after": human_size(after_size),

    }





def delete_old_backup_dirs(days: int, apply: bool) -> dict:

    backups_dir = ROOT / "backups"



    if days <= 0:

        return {

            "path": "backups/",

            "action": "backup_delete_disabled",

            "days": days,

            "deleted": 0,

        }



    if not backups_dir.exists():

        return {

            "path": "backups/",

            "action": "missing_skip",

            "days": days,

            "deleted": 0,

        }



    cutoff = time.time() - days * 24 * 60 * 60

    targets = []



    for child in backups_dir.iterdir():

        try:

            if child.is_dir() and child.stat().st_mtime < cutoff:

                targets.append(child)

        except FileNotFoundError:

            continue



    if apply:

        for target in targets:

            shutil.rmtree(target, ignore_errors=True)



    return {

        "path": "backups/",

        "action": "delete_old_backups" if apply else "would_delete_old_backups",

        "days": days,

        "deleted": len(targets),

    }





def print_result(result: dict) -> None:

    parts = [f"- {result.get('path')}", f"action={result.get('action')}"]



    for key in [

        "lines_before",

        "lines_after",

        "size_before",

        "size_after",

        "max_size",

        "days",

        "deleted",

    ]:

        if key in result:

            parts.append(f"{key}={result[key]}")



    print(" | ".join(parts))





def main() -> int:

    parser = argparse.ArgumentParser(description="Clean BitSwipe logs safely.")

    parser.add_argument(

        "--apply",

        action="store_true",

        help="Actually modify files. Without this, only prints what would happen.",

    )

    parser.add_argument(

        "--keep-candidates",

        type=int,

        default=50_000,

        help="Rows to keep in logs/candidates.jsonl.",

    )

    parser.add_argument(

        "--keep-final",

        type=int,

        default=20_000,

        help="Rows to keep in logs/final_verdicts.jsonl.",

    )

    parser.add_argument(

        "--max-watchlist-mb",

        type=int,

        default=5,

        help="Max size for logs/watchlist_scan.log.",

    )

    parser.add_argument(

        "--max-btc-watch-mb",

        type=int,

        default=3,

        help="Max size for logs/btc_watch.log.",

    )

    parser.add_argument(

        "--delete-backups-days",

        type=int,

        default=0,

        help="Delete backup directories older than N days. Default 0 disables deletion.",

    )



    args = parser.parse_args()



    mode = "APPLY" if args.apply else "DRY RUN"

    print(f"[BitSwipe Log Cleanup] mode={mode}")



    jsonl_limits = {

        ROOT / "logs" / "candidates.jsonl": args.keep_candidates,

        ROOT / "logs" / "final_verdicts.jsonl": args.keep_final,

    }



    text_limits = {

        ROOT / "logs" / "watchlist_scan.log": args.max_watchlist_mb,

        ROOT / "logs" / "btc_watch.log": args.max_btc_watch_mb,

    }



    for path, keep_lines in jsonl_limits.items():

        print_result(trim_jsonl_by_lines(path, keep_lines, args.apply))



    for path, max_mb in text_limits.items():

        print_result(trim_text_log_by_size(path, max_mb, args.apply))



    print_result(delete_old_backup_dirs(args.delete_backups_days, args.apply))



    if not args.apply:

        print("")

        print("No files were changed. Run again with --apply to actually clean logs.")



    return 0





if __name__ == "__main__":

    raise SystemExit(main())


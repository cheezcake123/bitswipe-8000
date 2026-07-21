#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_EXTENSIONS = {
    ".ico", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
}
REFERENCE_EXTENSIONS = {".html", ".css", ".js", ".md"}
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
URL_RE = re.compile(r"https?://[^\s\"'<>)] +".replace(" ", ""), re.IGNORECASE)


def is_skipped(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def introduction_commit(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    try:
        output = subprocess.check_output(
            ["git", "log", "--diff-filter=A", "--follow", "--format=%H", "--", rel],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip().splitlines()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"
    return output[-1] if output else "UNKNOWN"


def reference_evidence(asset: Path) -> list[str]:
    rel = asset.relative_to(ROOT).as_posix()
    name = asset.name
    hits: list[str] = []
    for candidate in ROOT.rglob("*"):
        if not candidate.is_file() or is_skipped(candidate):
            continue
        if candidate.suffix.lower() not in REFERENCE_EXTENSIONS:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rel not in text and name not in text:
            continue
        urls = sorted(set(URL_RE.findall(text)))
        where = candidate.relative_to(ROOT).as_posix()
        if urls:
            hits.append(f"{where}: " + ", ".join(urls[:8]))
        else:
            hits.append(where)
    return hits[:12]


def asset_paths() -> list[Path]:
    paths: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or is_skipped(path):
            continue
        if path.suffix.lower() in ASSET_EXTENSIONS:
            paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix().lower())


def main() -> None:
    print("# Static/media asset inventory")
    print()
    print("| Path | Extension | Bytes | SHA-256 | Introduction commit | Reference/URL evidence |")
    print("| --- | --- | ---: | --- | --- | --- |")
    assets = asset_paths()
    if not assets:
        print("| (none) | - | 0 | - | - | - |")
        return

    for path in assets:
        rel = path.relative_to(ROOT).as_posix()
        evidence = reference_evidence(path)
        evidence_text = "<br>".join(item.replace("|", "\\|") for item in evidence) if evidence else "UNKNOWN"
        print(
            f"| {rel} | {path.suffix.lower() or '-'} | {path.stat().st_size} | "
            f"{sha256_file(path)} | {introduction_commit(path)} | {evidence_text} |"
        )

    print()
    print("Notes:")
    print("- UNKNOWN means repository-local evidence was not found by this read-only scan.")
    print("- This script does not make ownership or license conclusions.")
    print("- .env contents are never read and network access is not used.")


if __name__ == "__main__":
    main()

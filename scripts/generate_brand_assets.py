#!/usr/bin/env python3
"""Generate the repository-owned geometric brand/media assets.

This script uses only the Python standard library and no external source files,
fonts, icons, or network resources. The generated mark is a simple geometric
"decision compass / lens" motif designed specifically for this downstream
project.

Usage:
    python3 scripts/generate_brand_assets.py --check
    python3 scripts/generate_brand_assets.py --write
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

BG = (20, 26, 31, 255)
PANEL = (27, 35, 41, 255)
INK = (235, 232, 224, 255)
ACCENT = (111, 183, 167, 255)
MUTED = (86, 102, 110, 255)

EXPECTED_SHA256 = {
    "apple-touch-icon.png": "49cbcf7d12f4c53a23b8bf5ef1dcbdb6d1184c7ba5d6f5265a228ad557c1ac1a",
    "favicon-16x16.png": "5f34c9797021ec7e02e0b021e906d94e463bb22da580a3f7eedcfa3f48f74d48",
    "favicon-32x32.png": "c51c3c374c1c3562cca9bd63723c558031520122c5e831f42e2dddaabb0012fc",
    "favicon.ico": "9e1e5b836ba7ed3e4fe02f5fcfe36c8e98b8f428214241bbe8fc0916c76e0e48",
    "favicon.svg": "ecc066af905cf1e9e52607986d7dc9d3347939bb75daba40319182938f1e3e81",
    "icon-192.png": "af842609060a9bcdd162f8cc9ef1707faa2f5c1cacc063b3a6fea5d34798ac7b",
    "icon-512.png": "75ada9675fff3276ee755fce8c194b894b8e0ff8f5dbb93384e3b253c62a5481",
    "og-image.png": "d2974a1447570dff3a1524e30802249d6be6ab04a21801f340a301572f0ba5a1",
}


def _png_bytes(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for pixel in pixels[y * width : (y + 1) * width]:
            raw.extend(pixel)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = binascii.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _blank(width: int, height: int, color: tuple[int, int, int, int] = BG) -> list[tuple[int, int, int, int]]:
    return [color] * (width * height)


def _fill_rect(pixels, width, height, x0, y0, x1, y1, color) -> None:
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(x1)), min(height, int(y1))
    for y in range(y0, y1):
        base = y * width
        for x in range(x0, x1):
            pixels[base + x] = color


def _fill_circle(pixels, width, height, cx, cy, radius, color) -> None:
    radius_sq = radius * radius
    x0, x1 = max(0, int(cx - radius - 1)), min(width - 1, int(cx + radius + 1))
    y0, y1 = max(0, int(cy - radius - 1)), min(height - 1, int(cy + radius + 1))
    for y in range(y0, y1 + 1):
        dy = (y + 0.5) - cy
        for x in range(x0, x1 + 1):
            dx = (x + 0.5) - cx
            if dx * dx + dy * dy <= radius_sq:
                pixels[y * width + x] = color


def _ring(pixels, width, height, cx, cy, radius, thickness, color) -> None:
    outer_sq = radius * radius
    inner = max(0, radius - thickness)
    inner_sq = inner * inner
    x0, x1 = max(0, int(cx - radius - 1)), min(width - 1, int(cx + radius + 1))
    y0, y1 = max(0, int(cy - radius - 1)), min(height - 1, int(cy + radius + 1))
    for y in range(y0, y1 + 1):
        dy = (y + 0.5) - cy
        for x in range(x0, x1 + 1):
            dx = (x + 0.5) - cx
            dist_sq = dx * dx + dy * dy
            if inner_sq <= dist_sq <= outer_sq:
                pixels[y * width + x] = color


def _line(pixels, width, height, x0, y0, x1, y1, thickness, color) -> None:
    vx, vy = x1 - x0, y1 - y0
    vv = vx * vx + vy * vy
    radius_sq = (thickness / 2) ** 2
    min_x = max(0, int(min(x0, x1) - thickness - 1))
    max_x = min(width - 1, int(max(x0, x1) + thickness + 1))
    min_y = max(0, int(min(y0, y1) - thickness - 1))
    max_y = min(height - 1, int(max(y0, y1) + thickness + 1))

    for y in range(min_y, max_y + 1):
        py = y + 0.5
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            qx, qy = x0, y0
            if vv:
                t = max(0, min(1, ((px - x0) * vx + (py - y0) * vy) / vv))
                qx, qy = x0 + t * vx, y0 + t * vy
            if (px - qx) ** 2 + (py - qy) ** 2 <= radius_sq:
                pixels[y * width + x] = color


def _rounded_rect(pixels, width, height, x0, y0, x1, y1, radius, color) -> None:
    _fill_rect(pixels, width, height, x0 + radius, y0, x1 - radius, y1, color)
    _fill_rect(pixels, width, height, x0, y0 + radius, x1, y1 - radius, color)
    _fill_circle(pixels, width, height, x0 + radius, y0 + radius, radius, color)
    _fill_circle(pixels, width, height, x1 - radius, y0 + radius, radius, color)
    _fill_circle(pixels, width, height, x0 + radius, y1 - radius, radius, color)
    _fill_circle(pixels, width, height, x1 - radius, y1 - radius, radius, color)


def _mark(size: int) -> bytes:
    pixels = _blank(size, size, (0, 0, 0, 0))
    margin = max(1, round(size * 0.06))
    corner = max(2, round(size * 0.20))
    _rounded_rect(pixels, size, size, margin, margin, size - margin, size - margin, corner, BG)

    cx = cy = size / 2
    radius = size * 0.235
    _ring(pixels, size, size, cx, cy, radius, max(1, size * 0.045), INK)
    _line(
        pixels,
        size,
        size,
        cx - radius * 0.72,
        cy + radius * 0.72,
        cx + radius * 0.72,
        cy - radius * 0.72,
        max(1, size * 0.055),
        ACCENT,
    )
    _fill_circle(pixels, size, size, cx, cy, max(1.2, size * 0.055), INK)
    _fill_circle(pixels, size, size, cx + radius * 0.9, cy - radius * 0.9, max(1, size * 0.035), ACCENT)
    return _png_bytes(size, size, pixels)


def build_assets() -> dict[str, bytes]:
    assets = {
        "favicon-16x16.png": _mark(16),
        "favicon-32x32.png": _mark(32),
        "apple-touch-icon.png": _mark(180),
        "icon-192.png": _mark(192),
        "icon-512.png": _mark(512),
    }

    width, height = 1200, 630
    pixels = _blank(width, height, BG)
    _rounded_rect(pixels, width, height, 48, 48, width - 48, height - 48, 42, PANEL)
    cx, cy, radius = 270, height / 2, 118
    _ring(pixels, width, height, cx, cy, radius, 22, INK)
    _line(pixels, width, height, cx - radius * 0.72, cy + radius * 0.72, cx + radius * 0.72, cy - radius * 0.72, 26, ACCENT)
    _fill_circle(pixels, width, height, cx, cy, 26, INK)
    _fill_circle(pixels, width, height, cx + radius * 0.9, cy - radius * 0.9, 15, ACCENT)

    for index, (y, length) in enumerate(((205, 420), (285, 330), (365, 380), (445, 250))):
        _fill_rect(pixels, width, height, 510, y, 510 + length, y + 18, INK if index == 0 else MUTED)
        _fill_circle(pixels, width, height, 980, y + 9, 9, ACCENT if index in (0, 2) else MUTED)
    for x, bar_height in ((555, 54), (620, 82), (685, 42), (750, 96), (815, 64)):
        _fill_rect(pixels, width, height, x, 475 - bar_height, x + 12, 475, ACCENT)

    assets["og-image.png"] = _png_bytes(width, height, pixels)

    ico_images = [assets["favicon-16x16.png"], assets["favicon-32x32.png"]]
    header = struct.pack("<HHH", 0, 1, 2)
    entries = []
    offset = 38
    for size, data in ((16, ico_images[0]), (32, ico_images[1])):
        entries.append(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    assets["favicon.ico"] = header + b"".join(entries) + b"".join(ico_images)

    assets["favicon.svg"] = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Decision compass mark">\n  <rect x="4" y="4" width="56" height="56" rx="13" fill="#141A1F"/>\n  <circle cx="32" cy="32" r="15" fill="none" stroke="#EBE8E0" stroke-width="3"/>\n  <path d="M24.4 39.6 39.6 24.4" fill="none" stroke="#6FB7A7" stroke-width="4" stroke-linecap="round"/>\n  <circle cx="32" cy="32" r="3.2" fill="#EBE8E0"/>\n  <circle cx="45.5" cy="18.5" r="2.2" fill="#6FB7A7"/>\n</svg>\n'''
    return assets


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_assets(assets: dict[str, bytes]) -> int:
    failures = []
    for name, generated in sorted(assets.items()):
        path = STATIC / name
        expected_hash = EXPECTED_SHA256[name]
        generated_hash = digest(generated)
        existing_hash = digest(path.read_bytes()) if path.exists() else "MISSING"
        ok = generated_hash == expected_hash == existing_hash
        print(f"{'OK' if ok else 'FAIL'} {name} sha256={existing_hash}")
        if not ok:
            failures.append(name)
    return 1 if failures else 0


def write_assets(assets: dict[str, bytes]) -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    for name, data in assets.items():
        (STATIC / name).write_bytes(data)
        print(f"WROTE {name} sha256={digest(data)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify checked-in assets match deterministic output")
    mode.add_argument("--write", action="store_true", help="regenerate the checked-in assets")
    args = parser.parse_args()

    assets = build_assets()
    if args.write:
        write_assets(assets)
        return 0
    return check_assets(assets)


if __name__ == "__main__":
    raise SystemExit(main())

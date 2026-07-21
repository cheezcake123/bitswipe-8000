#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
INDEX_HTML = ROOT / "static" / "index.html"
REVIEW_TERMS = ("AGPL", "GPL", "LGPL", "MPL", "EPL", "CDDL", "UNKNOWN")


def normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def direct_requirement_names() -> list[str]:
    names: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            names.append(normalize_name(match.group(1)))
    return names


def license_value(dist: metadata.Distribution) -> str:
    md = dist.metadata
    expression = (md.get("License-Expression") or "").strip()
    if expression:
        return expression
    license_field = (md.get("License") or "").strip().replace("\n", " ")
    if license_field and len(license_field) <= 160:
        return re.sub(r"\s+", " ", license_field)
    classifiers = [
        value.split("::")[-1].strip()
        for value in md.get_all("Classifier", [])
        if value.startswith("License ::")
    ]
    return ", ".join(classifiers) if classifiers else "UNKNOWN"


def license_files(dist: metadata.Distribution) -> str:
    names: list[str] = []
    for item in dist.files or []:
        base = Path(str(item)).name.upper()
        if base.startswith(("LICENSE", "COPYING", "NOTICE")):
            names.append(str(item))
    return ", ".join(sorted(set(names))) or "-"


def installed_distributions() -> dict[str, metadata.Distribution]:
    return {
        normalize_name(dist.metadata.get("Name") or ""): dist
        for dist in metadata.distributions()
        if dist.metadata.get("Name")
    }


def direct_package_inventory() -> list[tuple[str, str, str, str]]:
    installed = installed_distributions()
    rows: list[tuple[str, str, str, str]] = []
    for name in direct_requirement_names():
        dist = installed.get(name)
        if dist is None:
            rows.append((name, "NOT INSTALLED", "UNKNOWN", "-"))
            continue
        display_name = dist.metadata.get("Name") or name
        rows.append((display_name, dist.version, license_value(dist), license_files(dist)))
    return rows


def review_flags() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for dist in installed_distributions().values():
        name = dist.metadata.get("Name") or "UNKNOWN"
        license_name = license_value(dist)
        upper = license_name.upper()
        if any(term in upper for term in REVIEW_TERMS):
            rows.append((name, dist.version, license_name, license_files(dist)))
    return sorted(rows, key=lambda row: row[0].lower())


def browser_resources() -> list[str]:
    if not INDEX_HTML.exists():
        return []
    text = INDEX_HTML.read_text(encoding="utf-8")
    urls = re.findall(
        r'<(?:script|link)\b[^>]*?(?:src|href)="(https?://[^"#]+)"',
        text,
        re.IGNORECASE,
    )
    return sorted(set(urls))


def main() -> None:
    print("# Direct Python dependency inventory")
    print()
    print("| Package | Installed version | License metadata | License/notice files |")
    print("| --- | --- | --- | --- |")
    for name, version, license_name, files in direct_package_inventory():
        safe_license = license_name.replace("|", "\\|")
        safe_files = files.replace("|", "\\|")
        print(f"| {name} | {version} | {safe_license} | {safe_files} |")

    print()
    print("# Installed Python packages needing manual license review")
    print()
    flags = review_flags()
    if not flags:
        print("(none detected by metadata keyword scan)")
    else:
        print("| Package | Installed version | License metadata | License/notice files |")
        print("| --- | --- | --- | --- |")
        for name, version, license_name, files in flags:
            safe_license = license_name.replace("|", "\\|")
            safe_files = files.replace("|", "\\|")
            print(f"| {name} | {version} | {safe_license} | {safe_files} |")

    print()
    print("Note: this keyword scan is an audit aid, not a legal conclusion.")

    print()
    print("# Browser resources referenced by static/index.html")
    print()
    resources = browser_resources()
    if not resources:
        print("(none)")
    else:
        for url in resources:
            print(f"- {url}")


if __name__ == "__main__":
    main()

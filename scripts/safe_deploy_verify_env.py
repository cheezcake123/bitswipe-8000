#!/usr/bin/env python3
"""Read-only, fail-closed feature-flag checks for safe_deploy_verify.sh."""

from __future__ import annotations

import re
import sys
from pathlib import Path


FEATURE_FLAG = "SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED"
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
EXIT_MISSING = 40
EXIT_REJECTED = 41

_TARGET_ASSIGNMENT = re.compile(
    rf"^[ \t]*{re.escape(FEATURE_FLAG)}(?P<before_equals>[ \t]*)=(?P<value>.*)$"
)


def inspect_environment_file_bytes(data: bytes) -> str | None:
    """Return a rejection reason, or None when every target assignment is safe."""
    if b"\0" in data:
        return "NUL 바이트가 있어 EnvironmentFile을 안전하게 해석할 수 없습니다."

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "UTF-8로 해석할 수 없어 EnvironmentFile 검사를 중단합니다."

    for line_number, physical_line in enumerate(text.split("\n"), start=1):
        line = physical_line[:-1] if physical_line.endswith("\r") else physical_line
        match = _TARGET_ASSIGNMENT.fullmatch(line)
        if match is None:
            continue

        if match.group("before_equals"):
            return (
                f"{line_number}행의 {FEATURE_FLAG} 대입문에 모호한 공백이 있습니다."
            )

        raw_value = match.group("value")
        if "'" in raw_value or '"' in raw_value or "\\" in raw_value:
            return (
                f"{line_number}행의 {FEATURE_FLAG} 값에 따옴표 또는 "
                "백슬래시가 있어 안전하게 해석할 수 없습니다."
            )
        if any(
            (ord(character) < 32 and character not in " \t")
            or ord(character) == 127
            for character in raw_value
        ):
            return (
                f"{line_number}행의 {FEATURE_FLAG} 값에 제어 문자가 있어 "
                "안전하게 해석할 수 없습니다."
            )

        if raw_value.strip().lower() in TRUTHY_VALUES:
            return f"{line_number}행에서 {FEATURE_FLAG}가 활성화되어 있습니다."

    return None


def inspect_process_environment_bytes(data: bytes) -> str | None:
    """Parse Linux /proc environ bytes without changing embedded newlines."""
    if not data:
        return None
    if not data.endswith(b"\0"):
        return "프로세스 환경이 NUL 바이트로 끝나지 않아 안전하게 해석할 수 없습니다."

    target_prefix = FEATURE_FLAG.encode("ascii") + b"="
    target_values: list[bytes] = []
    for entry in data[:-1].split(b"\0"):
        if not entry or b"=" not in entry or entry.startswith(b"="):
            return "프로세스 환경에 올바르지 않은 항목이 있습니다."
        if entry.startswith(target_prefix):
            target_values.append(entry[len(target_prefix) :])

    if len(target_values) > 1:
        return f"프로세스 환경에 {FEATURE_FLAG} 항목이 중복되어 있습니다."
    if not target_values:
        return None

    try:
        value = target_values[0].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return f"프로세스의 {FEATURE_FLAG} 값을 UTF-8로 해석할 수 없습니다."

    if value.strip().lower() in TRUTHY_VALUES:
        return f"실행 중인 프로세스에서 {FEATURE_FLAG}가 활성화되어 있습니다."
    return None


def _read_bytes(path_text: str, label: str) -> tuple[bytes | None, int]:
    try:
        return Path(path_text).read_bytes(), 0
    except FileNotFoundError as exc:
        print(f"{label}이(가) 없습니다: {exc}", file=sys.stderr)
        return None, EXIT_MISSING
    except OSError as exc:
        print(f"{label}을(를) 읽기 전용으로 열 수 없습니다: {exc}", file=sys.stderr)
        return None, EXIT_REJECTED


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"environment-file", "process-environment"}:
        print(
            "사용법: safe_deploy_verify_env.py "
            "{environment-file|process-environment} <읽을 파일>",
            file=sys.stderr,
        )
        return 2

    mode, path_text = argv[1:]
    label = "EnvironmentFile" if mode == "environment-file" else "프로세스 환경"
    data, read_status = _read_bytes(path_text, label)
    if data is None:
        return read_status

    if mode == "environment-file":
        rejection = inspect_environment_file_bytes(data)
    else:
        rejection = inspect_process_environment_bytes(data)

    if rejection is not None:
        print(rejection, file=sys.stderr)
        return EXIT_REJECTED
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

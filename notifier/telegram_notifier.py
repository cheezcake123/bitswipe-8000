from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict


TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _load_env_file(path: str = ".env") -> Dict[str, str]:
    env: Dict[str, str] = {}
    source = Path(path)
    if not source.exists():
        return env

    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except Exception:
        return env

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def send_telegram_message(text: str) -> Dict[str, Any]:
    """Send one plain-text Telegram message without exposing credentials."""
    if not isinstance(text, str) or not text.strip():
        return {
            "ok": False,
            "error": "Telegram 메시지 본문이 비어 있습니다.",
            "error_code": "empty_message",
        }
    if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
        return {
            "ok": False,
            "error": "Telegram 메시지는 4096자를 초과할 수 없습니다.",
            "error_code": "message_too_long",
        }

    env = _load_env_file()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")

    if not token:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN이 설정되어 있지 않습니다.",
            "error_code": "missing_token",
        }
    if not chat_id:
        return {
            "ok": False,
            "error": "TELEGRAM_CHAT_ID가 설정되어 있지 않습니다.",
            "error_code": "missing_chat_id",
        }

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
        result = json.loads(body)
        if not isinstance(result, dict):
            return {
                "ok": False,
                "error": "Telegram API 응답 형식이 올바르지 않습니다.",
                "error_code": "invalid_response",
            }
        return result
    except Exception as exc:
        # urllib exceptions can include the request URL. Never return a string
        # that could contain the bot token embedded in that URL.
        return {
            "ok": False,
            "error": "Telegram 전송 중 오류가 발생했습니다.",
            "error_code": "send_failed",
            "error_type": type(exc).__name__,
        }

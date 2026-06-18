from __future__ import annotations

import time

import config as runtime_config


class _MissingSDKStatusError(Exception):
    status_code = 0


try:
    import anthropic
    AnthropicAPIStatusError = anthropic.APIStatusError
except ImportError:
    anthropic = None  # type: ignore
    AnthropicAPIStatusError = _MissingSDKStatusError  # type: ignore

try:
    from openai import OpenAI
    from openai import APIStatusError as OpenAIAPIStatusError
except ImportError:
    OpenAI = None  # type: ignore
    OpenAIAPIStatusError = _MissingSDKStatusError  # type: ignore


def configured_provider() -> str | None:
    if runtime_config.OPENAI_API_KEY:
        return "openai"
    if runtime_config.CLAUDE_API_KEY:
        return "claude"
    return None


def has_llm_provider() -> bool:
    return configured_provider() is not None


def provider_missing_message() -> str:
    return "OPENAI_API_KEY 또는 CLAUDE_API_KEY 미설정"


def default_agent_model(claude_default: str) -> str:
    if runtime_config.OPENAI_API_KEY:
        return runtime_config.OPENAI_MODEL
    return claude_default


def model_for_provider(model: str) -> str:
    """Return a model name that matches the currently configured provider."""
    requested = (model or "").strip()
    provider = configured_provider()
    lowered = requested.lower()

    if provider == "openai" and (not requested or lowered.startswith("claude")):
        return runtime_config.OPENAI_MODEL
    if provider == "claude" and (
        not requested
        or lowered.startswith("gpt-")
        or lowered.startswith("chatgpt-")
        or lowered.startswith(("o1", "o3", "o4", "o5"))
    ):
        return runtime_config.CLAUDE_MODEL
    return requested


def _openai_text(system: str, user: str, model: str, max_tokens: int) -> str:
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 설치되어 있지 않습니다. pip install -r requirements.txt 를 실행하세요.")
    client = OpenAI(
        api_key=runtime_config.OPENAI_API_KEY,
        timeout=runtime_config.LLM_REQUEST_TIMEOUT_SECS,
    )
    response = client.chat.completions.create(
        model=model_for_provider(model),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=max_tokens,
    )
    if not getattr(response, "choices", None):
        raise RuntimeError("OpenAI 응답이 비어 있습니다.")
    content = response.choices[0].message.content or ""
    if not content.strip():
        raise RuntimeError("OpenAI 응답 본문이 비어 있습니다.")
    return content.strip()


def _anthropic_text(system: str, user: str, model: str, max_tokens: int) -> str:
    if anthropic is None:
        raise RuntimeError("anthropic 패키지가 설치되어 있지 않습니다. pip install -r requirements.txt 를 실행하세요.")
    client = anthropic.Anthropic(
        api_key=runtime_config.CLAUDE_API_KEY,
        timeout=runtime_config.LLM_REQUEST_TIMEOUT_SECS,
    )
    msg = client.messages.create(
        model=model_for_provider(model),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not hasattr(msg, "content") or not isinstance(msg.content, list):
        raise RuntimeError(f"API 응답 형식 오류: {type(msg).__name__}: {msg!r:.200}")
    text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
    if not text.strip():
        raise RuntimeError("Claude 응답 본문이 비어 있습니다.")
    return text.strip()


def call_text_llm(
    system: str,
    user: str,
    *,
    model: str,
    max_tokens: int,
    max_retries: int = 3,
    initial_wait: int = 8,
) -> str:
    provider = configured_provider()
    if provider is None:
        raise RuntimeError(provider_missing_message())

    wait = initial_wait
    for attempt in range(max_retries):
        try:
            if provider == "openai":
                return _openai_text(system, user, model, max_tokens)
            return _anthropic_text(system, user, model, max_tokens)
        except (AnthropicAPIStatusError, OpenAIAPIStatusError) as exc:
            status_code = getattr(exc, "status_code", None)
            retryable = status_code in (429, 500, 502, 503, 504, 529)
            if retryable and attempt < max_retries - 1:
                time.sleep(wait)
                wait *= 2
                continue
            raise

    raise RuntimeError("LLM 응답 없음")

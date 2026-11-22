"""Text-only model providers for the built-in chat (Phase 6). One function: ``complete``.

* Anthropic: the official ``anthropic`` SDK (optional ``ai`` extra). Default model ``claude-opus-5-5``
  with effort set explicitly and the server-side refusal fallback where the model supports it.
* OpenAI (Responses API) and Google Gemini (``generateContent``): plain HTTPS through an injectable
  transport, so tests use recorded responses and never reach the network.

Only text leaves the machine: the system prompt, the conversation and the evidence the caller built. Keys
are passed in by the caller (read from the credential store) and never logged or included in errors.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.contracts.chat import ChatProvider

DEFAULT_MODELS = {ChatProvider.ANTHROPIC: "claude-opus-5-5", ChatProvider.OPENAI: "", ChatProvider.GEMINI: ""}
TIMEOUT_SECONDS = 90
MAX_OUTPUT_TOKENS = 16_000
#: Claude models that take the server-side refusal fallback (``fallbacks: "default"``).
_FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
_EFFORT_PREFIXES = ("claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-opus-4-")

#: (url, headers, body, timeout) -> (status, body)
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]
Message = tuple[str, str]  # (role: "user" | "assistant", text)


def _fail(message: str) -> EngineException:
    return EngineException(ErrorCode.INVALID_ARGUMENT, message)


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URLs only
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _fail(f"could not reach the provider ({type(exc).__name__})") from exc


def _http_error(provider: str, status: int, body: bytes, key: str) -> EngineException:
    detail = ""
    try:
        error = json.loads(body).get("error", {})
        detail = str(error.get("message", ""))[:300] if isinstance(error, dict) else ""
    except (ValueError, AttributeError):
        pass
    # Some providers echo the key (or part of it) in error text; never pass it on.
    if key:
        detail = detail.replace(key, "[key]")
        if len(key) > 8:
            detail = detail.replace(key[-8:], "[key]")
    if status in {401, 403}:
        return _fail(f"{provider} rejected the API key ({status})")
    if status == 429:
        return _fail(f"{provider} rate limit or quota reached; try again later")
    if status >= 500:
        return _fail(f"{provider} is having trouble ({status}); try again later")
    return _fail(f"{provider} refused the request ({status}){': ' + detail if detail else ''}")


def _openai(model: str, key: str, system: str, messages: list[Message], transport: Transport) -> str:
    body = {
        "model": model,
        "instructions": system,
        "input": [{"role": role, "content": text} for role, text in messages],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    status, raw = transport(
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json.dumps(body).encode("utf-8"),
        TIMEOUT_SECONDS,
    )
    if status != 200:
        raise _http_error("OpenAI", status, raw, key)
    data = json.loads(raw)
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts = [
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    if not parts:
        raise _fail("OpenAI returned no text")
    return "".join(parts)


def _gemini(model: str, key: str, system: str, messages: list[Message], transport: Transport) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
            for role, text in messages
        ],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    status, raw = transport(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent",
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        json.dumps(body).encode("utf-8"),
        TIMEOUT_SECONDS,
    )
    if status != 200:
        raise _http_error("Gemini", status, raw, key)
    data = json.loads(raw)
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise _fail(f"Gemini declined the request ({blocked})")
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
    text = "".join(part.get("text", "") for part in parts)
    if not text:
        raise _fail("Gemini returned no text")
    return text


def _anthropic_error(exc: Exception, model: str) -> EngineException | None:
    """Map the SDK's typed errors (most specific first) to plain messages; ``None`` if not an SDK error."""
    try:
        import anthropic
    except ImportError:
        return None
    if isinstance(exc, anthropic.AuthenticationError):
        return _fail("Anthropic rejected the API key")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return _fail("this API key may not use that model")
    if isinstance(exc, anthropic.NotFoundError):
        return _fail(f"Anthropic does not know the model {model!r}")
    if isinstance(exc, anthropic.RateLimitError):
        return _fail("Anthropic rate limit reached; try again later")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return _fail(f"Anthropic is having trouble ({exc.status_code}); try again later")
        return _fail(f"Anthropic refused the request ({exc.status_code})")
    if isinstance(exc, anthropic.APIConnectionError):
        return _fail("could not reach Anthropic")
    return None


def _anthropic(
    model: str, key: str, system: str, messages: list[Message], client_factory: Callable[[str], Any] | None
) -> str:
    if client_factory is not None:
        client = client_factory(key)
    else:
        try:
            import anthropic
        except ImportError as exc:
            raise _fail("the Claude provider needs the anthropic SDK (uv sync --extra ai)") from exc
        client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=float(TIMEOUT_SECONDS))
    options: dict[str, Any] = {}
    if model.startswith(_EFFORT_PREFIXES):
        options["output_config"] = {"effort": "high"}
    if model in _FALLBACK_MODELS:
        options.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": role, "content": text} for role, text in messages],
            **options,
        )
    except Exception as exc:
        mapped = _anthropic_error(exc, model)
        if mapped is None:
            raise
        raise mapped from exc
    if response.stop_reason == "refusal":
        raise _fail("Claude declined this request")
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text:
        raise _fail("Claude returned no text")
    return text


def complete(
    provider: ChatProvider,
    model: str,
    key: str,
    system: str,
    messages: list[Message],
    transport: Transport | None = None,
    anthropic_client_factory: Callable[[str], Any] | None = None,
) -> str:
    if not model:
        raise _fail("choose a model for this provider")
    if provider is ChatProvider.ANTHROPIC:
        return _anthropic(model, key, system, messages, anthropic_client_factory)
    send = transport or urllib_transport
    if provider is ChatProvider.OPENAI:
        return _openai(model, key, system, messages, send)
    return _gemini(model, key, system, messages, send)

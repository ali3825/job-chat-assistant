"""Optional LLM provider abstraction (Anthropic, or any OpenAI-compatible chat API) using httpx.

The LLM is only ever used for (a) classifying a message the rules couldn't route and
(b) short free-form career chat. It never executes tools, SQL or network calls itself, and it is
never sent resume text or contact details -- only the user's chat message.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol

import httpx

from ..config import Settings

log = logging.getLogger("app.llm")


class LLMUnavailable(Exception):
    """Raised when the provider is not configured or the call failed/timed out."""


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str, max_tokens: int = 400) -> str: ...


class _Http:
    def __init__(self, timeout: float, retries: int = 1):
        self.timeout, self.retries = timeout, retries

    def post(self, url: str, headers: dict, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(0.5 * 2 ** attempt)
            except (httpx.HTTPStatusError, ValueError) as exc:
                last = exc
                break
        log.warning("LLM call failed: %s", type(last).__name__)
        raise LLMUnavailable(type(last).__name__)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, s: Settings):
        self.key, self.model = s.llm_api_key, s.llm_model
        self.base = (s.llm_base_url or "https://api.anthropic.com").rstrip("/")
        self.http = _Http(s.llm_timeout_seconds)

    def complete(self, system: str, user: str, max_tokens: int = 400) -> str:
        data = self.http.post(
            f"{self.base}/v1/messages",
            {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": self.model, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]},
        )
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
        except (KeyError, TypeError):
            raise LLMUnavailable("bad_response")


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, s: Settings):
        self.key, self.model = s.llm_api_key, s.llm_model
        self.base = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.http = _Http(s.llm_timeout_seconds)

    def complete(self, system: str, user: str, max_tokens: int = 400) -> str:
        data = self.http.post(
            f"{self.base}/chat/completions",
            {"Authorization": f"Bearer {self.key}", "content-type": "application/json"},
            {"model": self.model, "max_tokens": max_tokens,
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMUnavailable("bad_response")


def build_provider(s: Settings) -> LLMProvider | None:
    """Return a provider only when fully configured; otherwise None (fallback mode)."""
    kind = (s.llm_provider or "none").lower()
    if kind == "none" or not s.llm_api_key or not s.llm_model:
        return None
    if kind == "anthropic":
        return AnthropicProvider(s)
    if kind == "openai":
        return OpenAICompatProvider(s)
    log.warning("Unknown LLM_PROVIDER %r; running in fallback mode", kind)
    return None

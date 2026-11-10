"""Optional LLM layer (CEO-approved, off by default: `enable_llm` in config/engine.yaml).

Providers, all free:
  ollama  - local, no key (default when enable_llm is on): OLLAMA_URL, model from settings
  groq    - GTM_GROQ_API_KEY (free tier)
  gemini  - GTM_GEMINI_API_KEY (free tier)

Rules that keep it honest:
  * Every call gets only text the engine already observed; the prompt forbids outside facts.
  * Structured outputs are validated: any extracted phrase must appear verbatim in the
    source text, or it is dropped. Free-text outputs are labelled "llm:" wherever stored.
  * Every use has a deterministic path that runs without the LLM; failures fall back silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class TokenBucket:
    """Async token bucket that paces calls to stay under a tokens-per-minute budget. Reactive
    429-retry recovers *after* a rate-limit; this avoids the rate-limit in the first place, which
    matters when several companies are judged at once and would otherwise burst past the limit."""

    def __init__(self, tokens_per_min: int):
        self.capacity = max(1, tokens_per_min)
        self.refill_per_sec = self.capacity / 60.0
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: int) -> None:
        amount = min(max(amount, 0), self.capacity)
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_sec)
                self.updated = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                await asyncio.sleep((amount - self.tokens) / self.refill_per_sec)


# Groq free tier's binding limit is ~8,000 tokens/min (verified). One shared bucket paces every
# GroqLLM call in the process; override with GTM_GROQ_TOKENS_PER_MIN.
def _groq_tpm() -> int:
    try:
        return int(os.environ.get("GTM_GROQ_TOKENS_PER_MIN", "8000"))
    except ValueError:
        return 8000


_GROQ_BUCKET = TokenBucket(_groq_tpm())


def _estimate_tokens(system: str, user: str, max_tokens: int) -> int:
    """Upper-bound token estimate for pacing: ~4 chars/token of prompt plus the output ceiling."""
    return (len(system) + len(user)) // 4 + max_tokens


async def _post_with_retry(client: httpx.AsyncClient, url: str, *, headers: dict | None = None,
                           json: dict | None = None, retries: int = 3, base_delay: float = 0.6) -> httpx.Response:
    """POST that retries transient failures – HTTP 429 / 5xx and network errors – with
    exponential backoff, honouring a Retry-After header when present. Rate limiting on a free
    tier is the common case, and one dropped call silently loses a verdict (e.g. a company's
    intent judgment), so a few backed-off retries are worth the wait. A non-transient response
    (any other 4xx) is returned immediately for the caller to handle."""
    response: httpx.Response | None = None
    for attempt in range(retries):
        try:
            response = await client.post(url, headers=headers, json=json)
        except httpx.TransportError as exc:  # connect/read/timeout
            if attempt == retries - 1:
                raise
            log.debug("llm post transient error (attempt %d): %s", attempt + 1, exc)
            await asyncio.sleep(base_delay * (2 ** attempt))
            continue
        if (response.status_code == 429 or response.status_code >= 500) and attempt < retries - 1:
            try:
                delay = float(response.headers.get("retry-after", ""))
            except ValueError:
                delay = base_delay * (2 ** attempt)
            log.debug("llm rate-limited/5xx %s (attempt %d), backing off %.1fs",
                      response.status_code, attempt + 1, delay)
            await asyncio.sleep(min(delay, 8.0))
            continue
        return response
    return response  # exhausted; caller's raise_for_status surfaces the final status


class LLM(Protocol):
    name: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str: ...


@dataclass
class OllamaLLM:
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    name: str = "ollama"

    async def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.base_url}/api/chat", json={
                "model": self.model, "stream": False, "options": {"temperature": 0, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            })
            r.raise_for_status()
            return r.json()["message"]["content"]


@dataclass
class GroqLLM:
    api_key: str
    model: str = "openai/gpt-oss-20b"      # verified available on the free tier
    name: str = "groq"

    async def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        await _GROQ_BUCKET.acquire(_estimate_tokens(system, user, max_tokens))
        payload = {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.model.startswith("openai/gpt-oss"):
            # gpt-oss reasoning models default to "medium" reasoning effort, which on longer,
            # constraint-heavy prompts can consume the entire max_tokens budget on chain-of-
            # thought before emitting any content (finish_reason="length", empty message).
            # "low" leaves the budget for the actual answer.
            payload["reasoning_effort"] = "low"
        async with httpx.AsyncClient(timeout=60) as c:
            r = await _post_with_retry(
                c, "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


# Google retires model ids often and free-tier previews return 503 under load, so the
# provider walks a short list instead of failing on the first name.
GEMINI_MODELS = ("gemini-3-flash-preview", "gemini-flash-latest", "gemini-flash-lite-latest")


@dataclass
class GeminiLLM:
    api_key: str
    model: str | None = None
    name: str = "gemini"

    async def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        models = [self.model] if self.model else list(GEMINI_MODELS)
        last: Exception | None = None
        async with httpx.AsyncClient(timeout=90) as c:
            for model in models:
                r = await c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}",
                    json={"systemInstruction": {"parts": [{"text": system}]},
                          "contents": [{"parts": [{"text": user}]}],
                          "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}})
                if r.status_code in (404, 429, 503):      # retired, rate-limited or overloaded
                    last = httpx.HTTPStatusError(f"{model}: {r.status_code}", request=r.request, response=r)
                    continue
                r.raise_for_status()
                cand = (r.json().get("candidates") or [{}])[0]
                text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
                if text.strip():
                    return text
                last = RuntimeError(f"{model}: empty response ({cand.get('finishReason')})")
        raise last or RuntimeError("gemini: no model produced a response")


@dataclass
class FallbackLLM:
    """Tries `primary` first; on a rate-limit/server error (or any exception once primary's own
    retries are exhausted) falls through to `secondary` for that single call. Keeps the primary's
    `name` for logging continuity, but tags the response's provenance via `last_used`."""
    primary: LLM
    secondary: LLM
    name: str = ""
    last_used: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"{self.primary.name}+{self.secondary.name}"
        self.last_used = self.primary.name

    async def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        try:
            result = await self.primary.complete(system, user, max_tokens=max_tokens)
            self.last_used = self.primary.name
            return result
        except Exception as exc:  # noqa: BLE001 - any primary failure falls through to secondary
            log.warning("llm: %s failed (%s), falling back to %s",
                        self.primary.name, type(exc).__name__, self.secondary.name)
            result = await self.secondary.complete(system, user, max_tokens=max_tokens)
            self.last_used = self.secondary.name
            return result


def build_llm(provider: str = "auto", model: str | None = None,
              *, groq_api_key: str | None = None, gemini_api_key: str | None = None) -> LLM | None:
    groq = groq_api_key or os.environ.get("GTM_GROQ_API_KEY")
    gemini = gemini_api_key or os.environ.get("GTM_GEMINI_API_KEY")
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    if provider in ("ollama", "auto"):
        try:
            httpx.get(f"{ollama_url}/api/tags", timeout=2).raise_for_status()
            return OllamaLLM(model=model or "llama3.2", base_url=ollama_url)
        except httpx.HTTPError:
            if provider == "ollama":
                log.warning("llm: ollama not reachable at %s", ollama_url)
                return None
    if provider in ("groq", "auto") and groq:
        # Groq's free-tier 8k tokens/min budget is tight under batch load; when a Gemini key is
        # also configured, fall through to it on rate-limit/failure instead of silently
        # degrading every throttled company to keyword-only. `llm_provider: groq` is set
        # explicitly in config/engine.yaml to skip the local-Ollama probe above, not to forbid
        # this fallback - so "groq" opportunistically becomes "groq, then gemini" when able.
        if gemini:
            return FallbackLLM(GroqLLM(groq, model or "openai/gpt-oss-20b"), GeminiLLM(gemini))
        return GroqLLM(groq, model or "openai/gpt-oss-20b")
    if provider in ("gemini", "auto") and gemini:
        return GeminiLLM(gemini, model)
    log.info("llm: no provider available (no local Ollama, no GTM_GROQ_API_KEY / GTM_GEMINI_API_KEY)")
    return None


# ----------------------------------------------------------------------------- guards

_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str) -> dict | None:
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def grounded(value, source: str) -> bool:
    """A string is grounded when it appears in the source text (case/space-insensitive)."""
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    norm = lambda s: re.sub(r"\s+", " ", s.lower()).strip()  # noqa: E731
    return norm(value) in norm(source)


def keep_grounded(obj: dict, source: str, fields: tuple[str, ...]) -> dict:
    """Drop any field whose value is not verbatim in the source. Never invents."""
    out = {}
    for k in fields:
        v = obj.get(k)
        if grounded(v, source):
            out[k] = v
    return out

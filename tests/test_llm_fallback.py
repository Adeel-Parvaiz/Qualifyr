"""Groq -> Gemini fallback: when Groq's free-tier rate limit is exhausted mid-batch, a single
call falls through to Gemini instead of silently degrading to keyword-only classification.

Offline - the LLM HTTP calls are mocked with respx; no live network."""

import httpx
import pytest
import respx

from gtm_engine.llm.client import FallbackLLM, GeminiLLM, GroqLLM, build_llm

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent"
)


@respx.mock
async def test_fallback_uses_secondary_when_primary_exhausts_retries():
    respx.post(GROQ_URL).mock(return_value=httpx.Response(429))
    respx.post(GEMINI_URL, params={"key": "g"}).mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    )
    llm = FallbackLLM(GroqLLM("k"), GeminiLLM("g"))
    result = await llm.complete("s", "u")
    assert result == "hi"
    assert llm.last_used == "gemini"


@respx.mock
async def test_fallback_stays_on_primary_when_it_succeeds():
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    llm = FallbackLLM(GroqLLM("k"), GeminiLLM("g"))
    result = await llm.complete("s", "u")
    assert result == "ok"
    assert llm.last_used == "groq"


@respx.mock
async def test_fallback_propagates_if_both_fail():
    respx.post(GROQ_URL).mock(return_value=httpx.Response(429))
    respx.post(GEMINI_URL, params={"key": "g"}).mock(return_value=httpx.Response(503))
    llm = FallbackLLM(GroqLLM("k"), GeminiLLM("g"))
    with pytest.raises(Exception):
        await llm.complete("s", "u")


def test_build_llm_auto_picks_fallback_when_both_keys_present(monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no ollama")))
    llm = build_llm("auto", groq_api_key="k", gemini_api_key="g")
    assert isinstance(llm, FallbackLLM)
    assert llm.primary.name == "groq" and llm.secondary.name == "gemini"


def test_build_llm_auto_picks_plain_groq_when_no_gemini_key(monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no ollama")))
    llm = build_llm("auto", groq_api_key="k", gemini_api_key=None)
    assert isinstance(llm, GroqLLM)


def test_build_llm_explicit_groq_also_gets_gemini_fallback():
    # config/engine.yaml pins llm_provider: groq to skip the local-Ollama probe in CI/prod -
    # that must not forbid the Gemini fallback when a Gemini key is configured too.
    llm = build_llm("groq", groq_api_key="k", gemini_api_key="g")
    assert isinstance(llm, FallbackLLM)


def test_build_llm_explicit_groq_without_gemini_key_stays_plain():
    llm = build_llm("groq", groq_api_key="k", gemini_api_key=None)
    assert isinstance(llm, GroqLLM)

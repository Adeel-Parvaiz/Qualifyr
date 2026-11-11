import httpx
import pytest

from gtm_engine.api import main
from gtm_engine.llm.client import GEMINI_MODELS


@pytest.mark.parametrize("first_status", [404, 429, 503])
def test_gemini_probe_uses_pipeline_model_fallbacks(monkeypatch, first_status):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        assert "secret" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "secret"
        return httpx.Response(first_status if len(calls) == 1 else 200)

    monkeypatch.setattr(main.httpx, "post", post)
    ok, message = main._test_key("gemini", "secret")
    assert ok
    assert len(calls) == 2
    assert GEMINI_MODELS[0] in calls[0]
    assert GEMINI_MODELS[1] in message


def test_gemini_invalid_key_stops_without_trying_all_models(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(403)

    monkeypatch.setattr(main.httpx, "post", post)
    ok, message = main._test_key("gemini", "invalid")
    assert not ok
    assert "403" in message
    assert len(calls) == 1

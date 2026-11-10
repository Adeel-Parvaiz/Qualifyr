"""judge_intent: the LLM judges whether a company NEEDS the offer (intent), rather than the
engine matching keywords. Pure/offline: the LLM is faked, so these run anywhere."""

import pytest

from gtm_engine.llm.tasks import judge_intent


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self._reply = reply
        self.last_user = None

    async def complete(self, system, user, *, max_tokens=400):
        self.last_user = user
        return self._reply


OFFER = "Order-management and inventory automation for retailers"
EVIDENCE = "Khaadi runs 50+ retail outlets and a central warehouse; stock is reconciled by hand."


@pytest.mark.asyncio
async def test_no_llm_returns_none_so_keyword_path_is_used():
    assert await judge_intent(None, OFFER, EVIDENCE) is None


@pytest.mark.asyncio
async def test_blank_offer_or_evidence_returns_none():
    assert await judge_intent(FakeLLM('{"buyer": true}'), "", EVIDENCE) is None
    assert await judge_intent(FakeLLM('{"buyer": true}'), OFFER, "   ") is None


@pytest.mark.asyncio
async def test_parses_a_buyer_verdict():
    llm = FakeLLM('{"buyer": true, "confidence": 0.92, "reason": "manages stock across 50+ outlets by hand"}')
    r = await judge_intent(llm, OFFER, EVIDENCE)
    assert r["buyer"] is True and r["confidence"] == 0.92
    assert "stock" in r["reason"] and r["by"] == "llm:fake"


@pytest.mark.asyncio
async def test_parses_a_non_buyer_verdict():
    llm = FakeLLM('{"buyer": false, "confidence": 0.9, "reason": "a web design agency, sells services"}')
    r = await judge_intent(llm, OFFER, "Pixel Studio is a web design and marketing agency.")
    assert r["buyer"] is False and r["confidence"] == 0.9


@pytest.mark.asyncio
async def test_confidence_is_clamped_and_bad_values_default_to_zero():
    assert (await judge_intent(FakeLLM('{"buyer": true, "confidence": 5}'), OFFER, EVIDENCE))["confidence"] == 1.0
    assert (await judge_intent(FakeLLM('{"buyer": true, "confidence": -3}'), OFFER, EVIDENCE))["confidence"] == 0.0
    assert (await judge_intent(FakeLLM('{"buyer": true, "confidence": "high"}'), OFFER, EVIDENCE))["confidence"] == 0.0


@pytest.mark.asyncio
async def test_garbage_or_missing_buyer_field_returns_none():
    assert await judge_intent(FakeLLM("not json at all"), OFFER, EVIDENCE) is None
    assert await judge_intent(FakeLLM('{"confidence": 0.5}'), OFFER, EVIDENCE) is None


@pytest.mark.asyncio
async def test_json_embedded_in_prose_is_recovered():
    llm = FakeLLM('Sure!\n{"buyer": true, "confidence": 0.8, "reason": "needs inventory automation"}\nHope that helps.')
    r = await judge_intent(llm, OFFER, EVIDENCE)
    assert r and r["buyer"] is True and r["confidence"] == 0.8


@pytest.mark.asyncio
async def test_offer_and_evidence_are_actually_sent_to_the_model():
    llm = FakeLLM('{"buyer": true, "confidence": 0.5, "reason": "x"}')
    await judge_intent(llm, OFFER, EVIDENCE)
    assert OFFER in llm.last_user and "Khaadi" in llm.last_user

"""P2: LLM-generated relevance keywords and the strict relevance gate (the Imtiaz fix)."""

import pytest

from gtm_engine.llm.tasks import generate_keywords
from gtm_engine.qualification.relevance import is_relevant, relevant_terms


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply

    async def complete(self, system, user, *, max_tokens=400):
        return self.reply


# --- relevance matching -------------------------------------------------------------------

def test_relevant_terms_word_boundary():
    kws = ["inventory", "point of sale", "erp"]
    assert relevant_terms("Hiring an inventory controller", kws) == ["inventory"]
    assert relevant_terms("We need a point of sale rollout", kws) == ["point of sale"]
    assert relevant_terms("cart and marketing roles", kws) == []      # no substring 'art'/'car'
    assert not is_relevant("Hiring a retail sales manager", kws)      # the Imtiaz case: dropped
    assert relevant_terms("anything", []) == []                       # no keywords -> no match


# --- keyword generation -------------------------------------------------------------------

async def test_generate_keywords_fallback_without_llm():
    kws = await generate_keywords(None, "Inventory and stock management software", ["retail"])
    assert "retail" in kws                       # industries seed the fallback
    assert "inventory" in kws and "stock" in kws  # offer words, minus stopwords like 'software'
    assert "software" not in kws


async def test_generate_keywords_merges_llm_output():
    llm = FakeLLM('["inventory", "warehouse", "point of sale", "erp"]')
    kws = await generate_keywords(llm, "stock software", ["retail"])
    assert {"warehouse", "point of sale", "erp"} <= set(kws)
    assert "stock" in kws                         # deterministic base is kept too


async def test_generate_keywords_tolerates_non_json():
    llm = FakeLLM("inventory, warehouse\npoint of sale")   # model ignored 'JSON array'
    kws = await generate_keywords(llm, "x", [])
    assert "warehouse" in kws and "point of sale" in kws


async def test_generate_keywords_caps_and_lowercases():
    llm = FakeLLM("[" + ", ".join(f'"KW{i}"' for i in range(50)) + "]")
    kws = await generate_keywords(llm, "offer", [], max_keywords=10)
    assert len(kws) == 10 and all(k == k.lower() for k in kws)

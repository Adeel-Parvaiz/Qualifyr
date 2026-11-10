"""AI-writing-pattern checker (CEO content-quality brief, 2026-10-10): a concrete, testable
list of the patterns real AI-text detectors flag - cliche phrases, the "it's not X, it's Y"
construction, exactly-three-item lists, overused "And" sentence starts, formulaic em-dash
use, and unnaturally uniform sentence length. Enforced as a separate post-generation check,
not just another instruction stacked into the prompt. Pure Python, no LLM, always runs."""

from __future__ import annotations

import re
import statistics

from gtm_engine.content.models import PatternViolation

CLICHE_PHRASES: tuple[str, ...] = (
    "in today's fast-paced world", "let's dive in", "unlock the power", "game changer",
    "game-changer", "it's important to note", "in conclusion", "at the end of the day",
    "navigate the landscape", "in this day and age", "buckle up", "picture this",
    "the world of", "look no further", "needless to say", "without further ado",
)

_NOT_X_BUT_Y_RE = re.compile(r"\bit'?s not\b[^.!?]{0,80}\bit'?s\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z']+")
_LIST_ITEM_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+\S")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _check_cliches(text: str) -> list[PatternViolation]:
    low = text.lower()
    return [PatternViolation("cliche_phrase", phrase) for phrase in CLICHE_PHRASES if phrase in low]


def _check_not_x_but_y(text: str) -> list[PatternViolation]:
    if _NOT_X_BUT_Y_RE.search(text):
        return [PatternViolation("not_x_but_y", "\"it's not X, it's Y\" construction")]
    return []


def _check_three_item_lists(text: str) -> list[PatternViolation]:
    run = 0
    violations: list[PatternViolation] = []
    for line in [*text.splitlines(), ""]:
        if _LIST_ITEM_RE.match(line):
            run += 1
        else:
            if run == 3:
                violations.append(PatternViolation("three_item_list", "a list of exactly three items"))
            run = 0
    return violations


def _check_overused_and(text: str) -> list[PatternViolation]:
    sentences = _sentences(text)
    if len(sentences) < 4:
        return []
    starts_with_and = sum(1 for s in sentences if s.lower().startswith("and "))
    if starts_with_and / len(sentences) > 0.15:
        return [PatternViolation(
            "overused_and", f"{starts_with_and}/{len(sentences)} sentences start with 'And'"
        )]
    return []


def _check_em_dash_frequency(text: str) -> list[PatternViolation]:
    words = _WORD_RE.findall(text)
    if not words:
        return []
    spaced = len(re.findall(r"\s—\s", text))
    density = spaced / max(len(words), 1) * 100
    if spaced >= 2 and density > 1.0:
        return [PatternViolation("em_dash_overuse", f"{spaced} space-padded em dashes")]
    return []


def _check_uniform_sentence_length(text: str) -> list[PatternViolation]:
    sentences = _sentences(text)
    lengths = [len(_WORD_RE.findall(s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    if len(lengths) < 5:
        return []
    mean = statistics.mean(lengths)
    stdev = statistics.pstdev(lengths)
    if mean > 0 and (stdev / mean) < 0.25:
        return [PatternViolation(
            "uniform_sentence_length", f"stdev/mean={stdev / mean:.2f} across {len(lengths)} sentences"
        )]
    return []


def check_ai_patterns(text: str) -> list[PatternViolation]:
    if not text or not text.strip():
        return []
    violations: list[PatternViolation] = []
    violations += _check_cliches(text)
    violations += _check_not_x_but_y(text)
    violations += _check_three_item_lists(text)
    violations += _check_overused_and(text)
    violations += _check_em_dash_frequency(text)
    violations += _check_uniform_sentence_length(text)
    return violations

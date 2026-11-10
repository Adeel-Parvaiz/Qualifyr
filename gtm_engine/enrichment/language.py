"""Best-effort page-language detection.

Used only to pick which vendor/negative keyword list to match against and whether to
engage the LLM buyer/vendor fallback for non-English sites. Never blocks the pipeline:
detection failure returns None and the deterministic English-only path still runs."""

from __future__ import annotations

try:
    from langdetect import DetectorFactory, LangDetectException, detect
    DetectorFactory.seed = 0  # deterministic results across runs
except ImportError:  # pragma: no cover - langdetect is a hard dependency; guards test envs
    detect = None
    LangDetectException = Exception


def detect_language(text: str | None) -> str | None:
    """Returns a 2-letter ISO 639-1 language code for the given text, or None if it can't
    be determined (too short, not installed, or genuinely ambiguous)."""
    if not text or detect is None:
        return None
    sample = text[:2000].strip()
    if len(sample) < 20:
        return None
    try:
        return detect(sample)
    except LangDetectException:
        return None

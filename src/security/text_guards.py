"""Output text guards for regulatory drafting."""

from __future__ import annotations

import re
import unicodedata

_SENTENCE_SPLIT = re.compile(r"([^。.!?\n]+[。.!?]?)")
_ID_SAFE = re.compile(r"[^0-9A-Za-z_-]+")


def normalize_for_guard(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def sanitize_identifier(value: object) -> str:
    return _ID_SAFE.sub("_", str(value)).strip("_")


def guard_assertions(text: str, forbidden_terms: list[str]) -> tuple[str, bool, list[str]]:
    """Replace unsupported assertion sentences.

    Returns (text, blocked, matched_terms). Blocks when every non-empty sentence
    is an assertion sentence, because sanitizing would erase the whole output.
    """

    if not text:
        return text, False, []
    normalized_terms = [normalize_for_guard(term) for term in forbidden_terms if term]
    parts = [p for p in _SENTENCE_SPLIT.findall(text) if p]
    if not parts:
        parts = [text]
    replaced: list[str] = []
    matched: list[str] = []
    assertion_count = 0
    for sentence in parts:
        norm = normalize_for_guard(sentence)
        hit = [raw for term, raw in zip(normalized_terms, forbidden_terms) if term and term in norm]
        if hit:
            assertion_count += 1
            matched.extend(hit)
            replaced.append("提示された根拠に基づくデータが得られた。")
        else:
            replaced.append(sentence)
    return "".join(replaced), assertion_count == len(parts), sorted(set(matched))

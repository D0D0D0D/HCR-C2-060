"""Clinical PHI detection and masking."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from shared.security.pii_detector import detect_pii

_CASE_PATTERNS = [
    re.compile(r"症例\s*(?:番号|No\.?|ID)?\s*[:：]?\s*[A-Za-z0-9_-]{2,}", re.IGNORECASE),
    re.compile(r"(?:患者|被験者|症例)\s*(?:ID|識別子|番号)\s*[:：]?\s*[A-Za-z0-9_-]{2,}", re.IGNORECASE),
    re.compile(r"\b(?:patient|subject)\s*(?:id|no\.?)?\s*[:：]?\s*[A-Za-z0-9_-]{2,}", re.IGNORECASE),
    re.compile(r"\b[A-Z]\.[A-Z]\.(?:さん|氏)?\b"),
    re.compile(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\b"),
]

_ROLE_WORDS = r"(?:患者|被験者|症例|職長|作業員|担当者)"
_CLINICAL_TERMS = (
    r"(?:肝機能障害|投与後|死亡|骨折|回復|改善|来院|入院|退院|発熱|"
    r"有害事象|副作用|疼痛|悪心|嘔吐|頭痛|転倒|治療|投与|検査|評価)"
)

_JP_NAME_PATTERNS = [
    re.compile(
        rf"{_ROLE_WORDS}(?:の|[\s：:])?(?!ID\b|{_CLINICAL_TERMS})"
        r"(?:[\u4e00-\u9fff々〆ヶ]{2,10}|[\u30a1-\u30ffー]{4,20}|[A-Za-z][A-Za-z ._-]{2,30})"
        r"(?=[がはもをに、。\s]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:田中|佐藤|鈴木|高橋|伊藤|山本|渡辺|中村|小林|加藤)\s*[\u4e00-\u9fff]{1,4}(?=[がはをに、。\s])"),
]


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_iter_strings(item))
        return out
    return []


def phi_findings(value: Any, include_sdk: bool = True) -> list[dict]:
    """Return PHI findings without storing matched text."""

    findings: list[dict] = []
    for text in _iter_strings(value):
        normalized = unicodedata.normalize("NFKC", text)
        if include_sdk:
            for found in detect_pii(normalized):
                findings.append({"type": found.get("type", "pii")})
        for pattern in [*_CASE_PATTERNS, *_JP_NAME_PATTERNS]:
            for _ in pattern.finditer(normalized):
                findings.append({"type": "clinical_phi"})
    return findings


def mask_phi(value: Any, include_sdk: bool = True) -> tuple[Any, int]:
    """Mask PHI recursively and return (masked_value, count)."""

    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        count = 0
        if include_sdk:
            sdk_findings = detect_pii(text)
            for found in sorted(sdk_findings, key=lambda f: f["start"], reverse=True):
                text = text[: found["start"]] + "[PHI_MASKED]" + text[found["end"] :]
                count += 1
        for pattern in [*_CASE_PATTERNS, *_JP_NAME_PATTERNS]:
            text, n = pattern.subn("[PHI_MASKED]", text)
            count += n
        return text, count
    if isinstance(value, dict):
        total = 0
        masked = {}
        for key, item in value.items():
            masked_item, count = mask_phi(item, include_sdk=include_sdk)
            masked[key] = masked_item
            total += count
        return masked, total
    if isinstance(value, list):
        total = 0
        masked_list = []
        for item in value:
            masked_item, count = mask_phi(item, include_sdk=include_sdk)
            masked_list.append(masked_item)
            total += count
        return masked_list, total
    return value, 0

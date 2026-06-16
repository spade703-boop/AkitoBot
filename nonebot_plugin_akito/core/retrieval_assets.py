"""Shared retrieval corpus helpers.

This module centralizes:
- corpus-specific retrieval text construction
- script / PJSK retrieval pool selection
- PJSK entry normalization / flattening
- alias extraction for lightweight lexical matching
- corpus fingerprint generation for stale-index detection
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

_PJSK_DRAFT_MARKERS = ("待补", "待核对", "待对应", "待确认")
_SCRIPT_RETRIEVAL_TYPES = {"home", "story"}


def script_retrieval_text(entry: dict) -> str:
    """Canonical retrieval text for script entries."""
    text = (entry.get("cn_key") or "").strip() or (entry.get("context") or "").strip()
    return text or "（空）"


def script_retrieval_items(db: list[dict]) -> list[tuple[int, dict]]:
    """Ordered script rows that participate in retrieval / embedding."""
    items: list[tuple[int, dict]] = []
    for i, entry in enumerate(db):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in _SCRIPT_RETRIEVAL_TYPES:
            continue
        items.append((i, entry))
    return items


def script_retrieval_entries(db: list[dict]) -> list[dict]:
    """Plain script retrieval pool with original order preserved."""
    return [entry for _, entry in script_retrieval_items(db)]


def _normalize_aliases(raw_aliases: Any) -> list[str]:
    out: list[str] = []
    if isinstance(raw_aliases, str):
        raw_aliases = [raw_aliases]
    if not isinstance(raw_aliases, list):
        return out
    for item in raw_aliases:
        if not isinstance(item, str):
            continue
        alias = item.strip()
        if alias and alias not in out:
            out.append(alias)
    return out


def _extract_inline_aliases(text: str) -> list[str]:
    aliases: list[str] = []
    for match in re.finditer(r'"([^"\n]{1,24})"', text):
        alias = match.group(1).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    for match in re.finditer(r"([A-Za-z0-9一-龥]{1,16}\d{1,2})", text):
        alias = match.group(1).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def is_pjsk_draft_status(text: str) -> bool:
    """Whether a PJSK entry is a draft / placeholder and should be skipped by default."""
    return any(marker in text for marker in _PJSK_DRAFT_MARKERS)


def normalize_pjsk_entry(category: str, raw_entry: Any, ordinal: int) -> dict | None:
    """Normalize a raw PJSK entry into a structured retrieval item.

    Supports both legacy string entries and structured dict entries.
    """
    if isinstance(raw_entry, str):
        text = raw_entry.strip()
        if not text:
            return None
        aliases = _extract_inline_aliases(text)
        status = "draft" if is_pjsk_draft_status(text) else "active"
        title = aliases[0] if aliases else f"{category}-{ordinal}"
        prompt_text = text
        retrieval_text = " ".join([category, *aliases, text]).strip()
        return {
            "id": f"pjsk-{ordinal}",
            "domain": "glossary",
            "category": category,
            "title": title,
            "aliases": aliases,
            "text": text,
            "prompt_text": prompt_text,
            "retrieval_text": retrieval_text or text,
            "status": status,
        }

    if isinstance(raw_entry, dict):
        text = str(raw_entry.get("text") or raw_entry.get("prompt_text") or "").strip()
        title = str(raw_entry.get("title") or "").strip()
        domain = str(raw_entry.get("domain") or "glossary").strip() or "glossary"
        status = str(raw_entry.get("status") or "").strip() or ("draft" if is_pjsk_draft_status(text) else "active")
        aliases = _normalize_aliases(raw_entry.get("aliases"))
        aliases.extend(a for a in _extract_inline_aliases(text) if a not in aliases)
        if not title:
            title = aliases[0] if aliases else f"{category}-{ordinal}"
        prompt_text = str(raw_entry.get("prompt_text") or text).strip()
        retrieval_text = str(raw_entry.get("retrieval_text") or "").strip()
        if not retrieval_text:
            retrieval_text = " ".join(
                x for x in [category, title, *aliases, text] if isinstance(x, str) and x.strip()
            ).strip()
        if not text and not retrieval_text:
            return None
        return {
            "id": str(raw_entry.get("id") or f"pjsk-{ordinal}"),
            "domain": domain,
            "category": category,
            "title": title,
            "aliases": aliases,
            "text": text,
            "prompt_text": prompt_text or text,
            "retrieval_text": retrieval_text or text or "（空）",
            "status": status,
        }

    return None


def flatten_pjsk_knowledge(data: dict, *, include_drafts: bool = False) -> list[dict]:
    """Flatten `pjsk_knowledge.json` payload into normalized retrieval entries."""
    flat: list[dict] = []
    ordinal = 0
    for item in data.get("knowledge_list", []):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        entries = item.get("entries", [])
        if not isinstance(entries, list):
            continue
        for raw_entry in entries:
            ordinal += 1
            normalized = normalize_pjsk_entry(category, raw_entry, ordinal)
            if not normalized:
                continue
            if normalized.get("status") == "draft" and not include_drafts:
                continue
            flat.append(normalized)
    return flat


def pjsk_retrieval_text(entry: dict) -> str:
    """Canonical retrieval text for PJSK entries."""
    text = str(entry.get("retrieval_text") or "").strip()
    if text:
        return text
    fallback = " ".join(
        x for x in [entry.get("category", ""), entry.get("title", ""), entry.get("text", "")] if str(x).strip()
    ).strip()
    return fallback or "（空）"


def build_pjsk_prompt_text(entry: dict) -> str:
    """Prompt-side rendering text for a normalized PJSK entry."""
    prompt_text = str(entry.get("prompt_text") or entry.get("text") or "").strip()
    title = str(entry.get("title") or "").strip()
    category = str(entry.get("category") or "").strip()
    aliases = [a for a in _normalize_aliases(entry.get("aliases")) if a != title]
    prefix_parts = [category]
    if title:
        prefix_parts.append(title)
    if aliases:
        prefix_parts.append(f"别名：{' / '.join(aliases)}")
    prefix = "｜".join(prefix_parts)
    return f"{prefix}：{prompt_text}".strip("：")


def build_corpus_fingerprint(name: str, db: list[dict], text_builder) -> str:
    """Stable fingerprint for stale index detection."""
    payload = {
        "corpus": name,
        "count": len(db),
        "items": [text_builder(entry) for entry in db],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()

"""卡面知识库的检索文本与视觉结果质量闸门。"""

from __future__ import annotations

_TRUSTED_CARD_VISUAL_STATUSES = {"verified", "reviewed"}


def is_trusted_card_art(art: object) -> bool:
    """只允许通过审核的卡面视觉字段进入运行时上下文。"""
    return isinstance(art, dict) and art.get("status") in _TRUSTED_CARD_VISUAL_STATUSES


def is_trusted_card_hairstyle(hairstyle: object) -> bool:
    """只允许人工确认或明确验证的发型事实进入运行时上下文。"""
    return isinstance(hairstyle, dict) and hairstyle.get("status") in _TRUSTED_CARD_VISUAL_STATUSES


def card_retrieval_text(entry: dict) -> str:
    """构建卡面语义检索与向量指纹使用的规范文本。"""
    explicit = str(entry.get("retrieval_text") or "").strip()
    if explicit:
        return explicit

    character = str(entry.get("character_name") or "").strip()
    sequence = str(entry.get("sequence_alias") or "").strip()
    title = str(entry.get("title") or "").strip()
    event = str(entry.get("event_name") or "").strip()
    commissioned_song = str(entry.get("commissioned_song") or "").strip()
    initial_gacha = entry.get("initial_gacha")
    gacha_name = str(initial_gacha.get("name") or "").strip() if isinstance(initial_gacha, dict) else ""
    supply = str(entry.get("supply_label") or "").strip()
    aliases = entry.get("aliases", [])
    alias_text = " ".join(str(alias).strip() for alias in aliases if str(alias).strip()) if isinstance(aliases, list) else ""

    art_parts: list[str] = []
    hairstyle = entry.get("hairstyle")
    if is_trusted_card_hairstyle(hairstyle):
        description = str(hairstyle.get("description") or "").strip()
        if description:
            art_parts.append(description)
        features = hairstyle.get("features", [])
        if isinstance(features, list):
            art_parts.extend(str(feature).strip() for feature in features if str(feature).strip())
    for art_key in ("normal_art", "trained_art"):
        art = entry.get(art_key)
        if not is_trusted_card_art(art):
            continue
        for key in (
            "summary",
            "scene",
            "mood",
            "lighting",
            "owner_position",
            "owner_action",
            "owner_expression",
        ):
            value = str(art.get(key) or "").strip()
            if value:
                art_parts.append(value)
        for list_key in (
            "tags",
            "distinctive_anchors",
            "owner_clothing",
            "owner_headwear",
            "other_people",
        ):
            values = art.get(list_key, [])
            if isinstance(values, list):
                art_parts.extend(str(value).strip() for value in values if str(value).strip())

    text = " ".join(
        part
        for part in [
            character,
            sequence,
            title,
            event,
            commissioned_song,
            gacha_name,
            supply,
            alias_text,
            *dict.fromkeys(art_parts),
        ]
        if part
    )
    return text or "（空）"

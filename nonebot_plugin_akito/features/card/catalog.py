"""PJSK 卡面知识库：精确别称解析、歧义处理、Prompt 渲染与人工别称写入。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import unicodedata

from nonebot.log import logger

from ...core.data import (
    find_data_path,
    get_data_dir,
    load_json_file,
)
from ...core.retrieval import RetrievalContext, retrieve_result
from .retrieval import is_trusted_card_art, is_trusted_card_hairstyle

CARD_DB: list[dict] = []
CARD_ALIASES: dict[str, int] = {}
CARD_ALIAS_NOTES: dict[str, str] = {}
CARD_GROUP_ALIASES: dict[str, list[int]] = {}
CARD_GROUP_ALIAS_NOTES: dict[str, str] = {}


def _load_optional_json(filename: str):
    """Load optional card-specific JSON without emitting missing-file warnings."""
    if find_data_path(filename) is None:
        return None
    return load_json_file(filename, None)


def init_card_catalog() -> None:
    """Load structured card metadata and manually maintained aliases."""
    raw_catalog = _load_optional_json("pjsk_cards.json") or {}
    raw_aliases = _load_optional_json("pjsk_card_aliases.json") or {}
    try:
        cards = raw_catalog.get("cards", []) if isinstance(raw_catalog, dict) else raw_catalog
        if not isinstance(cards, list):
            cards = []
        normalized_cards = [entry for entry in cards if isinstance(entry, dict) and entry.get("id") is not None]

        aliases_payload = raw_aliases.get("card_aliases", raw_aliases.get("aliases", {})) if isinstance(raw_aliases, dict) else {}
        group_aliases_payload = raw_aliases.get("group_aliases", {}) if isinstance(raw_aliases, dict) else {}
        normalized_aliases: dict[str, int] = {}
        normalized_alias_notes: dict[str, str] = {}
        normalized_group_aliases: dict[str, list[int]] = {}
        normalized_group_notes: dict[str, str] = {}
        valid_ids = {int(entry["id"]) for entry in normalized_cards}
        if isinstance(aliases_payload, dict):
            for alias, raw_binding in aliases_payload.items():
                card_id = raw_binding.get("card_id") if isinstance(raw_binding, dict) else raw_binding
                try:
                    normalized_id = int(card_id)
                except (TypeError, ValueError):
                    continue
                if isinstance(alias, str) and alias.strip() and normalized_id in valid_ids:
                    clean_alias = alias.strip()
                    normalized_aliases[clean_alias] = normalized_id
                    if isinstance(raw_binding, dict):
                        note = str(raw_binding.get("note") or "").strip()
                        if note:
                            normalized_alias_notes[clean_alias] = note
        if isinstance(group_aliases_payload, dict):
            for alias, raw_binding in group_aliases_payload.items():
                card_ids = raw_binding.get("card_ids", []) if isinstance(raw_binding, dict) else raw_binding
                if not isinstance(card_ids, list) or not isinstance(alias, str) or not alias.strip():
                    continue
                normalized_ids: list[int] = []
                for card_id in card_ids:
                    try:
                        normalized_id = int(card_id)
                    except (TypeError, ValueError):
                        continue
                    if normalized_id in valid_ids and normalized_id not in normalized_ids:
                        normalized_ids.append(normalized_id)
                if not normalized_ids:
                    continue
                clean_alias = alias.strip()
                normalized_group_aliases[clean_alias] = normalized_ids
                if isinstance(raw_binding, dict):
                    note = str(raw_binding.get("note") or "").strip()
                    if note:
                        normalized_group_notes[clean_alias] = note

        CARD_DB.clear()
        CARD_DB.extend(normalized_cards)
        CARD_ALIASES.clear()
        CARD_ALIASES.update(normalized_aliases)
        CARD_ALIAS_NOTES.clear()
        CARD_ALIAS_NOTES.update(normalized_alias_notes)
        CARD_GROUP_ALIASES.clear()
        CARD_GROUP_ALIASES.update(normalized_group_aliases)
        CARD_GROUP_ALIAS_NOTES.clear()
        CARD_GROUP_ALIAS_NOTES.update(normalized_group_notes)
    except Exception as exc:
        logger.error(f"❌ 卡面知识库拼装失败: {exc}")


init_card_catalog()

_CARD_QUERY_MARKERS = ("卡", "卡面", "哪张", "花前", "花后", "特训前", "特训后", "开花", "四星", "插画")
_UNKNOWN_ALIAS_PATTERNS = (
    re.compile(r"[‘’'\"「『【]?([^，。！？?\s]{1,16}?)[’'\"」』】]?(?:是|指)?哪张卡"),
    re.compile(r"[‘’'\"「『【]?([^，。！？?\s]{1,16}?)[’'\"」』】]?(?:是|指)?什么卡"),
)


@dataclass(slots=True)
class CardResolution:
    """Result of deterministic card mention resolution."""

    status: str
    cards: list[dict] = field(default_factory=list)
    matched_alias: str = ""
    reason: str = ""


def normalize_card_alias(value: str) -> str:
    """统一全半角、大小写、空白与常见括号，供别称精确比较。"""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = re.sub(r"[\s《》〈〉「」『』【】\[\]()（）]", "", normalized)
    return normalized.strip("，。！？!?、:：·・~～")


def _card_id_map() -> dict[int, dict]:
    return {int(card["id"]): card for card in CARD_DB if card.get("id") is not None}


def _built_in_aliases(card: dict) -> list[str]:
    aliases = card.get("aliases", [])
    result = [str(alias).strip() for alias in aliases if str(alias).strip()] if isinstance(aliases, list) else []
    for key in ("sequence_alias", "title"):
        value = str(card.get(key) or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _alias_index() -> dict[str, list[tuple[str, dict]]]:
    index: dict[str, list[tuple[str, dict]]] = {}
    cards_by_id = _card_id_map()
    for card in CARD_DB:
        for alias in _built_in_aliases(card):
            normalized = normalize_card_alias(alias)
            if normalized:
                index.setdefault(normalized, []).append((alias, card))
    for alias, card_id in CARD_ALIASES.items():
        card = cards_by_id.get(int(card_id))
        normalized = normalize_card_alias(alias)
        if card is not None and normalized:
            index[normalized] = [(alias, card)]
    for alias, card_ids in CARD_GROUP_ALIASES.items():
        entries = [(alias, cards_by_id[card_id]) for card_id in card_ids if card_id in cards_by_id]
        normalized = normalize_card_alias(alias)
        if entries and normalized:
            index[normalized] = entries
    return index


def resolve_card_mentions(text: str) -> CardResolution:
    """按人工别称、角色序号与官方卡名解析消息中的卡面实体。"""
    normalized_text = normalize_card_alias(text)
    if not normalized_text or not CARD_DB:
        return CardResolution(status="no_hit", reason="empty_catalog")

    alias_matches: list[tuple[int, str, dict]] = []
    for normalized_alias, entries in _alias_index().items():
        if normalized_alias not in normalized_text:
            continue
        for display_alias, card in entries:
            alias_matches.append((len(normalized_alias), display_alias, card))

    if not alias_matches:
        return CardResolution(status="no_hit", reason="no_exact_alias")
    longest = max(length for length, _alias, _card in alias_matches)
    matches: dict[int, dict] = {}
    matched_aliases: list[str] = []
    for length, display_alias, card in alias_matches:
        if length != longest:
            continue
        matches[int(card["id"])] = card
        matched_aliases.append(display_alias)
    cards = sorted(matches.values(), key=lambda card: (int(card.get("release_at", 0)), int(card["id"])))
    status = "hit" if len(cards) == 1 else "ambiguous"
    return CardResolution(status=status, cards=cards, matched_alias=" / ".join(dict.fromkeys(matched_aliases)))


def _render_art(label: str, art: object) -> str:
    if not is_trusted_card_art(art):
        return ""
    summary = str(art.get("summary") or "").strip()
    return f"；{label}：{summary}" if summary else ""


def _manual_alias_note(card: dict) -> str:
    card_id = int(card.get("id", 0))
    notes = [
        f"{alias}：{CARD_ALIAS_NOTES[alias]}"
        for alias, bound_id in CARD_ALIASES.items()
        if int(bound_id) == card_id and CARD_ALIAS_NOTES.get(alias)
    ]
    return f"；群内俗称：{'；'.join(notes)}" if notes else ""


def _render_hairstyle(card: dict) -> str:
    hairstyle = card.get("hairstyle")
    if not isinstance(hairstyle, dict):
        return ""
    if not hairstyle.get("available", False):
        return "；限定发型：无"
    description = str(hairstyle.get("description") or "").strip()
    if description and is_trusted_card_hairstyle(hairstyle):
        source = "人工确认" if hairstyle.get("source") == "manual_review" else "已校验"
        return f"；限定发型：有（{source}），{description}"
    if description:
        return "；限定发型：有，自动识别初稿待人工复核"
    return "；限定发型：有，外观待补充"


def render_card_fact(card: dict) -> str:
    """将单张结构化卡片渲染为紧凑、只陈述事实的 Prompt 文本。"""
    sequence = str(card.get("sequence_alias") or "").strip()
    character = str(card.get("character_name") or "未知角色").strip()
    title = str(card.get("title") or "未命名卡面").strip()
    event = str(card.get("event_name") or "无对应活动").strip()
    supply = str(card.get("supply_label") or "类型未知").strip()
    commissioned_song = str(card.get("commissioned_song") or "").strip()
    song = f"｜书下曲：{commissioned_song}" if commissioned_song else ""
    normal = _render_art("花前", card.get("normal_art"))
    trained = _render_art("花后", card.get("trained_art"))
    hairstyle = _render_hairstyle(card)
    note = _manual_alias_note(card)
    fact = f"- {sequence}｜ID {card.get('id')}｜{character}「{title}」｜出处：{event}{song}｜{supply}{hairstyle}{note}{normal}{trained}"
    return fact.replace("。；", "；")


def _group_alias_note(alias: str) -> str:
    note = CARD_GROUP_ALIAS_NOTES.get(alias, "")
    return f"；群内含义：{note.rstrip('。')}" if note else ""


def _narrow_group_by_character(cards: list[dict], query: str) -> list[dict]:
    normalized_query = normalize_card_alias(query)
    matched_ids: set[int] = set()
    for card in cards:
        character_aliases = {
            normalize_card_alias(str(card.get("character_name") or "")),
            normalize_card_alias(str(card.get("character_name_jp") or "")),
        }
        sequence = str(card.get("sequence_alias") or "")
        character_aliases.add(normalize_card_alias(re.sub(r"\d+$", "", sequence)))
        if int(card.get("character_id", 0)) == 9:
            character_aliases.update({"心羽", "豆", "小豆泽心羽", "小豆沢こはね"})
        elif int(card.get("character_id", 0)) == 11:
            character_aliases.update({"彰", "彰人", "东云彰人", "東雲彰人"})
        elif int(card.get("character_id", 0)) == 12:
            character_aliases.update({"冬", "冬弥", "青柳冬弥"})
        if any(alias and alias in normalized_query for alias in character_aliases):
            matched_ids.add(int(card["id"]))
    return [card for card in cards if int(card["id"]) in matched_ids] or cards


def _render_ambiguity(cards: list[dict], query: str) -> str:
    candidates = "\n".join(render_card_fact(card) for card in cards[:5])
    return (
        f"\n🃏【卡面别称需要澄清】用户说的是「{query}」，目前无法唯一对应。\n"
        f"候选：\n{candidates}\n"
        "请简短追问对方具体指哪一张，不要自行猜测，也不要把候选信息混成一张卡。\n"
    )


def _extract_unknown_alias(query: str) -> str:
    for pattern in _UNKNOWN_ALIAS_PATTERNS:
        match = pattern.search(query)
        if match:
            candidate = match.group(1).strip("叫所谓")
            if candidate and not any(marker in candidate for marker in ("这", "那", "哪", "什么")):
                return candidate
    return ""


def _looks_like_card_query(query: str) -> bool:
    return any(marker in query for marker in _CARD_QUERY_MARKERS)


async def get_relevant_cards(
    query: str,
    num: int = 3,
    retrieval_ctx: RetrievalContext | None = None,
) -> str:
    """构建卡面事实块：精确实体优先，描述型问题再走独立 cards RAG。"""
    resolution = resolve_card_mentions(query)
    if resolution.status == "hit":
        facts = "\n".join(render_card_fact(card) for card in resolution.cards[:num])
        return f"\n🃏【卡面事实库（优先于模型常识，禁止补造）】\n{facts}\n"
    if resolution.status == "ambiguous":
        group_alias = next(
            (alias for alias in CARD_GROUP_ALIASES if normalize_card_alias(alias) == normalize_card_alias(resolution.matched_alias)),
            "",
        )
        if group_alias:
            narrowed = _narrow_group_by_character(resolution.cards, query)
            if len(narrowed) == 1:
                return (
                    f"\n🃏【群内卡组别称】「{group_alias}」指向一组同期卡{_group_alias_note(group_alias)}。\n"
                    f"用户同时指定了角色，因此本句对应：\n{render_card_fact(narrowed[0])}\n"
                )
            facts = "\n".join(render_card_fact(card) for card in narrowed[:5])
            return (
                f"\n🃏【群内卡组别称】「{group_alias}」指向一组同期卡{_group_alias_note(group_alias)}。\n"
                f"对应成员：\n{facts}\n"
                "若用户要问具体卡或发型而没有说角色，请简短追问是哪位角色，不要默认其中一张。\n"
            )
        return _render_ambiguity(resolution.cards, resolution.matched_alias or query)
    if not _looks_like_card_query(query):
        return ""

    unknown_alias = _extract_unknown_alias(query)
    if unknown_alias and not any(marker in unknown_alias for marker in _CARD_QUERY_MARKERS):
        return (
            f"\n🃏【未知卡面别称】「{unknown_alias}」尚未绑定到唯一卡面。"
            "请直接追问对方是哪个角色、哪期活动或花前/花后，不要猜测。\n"
        )

    ctx = retrieval_ctx or RetrievalContext(original_query=query, query=query)
    result = await retrieve_result("cards", ctx.query or query, num, ctx=ctx)
    if result.status == "hit":
        relevant = [CARD_DB[index] for index in result.ids if 0 <= index < len(CARD_DB)]
        if relevant:
            facts = "\n".join(render_card_fact(card) for card in relevant)
            return f"\n🃏【可能相关的卡面事实（按描述检索，禁止把多张卡混为一张）】\n{facts}\n"

    return ""


def _alias_file_path() -> Path:
    existing = get_data_dir() / "content" / "pjsk_card_aliases.json"
    return existing


def _save_aliases() -> None:
    target = _alias_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".tmp")
    card_aliases = {
        alias: {
            "card_id": card_id,
            **({"note": CARD_ALIAS_NOTES[alias]} if CARD_ALIAS_NOTES.get(alias) else {}),
        }
        for alias, card_id in sorted(CARD_ALIASES.items())
    }
    group_aliases = {
        alias: {
            "card_ids": card_ids,
            **({"note": CARD_GROUP_ALIAS_NOTES[alias]} if CARD_GROUP_ALIAS_NOTES.get(alias) else {}),
        }
        for alias, card_ids in sorted(CARD_GROUP_ALIASES.items())
    }
    payload = {"version": 2, "card_aliases": card_aliases, "group_aliases": group_aliases}
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, target)


def bind_card_alias(alias: str, target: str, note: str = "") -> tuple[bool, str]:
    """将人工别称绑定到唯一卡片；冲突时拒绝覆盖。"""
    clean_alias = alias.strip()
    normalized_alias = normalize_card_alias(clean_alias)
    if len(normalized_alias) < 2:
        return False, "别称至少需要 2 个有效字符。"
    if any(normalize_card_alias(existing) == normalized_alias for existing in CARD_GROUP_ALIASES):
        return False, f"「{clean_alias}」已被用作卡组别称，请先解绑。"

    target_resolution = resolve_card_mentions(target)
    if target_resolution.status != "hit" and target.strip().isdigit():
        card = _card_id_map().get(int(target.strip()))
        target_resolution = CardResolution(status="hit", cards=[card]) if card else target_resolution
    if target_resolution.status != "hit" or not target_resolution.cards:
        return False, "目标卡面无法唯一确定，请使用卡 ID 或彰N/冬N/杏N/心羽N。"

    for existing_alias, existing_card_id in CARD_ALIASES.items():
        if normalize_card_alias(existing_alias) != normalized_alias:
            continue
        target_id = int(target_resolution.cards[0]["id"])
        if int(existing_card_id) == target_id:
            return True, f"「{clean_alias}」已经绑定到 {target_resolution.cards[0].get('sequence_alias')}。"
        return False, f"「{existing_alias}」已绑定到卡 ID {existing_card_id}，请先解绑。"

    card = target_resolution.cards[0]
    CARD_ALIASES[clean_alias] = int(card["id"])
    clean_note = note.strip()
    if clean_note:
        CARD_ALIAS_NOTES[clean_alias] = clean_note
    _save_aliases()
    init_card_catalog()
    logger.info(f"🃏 卡面别称已绑定: {clean_alias} -> {card['id']}")
    return True, f"已将「{clean_alias}」绑定到 {card.get('sequence_alias')}「{card.get('title')}」。"


def unbind_card_alias(alias: str) -> tuple[bool, str]:
    """删除人工卡面别称绑定。"""
    normalized_alias = normalize_card_alias(alias)
    existing = next((key for key in CARD_ALIASES if normalize_card_alias(key) == normalized_alias), "")
    if not existing:
        return False, f"没有找到别称「{alias.strip()}」的人工绑定。"
    CARD_ALIASES.pop(existing, None)
    CARD_ALIAS_NOTES.pop(existing, None)
    _save_aliases()
    init_card_catalog()
    logger.info(f"🃏 卡面别称已解绑: {existing}")
    return True, f"已解绑卡面别称「{existing}」。"


def bind_card_group_alias(alias: str, targets: list[str], note: str = "") -> tuple[bool, str]:
    """将人工卡组别称绑定到多张明确卡片。"""
    clean_alias = alias.strip()
    normalized_alias = normalize_card_alias(clean_alias)
    if len(normalized_alias) < 2:
        return False, "别称至少需要 2 个有效字符。"
    if any(normalize_card_alias(existing) == normalized_alias for existing in CARD_ALIASES):
        return False, f"「{clean_alias}」已被用作单卡别称，请先解绑。"

    card_ids: list[int] = []
    for target in targets:
        resolution = resolve_card_mentions(target)
        if resolution.status != "hit" and target.strip().isdigit():
            card = _card_id_map().get(int(target.strip()))
            resolution = CardResolution(status="hit", cards=[card]) if card else resolution
        if resolution.status != "hit" or not resolution.cards:
            return False, f"目标「{target}」无法唯一确定，请使用卡 ID 或角色序号。"
        card_id = int(resolution.cards[0]["id"])
        if card_id not in card_ids:
            card_ids.append(card_id)
    if len(card_ids) < 2:
        return False, "卡组别称至少需要绑定 2 张不同卡面。"

    existing = next((key for key in CARD_GROUP_ALIASES if normalize_card_alias(key) == normalized_alias), "")
    if existing and CARD_GROUP_ALIASES[existing] != card_ids:
        return False, f"「{existing}」已有卡组绑定，请先解绑。"
    CARD_GROUP_ALIASES[clean_alias] = card_ids
    clean_note = note.strip()
    if clean_note:
        CARD_GROUP_ALIAS_NOTES[clean_alias] = clean_note
    _save_aliases()
    init_card_catalog()
    labels = "、".join(str(_card_id_map()[card_id].get("sequence_alias")) for card_id in card_ids)
    return True, f"已将卡组别称「{clean_alias}」绑定到 {labels}。"


def unbind_card_group_alias(alias: str) -> tuple[bool, str]:
    normalized_alias = normalize_card_alias(alias)
    existing = next((key for key in CARD_GROUP_ALIASES if normalize_card_alias(key) == normalized_alias), "")
    if not existing:
        return False, f"没有找到卡组别称「{alias.strip()}」的人工绑定。"
    CARD_GROUP_ALIASES.pop(existing, None)
    CARD_GROUP_ALIAS_NOTES.pop(existing, None)
    _save_aliases()
    init_card_catalog()
    return True, f"已解绑卡组别称「{existing}」。"

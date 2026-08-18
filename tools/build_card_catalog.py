"""从 PJSK master data 构建 VBS 四星卡面知识库，并可选用 GLM-4.6V 批量生成花前/花后简介。

用法：
  py tools/build_card_catalog.py --metadata-only
  py tools/build_card_catalog.py --resume
  py tools/build_card_catalog.py --resume --limit 10 --concurrency 2
  py tools/build_card_catalog.py --images-dir data/card_images --resume

默认读取 tmp_pjsk/，输出 data/content/pjsk_cards.json；视觉失败不会丢弃已生成的确定元数据。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import defaultdict
import json
import os
from pathlib import Path
import sys
from typing import Any

VBS_CHARACTER_IDS = {9, 10, 11, 12}
CHARACTER_META = {
    9: {"name": "小豆泽心羽", "name_jp": "小豆沢こはね", "sequence_prefix": "心羽"},
    10: {"name": "白石杏", "name_jp": "白石杏", "sequence_prefix": "杏"},
    11: {"name": "东云彰人", "name_jp": "東雲彰人", "sequence_prefix": "彰"},
    12: {"name": "青柳冬弥", "name_jp": "青柳冬弥", "sequence_prefix": "冬"},
}
SUPPLY_LABELS = {
    "normal": "常驻",
    "birthday": "生日限定",
    "term_limited": "期间限定",
    "colorful_festival_limited": "FES限定",
    "bloom_festival_limited": "Bloom FES限定",
    "unit_event_limited": "World Link限定",
    "collaboration_limited": "联动限定",
}
DEFAULT_ASSET_URL = (
    "https://storage.sekai.best/sekai-jp-assets/character/member/"
    "{assetbundle}/{variant}.webp"
)
VISION_MODEL = "glm-4.6v-flash"
VISION_REQUEST_TIMEOUT = 60.0
TRANSIENT_VISION_ERRORS = (
    "429",
    "1305",
    "500",
    "502",
    "503",
    "504",
    "访问量过大",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "connection reset",
)
UNSAFE_ART_IDENTITY_TERMS = (
    "发色",
    "头发",
    "短发",
    "长发",
    "橙发",
    "蓝发",
    "金发",
    "黑发",
    "银发",
    "灰发",
    "瞳色",
    "眼睛颜色",
)

VISION_SCHEMA_VERSION = 3

NORMAL_IDENTITY_HINTS = {
    "小豆泽心羽": (
        "花前辅助定位线索：通常为浅米黄色头发，常规造型是短双马尾，有些卡会反戴鸭舌帽。"
        "双马尾和帽子都不是必须条件。"
    ),
    "白石杏": (
        "花前辅助定位线索：通常为黑发并带蓝色发尾，常见星星发饰。"
        "长发和发饰都不是必须条件。"
    ),
    "东云彰人": (
        "花前辅助定位线索：通常为橙发并带黄色挑染，常见耳链或耳饰。"
        "头发长短和耳饰都不是必须条件。"
    ),
    "青柳冬弥": (
        "花前辅助定位线索：通常为深蓝与浅灰分区发色，左眼下有泪痣。"
        "泪痣可能因角度、遮挡或分辨率看不清，不能因未看见而否定。"
    ),
}

VISION_PROMPT = """你是《Project SEKAI》卡牌插画资料整理员。第一张图片是花前，第二张图片是花后。
官方 master data 已确认两张图都属于“__CHARACTER_NAME__”，角色归属是不可更改的事实。你的任务不是重新猜角色，而是定位并描述卡主。
__NORMAL_IDENTITY_HINT__这些线索只用于花前辅助定位，不能作为硬条件；花后可能更换发型，瞳色和发色也可能受环境光影响，禁止据此改判身份。
只输出一个合法 JSON 对象，不要 markdown，不要推测卡名、活动、剧情、圈内俗称或人物关系。
每张图分别填写卡主的可见程度、画面位置、动作、服装和头部配饰。多人构图必须明确卡主位置；无法可靠定位时 owner_visibility 填 unclear，其余卡主字段留空。
other_people 只写相对位置和可见外观，不猜名字。distinctive_anchors 填写最适合区分这张卡的物件、动作、构图或场景，不要只写“梦幻”“有活力”之类泛词，也不要使用头发、发色或瞳色作为锚点。眼神和表情形态可以写入 owner_expression。
不要输出自由发挥的 summary，也不要自报 confidence。严格使用以下结构：
{
  "normal_art": {
    "owner_visibility":"clear/partial/unclear",
    "owner_position":"例如右侧前景",
    "owner_action":"卡主正在做什么",
    "owner_expression":"可见表情；看不清留空",
    "owner_clothing":["1到4个服装短语"],
    "owner_headwear":["帽子、头饰；没有则空数组"],
    "other_people":["其他人物的相对位置和外观；没有则空数组"],
    "scene":"具体地点或场景",
    "mood":"简短氛围",
    "lighting":"显著光线或色调",
    "distinctive_anchors":["3到8个具有检索价值的显著锚点"]
  },
  "trained_art": {
    "owner_visibility":"clear/partial/unclear",
    "owner_position":"例如中央前景",
    "owner_action":"卡主正在做什么",
    "owner_expression":"可见表情；看不清留空",
    "owner_clothing":["1到4个服装短语"],
    "owner_headwear":["帽子、头饰；没有则空数组"],
    "other_people":["其他人物的相对位置和外观；没有则空数组"],
    "scene":"具体地点或场景",
    "mood":"简短氛围",
    "lighting":"显著光线或色调",
    "distinctive_anchors":["3到8个具有检索价值的显著锚点"]
  }
}
看不清的字段留空，禁止编造。"""

HAIRSTYLE_PROMPT = """你是《Project SEKAI》限定发型资料审核员。第一张图片是花前，仅供了解卡主常规外观；第二张图片是花后，是本次唯一需要提取发型的图片。
官方 master data 已确认这张限定卡属于“__CHARACTER_NAME__”，不要重新判断角色。花前和花后构图可能完全不同，不能按相同位置机械追踪。
花后可能更换发型，瞳色、发色会受环境光影响。不要用发型、发色或瞳色反推身份；只描述官方卡主在花后图里可见的发型结构。
若多人构图中无法可靠定位卡主，clarity 填 unclear，结构字段留空。帽子、皇冠、角和大型头饰写入 headwear，不得当成头发或发型；贴在头发上的发夹、发带写入 hair_accessories。
observed_color 只记录画面中看到的颜色；lighting_effect 说明是否可能受环境光影响，两者都不能覆盖角色标准发色。
只输出合法 JSON，不要 markdown、剧情、卡名、俗称、服装或自报置信度：
{
  "owner_location":"卡主在花后图中的位置；不确定留空",
  "clarity":"clear/partial/unclear",
  "structure": {
    "length":"长度",
    "silhouette":"整体轮廓或造型",
    "bangs":"刘海",
    "side_locks":"两侧发束",
    "tied_part":"束发方式和位置；没有则写无束发",
    "rear_shape":"后发、后颈或发尾形态",
    "streaks":"挑染；看不清留空"
  },
  "hair_accessories":["直接固定在头发上的小型发饰"],
  "headwear":["帽子、皇冠、角或大型头饰"],
  "observed_color":"花后画面观察色；看不清留空",
  "lighting_effect":"环境光对颜色的影响；没有明显影响则留空"
}
禁止根据花前默认发型填写花后字段。"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_optional_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = _read_json(path)
    return payload if isinstance(payload, list) else []


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _event_for_card(card: dict, event_cards: list[dict], events_by_id: dict[int, dict]) -> dict | None:
    candidates: list[tuple[int, dict]] = []
    release_at = int(card.get("releaseAt", 0))
    for link in event_cards:
        if int(link.get("cardId", 0)) != int(card["id"]) or not link.get("isDisplayCardStory", False):
            continue
        event = events_by_id.get(int(link.get("eventId", 0)))
        if event is None:
            continue
        distance = abs(int(event.get("startAt", 0)) - release_at)
        candidates.append((distance, event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], int(item[1].get("startAt", 0))))
    return candidates[0][1]


def _automatic_aliases(character_id: int, sequence: int, title: str) -> list[str]:
    meta = CHARACTER_META[character_id]
    prefix = meta["sequence_prefix"]
    aliases = [f"{prefix}{sequence}", title]
    if character_id == 9:
        aliases.extend([f"心羽{sequence}", f"豆{sequence}", f"小豆泽心羽{sequence}", f"小豆沢こはね{sequence}"])
    elif character_id == 10:
        aliases.extend([f"白石杏{sequence}"])
    elif character_id == 11:
        aliases.extend([f"彰人{sequence}", f"东云彰人{sequence}", f"東雲彰人{sequence}"])
    elif character_id == 12:
        aliases.extend([f"冬弥{sequence}", f"青柳冬弥{sequence}"])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _commissioned_songs_by_event(master_dir: Path) -> dict[int, list[dict]]:
    event_musics = _read_optional_list(master_dir / "eventMusics.json")
    musics = _read_optional_list(master_dir / "musics.json")
    musics_by_id = {int(music["id"]): music for music in musics if music.get("id") is not None}
    result: dict[int, list[dict]] = defaultdict(list)
    for link in event_musics:
        music = musics_by_id.get(int(link.get("musicId", 0)))
        if music is None or not music.get("isNewlyWrittenMusic", False):
            continue
        result[int(link.get("eventId", 0))].append(
            {"id": int(music["id"]), "title": str(music.get("title") or "").strip()}
        )
    return result


def _initial_gachas_by_card(master_dir: Path) -> dict[int, dict]:
    """为卡片选取首次实装时的主卡池；复刻和付费派生池不参与。"""
    gachas = _read_optional_list(master_dir / "gachas.json")
    result: dict[int, tuple[tuple[int, int, int], dict]] = {}
    for gacha in gachas:
        name = str(gacha.get("name") or "").strip()
        if not name or name.startswith("[") or "復刻" in name or "复刻" in name:
            continue
        start_at = int(gacha.get("startAt", 0))
        for pickup in gacha.get("gachaPickups", []):
            if not isinstance(pickup, dict) or pickup.get("cardId") is None:
                continue
            card_id = int(pickup["cardId"])
            preferred_type = 0 if str(gacha.get("gachaType") or "") == "ceil" else 1
            paid_variant = 1 if "有償" in name or "限定" in name[:12] else 0
            rank = (start_at, preferred_type, paid_variant)
            current = result.get(card_id)
            if current is None or rank < current[0]:
                result[card_id] = (
                    rank,
                    {
                        "id": int(gacha.get("id", 0)),
                        "name": name,
                        "start_at": start_at,
                    },
                )
    return {card_id: value for card_id, (_rank, value) in result.items()}


def _hairstyle_metadata(supply_type: str) -> dict:
    available = supply_type != "normal"
    return {
        "available": available,
        "description": "",
        "features": [],
        "source_image": "trained" if available else "",
        "visible_in": "trained" if available else "",
        "clarity": "",
        "owner_location": "",
        "structure": {},
        "hair_accessories": [],
        "headwear": [],
        "observed_color": "",
        "lighting_effect": "",
        "quality_flags": [],
        "status": "pending" if available else "not_applicable",
    }


def build_metadata(master_dir: Path) -> list[dict]:
    """从 master data 生成 VBS 四星结构化元数据，序号按实装时间与卡 ID 稳定排序。"""
    cards = _read_json(master_dir / "cards.json")
    event_cards = _read_json(master_dir / "eventCards.json")
    events = _read_json(master_dir / "events.json")
    supplies = _read_json(master_dir / "cardSupplies.json")
    commissioned_songs = _commissioned_songs_by_event(master_dir)
    initial_gachas = _initial_gachas_by_card(master_dir)

    events_by_id = {int(event["id"]): event for event in events}
    supplies_by_id = {int(supply["id"]): supply for supply in supplies}
    selected = [
        card for card in cards
        if int(card.get("characterId", 0)) in VBS_CHARACTER_IDS and card.get("cardRarityType") == "rarity_4"
    ]
    selected.sort(key=lambda card: (int(card.get("releaseAt", 0)), int(card["id"])))

    sequence_counts: dict[int, int] = defaultdict(int)
    result: list[dict] = []
    for card in selected:
        character_id = int(card["characterId"])
        sequence_counts[character_id] += 1
        sequence = sequence_counts[character_id]
        meta = CHARACTER_META[character_id]
        title = str(card.get("prefix") or "").strip()
        event = _event_for_card(card, event_cards, events_by_id)
        supply = supplies_by_id.get(int(card.get("cardSupplyId", 0)), {})
        supply_type = str(supply.get("cardSupplyType") or "unknown")
        event_id = int(event["id"]) if event else None
        song_entries = commissioned_songs.get(event_id, []) if event_id is not None else []
        initial_gacha = initial_gachas.get(int(card["id"]))
        sequence_alias = f"{meta['sequence_prefix']}{sequence}"
        result.append(
            {
                "id": int(card["id"]),
                "character_id": character_id,
                "character_name": meta["name"],
                "character_name_jp": meta["name_jp"],
                "sequence": sequence,
                "sequence_alias": sequence_alias,
                "title": title,
                "aliases": _automatic_aliases(character_id, sequence, title),
                "event_id": event_id,
                "event_name": str(event.get("name") or "") if event else "",
                "commissioned_songs": song_entries,
                "commissioned_song": song_entries[0]["title"] if song_entries else "",
                "initial_gacha": initial_gacha,
                "release_at": int(card.get("releaseAt", 0)),
                "assetbundle_name": str(card.get("assetbundleName") or ""),
                "supply_type": supply_type,
                "supply_label": SUPPLY_LABELS.get(supply_type, "类型未知"),
                "hairstyle": _hairstyle_metadata(supply_type),
                "normal_art": None,
                "trained_art": None,
                "generation": {
                    "status": "metadata_only",
                    "art_status": "metadata_only",
                    "hairstyle_status": "pending" if supply_type != "normal" else "not_applicable",
                    "quality_status": "metadata_only",
                    "review_reasons": [],
                    "model": "",
                    "schema_version": VISION_SCHEMA_VERSION,
                    "error": "",
                },
            }
        )
    return result


def merge_resume_metadata(cards: list[dict], existing_payload: dict) -> list[dict]:
    """按卡 ID 合并旧视觉结果，master data 元字段始终以本轮为准。"""
    existing = {
        int(card["id"]): card
        for card in existing_payload.get("cards", [])
        if isinstance(card, dict) and card.get("id") is not None
    }
    for card in cards:
        old = existing.get(int(card["id"]))
        if not old:
            continue
        card["normal_art"] = old.get("normal_art")
        card["trained_art"] = old.get("trained_art")
        old_hairstyle = old.get("hairstyle")
        old_generation = old.get("generation") or {}
        if isinstance(old_hairstyle, dict) and (
            old_hairstyle.get("source") == "manual_review"
            or int(old_generation.get("schema_version", 0)) >= VISION_SCHEMA_VERSION
        ):
            card["hairstyle"] = old_hairstyle
        if int(old_generation.get("schema_version", 0)) >= VISION_SCHEMA_VERSION:
            card["generation"] = old_generation
    return cards


def apply_review_overrides(cards: list[dict], overrides_payload: dict) -> list[dict]:
    """应用人工审核字段；人工结论始终覆盖视觉模型初稿。"""
    raw_cards = overrides_payload.get("cards", {}) if isinstance(overrides_payload, dict) else {}
    if not isinstance(raw_cards, dict):
        return cards
    cards_by_id = {int(card["id"]): card for card in cards}
    for raw_card_id, override in raw_cards.items():
        if not isinstance(override, dict):
            continue
        try:
            card = cards_by_id.get(int(raw_card_id))
        except (TypeError, ValueError):
            continue
        if card is None:
            continue
        raw_hairstyle = override.get("hairstyle")
        if not isinstance(raw_hairstyle, dict) or not card.get("hairstyle", {}).get("available", False):
            continue
        description = str(raw_hairstyle.get("description") or "").strip()[:300]
        features = raw_hairstyle.get("features", [])
        if not description:
            continue
        current_hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}

        def review_value(
            key: str,
            default: Any,
            raw_review: dict = raw_hairstyle,
            current_review: dict = current_hairstyle,
        ) -> Any:
            value = raw_review.get(key)
            return value if value not in (None, "", []) else current_review.get(key, default)

        card["hairstyle"] = {
            "available": True,
            "description": description,
            "features": (
                [str(feature).strip()[:40] for feature in features[:8] if str(feature).strip()]
                if isinstance(features, list)
                else []
            ),
            "source_image": "trained",
            "visible_in": "trained",
            "clarity": "clear",
            "owner_location": str(review_value("owner_location", "")).strip()[:80],
            "structure": review_value("structure", {}) if isinstance(review_value("structure", {}), dict) else {},
            "hair_accessories": _clean_list(review_value("hair_accessories", []), limit=5, item_limit=40),
            "headwear": _clean_list(review_value("headwear", []), limit=5, item_limit=40),
            "observed_color": str(review_value("observed_color", "")).strip()[:60],
            "lighting_effect": str(review_value("lighting_effect", "")).strip()[:100],
            "quality_flags": [],
            "status": "reviewed",
            "source": "manual_review",
            "review_note": str(raw_hairstyle.get("review_note") or "").strip()[:300],
        }
        generation = card.get("generation") if isinstance(card.get("generation"), dict) else {}
        if generation:
            generation["hairstyle_status"] = "reviewed"
            generation["status"] = "complete" if generation.get("art_status") == "complete" else generation.get("status")
            generation["review_reasons"] = _card_review_reasons(card)
            art_statuses = {
                str(card.get(key, {}).get("status") or "missing")
                for key in ("normal_art", "trained_art")
                if isinstance(card.get(key), dict)
            }
            if "rejected" in art_statuses:
                generation["quality_status"] = "rejected"
            elif generation["review_reasons"]:
                generation["quality_status"] = "needs_review"
            else:
                generation["quality_status"] = "verified"
            if generation.get("art_status") == "complete":
                generation["error"] = ""
    return cards


def _local_image_path(images_dir: Path, assetbundle: str, variant: str) -> Path | None:
    candidates = [
        images_dir / assetbundle / f"{variant}.webp",
        images_dir / assetbundle / f"{variant}.png",
        images_dir / assetbundle / f"{variant}.jpg",
        images_dir / f"{assetbundle}_{variant}.webp",
        images_dir / f"{assetbundle}_{variant}.png",
        images_dir / f"{assetbundle}_{variant}.jpg",
    ]
    return next((path for path in candidates if path.exists()), None)


async def _load_image(
    session: Any,
    assetbundle: str,
    variant: str,
    images_dir: Path | None,
    asset_url_template: str,
) -> bytes:
    if images_dir is not None:
        local_path = _local_image_path(images_dir, assetbundle, variant)
        if local_path is not None:
            return local_path.read_bytes()
    url = asset_url_template.format(assetbundle=assetbundle, variant=variant)
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.read()


def _image_data_url(image_data: bytes) -> str:
    """把下载到的卡图编码为视觉 API 可直接消费的 data URL。"""
    if image_data.startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(image_data).decode('ascii')}"


def _clean_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_list(value: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            text
            for item in value[:limit]
            if (text := _clean_text(item, item_limit))
        )
    )


def _build_art_summary(character_name: str, art: dict) -> str:
    visibility = art["owner_visibility"]
    if visibility == "unclear":
        return f"官方卡主为{character_name}，但该图无法可靠定位卡主，视觉细节待人工复核。"

    subject = character_name
    if art["owner_position"]:
        subject += f"位于{art['owner_position']}"
    if art["owner_action"]:
        subject += f"，{art['owner_action']}"
    if art["owner_expression"]:
        subject += f"，{art['owner_expression']}"

    parts = [subject]
    if art["owner_clothing"]:
        parts.append(f"穿着{'、'.join(art['owner_clothing'])}")
    if art["owner_headwear"]:
        parts.append(f"头部配饰为{'、'.join(art['owner_headwear'])}")
    if art["scene"]:
        parts.append(f"场景为{art['scene']}")
    if art["other_people"]:
        parts.append(f"其他人物：{'、'.join(art['other_people'][:3])}")
    if art["distinctive_anchors"]:
        parts.append(f"显著元素有{'、'.join(art['distinctive_anchors'][:4])}")
    return "；".join(parts)[:300] + "。"


def _normalize_art(raw: object, character_name: str) -> dict:
    raw_art = raw if isinstance(raw, dict) else {}
    visibility = _clean_text(raw_art.get("owner_visibility"), 16).lower()
    if visibility not in {"clear", "partial", "unclear"}:
        visibility = "unclear"
    distinctive_anchors = _clean_list(raw_art.get("distinctive_anchors"), limit=8, item_limit=40)
    distinctive_anchors = [
        anchor
        for anchor in distinctive_anchors
        if not any(term in anchor for term in UNSAFE_ART_IDENTITY_TERMS)
    ]
    art = {
        "owner_visibility": visibility,
        "owner_position": _clean_text(raw_art.get("owner_position"), 60),
        "owner_action": _clean_text(raw_art.get("owner_action"), 100),
        "owner_expression": _clean_text(raw_art.get("owner_expression"), 60),
        "owner_clothing": _clean_list(raw_art.get("owner_clothing"), limit=4, item_limit=50),
        "owner_headwear": _clean_list(raw_art.get("owner_headwear"), limit=4, item_limit=40),
        "other_people": _clean_list(raw_art.get("other_people"), limit=5, item_limit=80),
        "scene": _clean_text(raw_art.get("scene"), 60),
        "mood": _clean_text(raw_art.get("mood"), 40),
        "lighting": _clean_text(raw_art.get("lighting"), 80),
        "distinctive_anchors": distinctive_anchors,
    }
    quality_flags: list[str] = []
    if visibility == "unclear":
        quality_flags.append("owner_unclear")
    if not art["owner_position"]:
        quality_flags.append("owner_position_missing")
    if not (art["owner_action"] or art["owner_clothing"] or art["owner_headwear"]):
        quality_flags.append("owner_detail_missing")
    if len(art["distinctive_anchors"]) < 2:
        quality_flags.append("distinctive_anchors_insufficient")
    status = "rejected" if visibility == "unclear" else ("verified" if not quality_flags else "needs_review")
    return {
        **art,
        "summary": _build_art_summary(character_name, art),
        "tags": art["distinctive_anchors"],
        "status": status,
        "source": "vision_model",
        "quality_flags": quality_flags,
    }


def _normalize_hairstyle(raw: object) -> dict:
    raw_hairstyle = raw if isinstance(raw, dict) else {}
    clarity = _clean_text(raw_hairstyle.get("clarity"), 16).lower()
    if clarity not in {"clear", "partial", "unclear"}:
        clarity = "unclear"
    raw_structure = raw_hairstyle.get("structure")
    raw_structure = raw_structure if isinstance(raw_structure, dict) else {}
    structure = {
        key: _clean_text(raw_structure.get(key), 80)
        for key in ("length", "silhouette", "bangs", "side_locks", "tied_part", "rear_shape", "streaks")
    }
    hair_accessories = _clean_list(raw_hairstyle.get("hair_accessories"), limit=5, item_limit=40)
    headwear = _clean_list(raw_hairstyle.get("headwear"), limit=5, item_limit=40)
    labels = {
        "length": "长度",
        "silhouette": "整体",
        "bangs": "刘海",
        "side_locks": "侧发",
        "tied_part": "束发",
        "rear_shape": "后发",
        "streaks": "挑染",
    }
    description_parts = [f"{labels[key]}：{value}" for key, value in structure.items() if value]
    if hair_accessories:
        description_parts.append(f"发饰：{'、'.join(hair_accessories)}")
    description = "；".join(description_parts)[:300]
    quality_flags: list[str] = []
    if clarity == "unclear":
        quality_flags.append("owner_or_hair_unclear")
    owner_location = _clean_text(raw_hairstyle.get("owner_location"), 80)
    if not owner_location:
        quality_flags.append("owner_location_missing")
    structural_features = [value for value in structure.values() if value]
    if len(structural_features) < 2:
        quality_flags.append("hair_structure_insufficient")
    lighting_effect = _clean_text(raw_hairstyle.get("lighting_effect"), 100)
    if lighting_effect:
        quality_flags.append("observed_color_affected_by_lighting")
    return {
        "available": True,
        "description": description,
        "features": [*structural_features, *hair_accessories][:8],
        "source_image": "trained",
        "visible_in": "trained",
        "clarity": clarity,
        "owner_location": owner_location,
        "structure": structure,
        "hair_accessories": hair_accessories,
        "headwear": headwear,
        "observed_color": _clean_text(raw_hairstyle.get("observed_color"), 60),
        "lighting_effect": lighting_effect,
        "quality_flags": quality_flags,
        "status": "needs_review",
        "source": "vision_model",
    }


def _build_vision_prompt(card: dict) -> str:
    character_name = str(card["character_name"])
    identity_hint = NORMAL_IDENTITY_HINTS.get(character_name, "")
    return (
        VISION_PROMPT.replace("__CHARACTER_NAME__", character_name)
        .replace("__NORMAL_IDENTITY_HINT__", identity_hint)
    )


def _build_hairstyle_prompt(card: dict) -> str:
    return HAIRSTYLE_PROMPT.replace("__CHARACTER_NAME__", str(card["character_name"]))


def _is_transient_vision_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TRANSIENT_VISION_ERRORS)


async def _request_vision_description(
    client: Any,
    *,
    normal_data: bytes,
    trained_data: bytes,
    vision_model: str,
    retries: int,
    retry_delay: float,
    prompt: str,
    thinking: str = "disabled",
    max_tokens: int = 1400,
) -> Any:
    """调用视觉模型；仅对限流、超时和服务端错误做指数退避。"""
    for attempt in range(max(0, retries) + 1):
        try:
            return await client.chat.completions.create(
                model=vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "text", "text": "第一张图片：花前"},
                            {"type": "image_url", "image_url": {"url": _image_data_url(normal_data)}},
                            {"type": "text", "text": "第二张图片：花后"},
                            {"type": "image_url", "image_url": {"url": _image_data_url(trained_data)}},
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                timeout=VISION_REQUEST_TIMEOUT,
                extra_body={"thinking": {"type": thinking}},
            )
        except Exception as exc:
            if attempt >= max(0, retries) or not _is_transient_vision_error(exc):
                raise
            delay = max(0.0, retry_delay) * (2**attempt)
            print(f"  视觉服务暂时繁忙，{delay:g} 秒后重试（{attempt + 1}/{retries}）", flush=True)
            await asyncio.sleep(delay)
    raise RuntimeError("视觉模型重试流程异常结束")


def _parse_vision_json(response: Any) -> dict:
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("视觉模型没有返回 JSON 对象")
    return parsed


def _art_generation_complete(card: dict) -> bool:
    generation = card.get("generation") if isinstance(card.get("generation"), dict) else {}
    schema_current = int(generation.get("schema_version", 0)) >= VISION_SCHEMA_VERSION
    art_status = str(generation.get("art_status") or generation.get("status") or "")
    return (
        schema_current
        and art_status == "complete"
        and isinstance(card.get("normal_art"), dict)
        and isinstance(card.get("trained_art"), dict)
    )


def _hairstyle_generation_complete(card: dict) -> bool:
    hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
    if not hairstyle.get("available", False):
        return True
    if hairstyle.get("source") == "manual_review" or hairstyle.get("status") == "reviewed":
        return True
    generation = card.get("generation") if isinstance(card.get("generation"), dict) else {}
    return (
        int(generation.get("schema_version", 0)) >= VISION_SCHEMA_VERSION
        and generation.get("hairstyle_status") == "complete"
        and hairstyle.get("source") == "vision_model"
    )


def _card_needs_enrichment(card: dict) -> bool:
    return not (_art_generation_complete(card) and _hairstyle_generation_complete(card))


def _card_review_reasons(card: dict) -> list[str]:
    generation = card.get("generation") if isinstance(card.get("generation"), dict) else {}
    if generation.get("status") == "failed":
        return ["art_generation_failed"]
    reasons: list[str] = []
    for key, label in (("normal_art", "normal_art"), ("trained_art", "trained_art")):
        art = card.get(key) if isinstance(card.get(key), dict) else {}
        status = str(art.get("status") or "missing")
        if status not in {"verified", "reviewed"}:
            reasons.append(f"{label}_{status}")
    hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
    if hairstyle.get("available", False) and hairstyle.get("status") != "reviewed":
        reasons.append(f"hairstyle_{hairstyle.get('status') or 'missing'}")
    return reasons


async def _enrich_card(
    card: dict,
    *,
    semaphore: asyncio.Semaphore,
    session: Any,
    client: Any,
    images_dir: Path | None,
    asset_url_template: str,
    vision_model: str,
    retries: int,
    retry_delay: float,
) -> tuple[dict, str]:
    async with semaphore:
        try:
            assetbundle = card["assetbundle_name"]
            normal_data, trained_data = await asyncio.gather(
                _load_image(session, assetbundle, "card_normal", images_dir, asset_url_template),
                _load_image(session, assetbundle, "card_after_training", images_dir, asset_url_template),
            )
            if not _art_generation_complete(card):
                response = await _request_vision_description(
                    client,
                    normal_data=normal_data,
                    trained_data=trained_data,
                    vision_model=vision_model,
                    retries=retries,
                    retry_delay=retry_delay,
                    prompt=_build_vision_prompt(card),
                )
                parsed = _parse_vision_json(response)
                character_name = str(card["character_name"])
                card["normal_art"] = _normalize_art(parsed.get("normal_art"), character_name)
                card["trained_art"] = _normalize_art(parsed.get("trained_art"), character_name)

            hairstyle_error = ""
            hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
            if (
                hairstyle.get("available", False)
                and hairstyle.get("source") != "manual_review"
                and not _hairstyle_generation_complete(card)
            ):
                try:
                    hairstyle_response = await _request_vision_description(
                        client,
                        normal_data=normal_data,
                        trained_data=trained_data,
                        vision_model=vision_model,
                        retries=retries,
                        retry_delay=retry_delay,
                        prompt=_build_hairstyle_prompt(card),
                        thinking="enabled",
                        max_tokens=1000,
                    )
                    card["hairstyle"] = _normalize_hairstyle(_parse_vision_json(hairstyle_response))
                except Exception as exc:
                    hairstyle_error = str(exc)[:300]
                    card["hairstyle"] = {
                        **_hairstyle_metadata(str(card.get("supply_type") or "unknown")),
                        "status": "failed",
                        "source": "vision_model",
                        "error": hairstyle_error,
                    }

            hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
            if not hairstyle.get("available", False):
                hairstyle_status = "not_applicable"
            elif hairstyle.get("source") == "manual_review":
                hairstyle_status = "reviewed"
            elif hairstyle_error:
                hairstyle_status = "failed"
            else:
                hairstyle_status = "complete"

            review_reasons = _card_review_reasons(card)
            art_statuses = {
                str(card.get(key, {}).get("status") or "missing")
                for key in ("normal_art", "trained_art")
                if isinstance(card.get(key), dict)
            }
            if "rejected" in art_statuses:
                quality_status = "rejected"
            elif review_reasons:
                quality_status = "needs_review"
            else:
                quality_status = "verified"
            card["generation"] = {
                "status": "partial" if hairstyle_error else "complete",
                "art_status": "complete",
                "hairstyle_status": hairstyle_status,
                "quality_status": quality_status,
                "review_reasons": review_reasons,
                "model": vision_model,
                "schema_version": VISION_SCHEMA_VERSION,
                "error": hairstyle_error,
            }
            return card, ",".join(review_reasons)
        except Exception as exc:
            generation = card.get("generation") if isinstance(card.get("generation"), dict) else {}
            card["generation"] = {
                "status": "failed",
                "art_status": "failed",
                "hairstyle_status": generation.get("hairstyle_status", "pending"),
                "quality_status": "rejected",
                "review_reasons": ["art_generation_failed"],
                "model": vision_model,
                "schema_version": VISION_SCHEMA_VERSION,
                "error": str(exc)[:300],
            }
            return card, "failed"


async def enrich_cards(
    cards: list[dict],
    *,
    output_path: Path,
    review_path: Path,
    images_dir: Path | None,
    asset_url_template: str,
    concurrency: int,
    limit: int | None,
    vision_model: str,
    retries: int,
    retry_delay: float,
    card_selectors: list[str] | None,
) -> None:
    """批量识图并逐张写检查点；已 complete 的条目自动跳过。"""
    import aiohttp
    from openai import AsyncOpenAI

    api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key or "请在这里" in api_key:
        raise RuntimeError("未配置有效的 ZHIPU_API_KEY")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        timeout=VISION_REQUEST_TIMEOUT,
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    pending = [
        card for card in cards
        if _card_needs_enrichment(card)
    ]
    if card_selectors:
        normalized_selectors = {selector.strip().casefold() for selector in card_selectors if selector.strip()}
        pending = [
            card
            for card in pending
            if str(card.get("id", "")).casefold() in normalized_selectors
            or str(card.get("sequence_alias", "")).casefold() in normalized_selectors
        ]
        if not pending:
            raise RuntimeError(f"没有找到待生成的指定卡面：{', '.join(card_selectors)}")
    if limit is not None:
        pending = pending[:limit]

    timeout = aiohttp.ClientTimeout(total=60)
    connector = aiohttp.TCPConnector(limit=max(2, concurrency * 2))
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for start in range(0, len(pending), max(1, concurrency)):
            batch = pending[start:start + max(1, concurrency)]
            results = await asyncio.gather(
                *[
                    _enrich_card(
                        card,
                        semaphore=semaphore,
                        session=session,
                        client=client,
                        images_dir=images_dir,
                        asset_url_template=asset_url_template,
                        vision_model=vision_model,
                        retries=retries,
                        retry_delay=retry_delay,
                    )
                    for card in batch
                ]
            )
            for card, _review_reason in results:
                print(
                    f"[{card['sequence_alias']}] {card['title']} -> {card['generation']['status']}"
                    f" / {card['generation']['quality_status']}",
                    flush=True,
                )
            _atomic_write_json(output_path, {"version": 1, "scope": "vbs_rarity_4", "cards": cards})
            review = [
                {
                    "id": item["id"],
                    "sequence_alias": item["sequence_alias"],
                    "reason": ",".join(_card_review_reasons(item)),
                }
                for item in cards
                if item.get("generation", {}).get("status") != "metadata_only"
                and _card_review_reasons(item)
            ]
            _atomic_write_json(review_path, {"version": 1, "items": review})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 VBS 四星卡面知识库")
    parser.add_argument("--master-dir", type=Path, default=Path("tmp_pjsk"))
    parser.add_argument("--output", type=Path, default=Path("data/content/pjsk_cards.json"))
    parser.add_argument("--review-output", type=Path, default=Path("data/card_catalog_review.json"))
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("data/content/pjsk_card_reviews.json"),
        help="人工审核覆盖文件；不会被构建器写入",
    )
    parser.add_argument("--images-dir", type=Path)
    parser.add_argument("--asset-url-template", default=DEFAULT_ASSET_URL)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--card",
        action="append",
        dest="card_selectors",
        help="只生成指定卡 ID 或角色序号；可重复传入，例如 --card 917 --card 彰21",
    )
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--vision-model", default=VISION_MODEL)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=3.0)
    return parser.parse_args()


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    cards = build_metadata(args.master_dir)
    if args.resume and args.output.exists():
        cards = merge_resume_metadata(cards, _read_json(args.output))
    if args.overrides.exists():
        cards = apply_review_overrides(cards, _read_json(args.overrides))
    payload = {"version": 1, "scope": "vbs_rarity_4", "cards": cards}
    _atomic_write_json(args.output, payload)
    print(f"✅ 已生成元数据: {len(cards)} 张 -> {args.output}")
    if not args.metadata_only:
        from dotenv import load_dotenv

        load_dotenv()
        asyncio.run(
            enrich_cards(
                cards,
                output_path=args.output,
                review_path=args.review_output,
                images_dir=args.images_dir,
                asset_url_template=args.asset_url_template,
                concurrency=args.concurrency,
                limit=args.limit,
                vision_model=args.vision_model,
                retries=args.retries,
                retry_delay=args.retry_delay,
                card_selectors=args.card_selectors,
            )
        )


if __name__ == "__main__":
    main()

"""VBS 四星卡面 master-data 构建器测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from tools import build_card_catalog


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_metadata_filters_vbs_four_stars_and_assigns_stable_sequences(tmp_path):
    _write(
        tmp_path / "cards.json",
        [
            {"id": 3, "characterId": 11, "cardRarityType": "rarity_4", "prefix": "第三张", "releaseAt": 200, "cardSupplyId": 3, "assetbundleName": "c"},
            {"id": 1, "characterId": 11, "cardRarityType": "rarity_4", "prefix": "第一张", "releaseAt": 100, "cardSupplyId": 1, "assetbundleName": "a"},
            {"id": 2, "characterId": 12, "cardRarityType": "rarity_4", "prefix": "冬弥第一张", "releaseAt": 100, "cardSupplyId": 4, "assetbundleName": "b"},
            {"id": 4, "characterId": 11, "cardRarityType": "rarity_3", "prefix": "三星", "releaseAt": 300, "cardSupplyId": 1, "assetbundleName": "d"},
            {"id": 5, "characterId": 1, "cardRarityType": "rarity_4", "prefix": "非VBS", "releaseAt": 100, "cardSupplyId": 1, "assetbundleName": "e"},
        ],
    )
    _write(tmp_path / "events.json", [{"id": 7, "name": "出处活动", "startAt": 101}])
    _write(tmp_path / "eventCards.json", [{"cardId": 1, "eventId": 7, "isDisplayCardStory": True}])
    _write(
        tmp_path / "cardSupplies.json",
        [
            {"id": 1, "cardSupplyType": "normal"},
            {"id": 3, "cardSupplyType": "term_limited"},
            {"id": 4, "cardSupplyType": "colorful_festival_limited"},
        ],
    )
    _write(tmp_path / "eventMusics.json", [{"eventId": 7, "musicId": 99}])
    _write(tmp_path / "musics.json", [{"id": 99, "title": "测试书下曲", "isNewlyWrittenMusic": True}])
    _write(
        tmp_path / "gachas.json",
        [
            {
                "id": 8,
                "name": "测试限定池",
                "gachaType": "ceil",
                "startAt": 100,
                "gachaPickups": [{"cardId": 1}, {"cardId": 2}],
            }
        ],
    )

    cards = build_card_catalog.build_metadata(tmp_path)

    assert [card["id"] for card in cards] == [1, 2, 3]
    assert [card["sequence_alias"] for card in cards] == ["彰1", "冬1", "彰2"]
    assert cards[0]["event_name"] == "出处活动"
    assert cards[0]["commissioned_song"] == "测试书下曲"
    assert "测试书下曲" not in cards[0]["aliases"]
    assert cards[0]["initial_gacha"]["name"] == "测试限定池"
    assert cards[0]["hairstyle"]["available"] is False
    assert cards[1]["supply_label"] == "FES限定"
    assert cards[1]["hairstyle"]["available"] is True
    assert cards[2]["supply_label"] == "期间限定"


def test_merge_resume_preserves_art_but_requeues_old_visual_schema():
    fresh = [{"id": 1, "title": "新标题", "normal_art": None, "trained_art": None, "generation": {"status": "metadata_only"}}]
    old = {
        "cards": [
            {
                "id": 1,
                "title": "旧标题",
                "normal_art": {"summary": "花前"},
                "trained_art": {"summary": "花后"},
                "generation": {"status": "complete"},
            }
        ]
    }

    merged = build_card_catalog.merge_resume_metadata(fresh, old)

    assert merged[0]["title"] == "新标题"
    assert merged[0]["normal_art"] == {"summary": "花前"}
    assert merged[0]["generation"]["status"] == "metadata_only"


def test_merge_resume_preserves_current_visual_schema():
    fresh = [
        {
            "id": 1,
            "normal_art": None,
            "trained_art": None,
            "hairstyle": {"available": True, "status": "pending"},
            "generation": {"status": "metadata_only", "schema_version": build_card_catalog.VISION_SCHEMA_VERSION},
        }
    ]
    old = {
        "cards": [
            {
                "id": 1,
                "normal_art": {"summary": "花前"},
                "trained_art": {"summary": "花后"},
                "hairstyle": {"available": True, "description": "侧编发", "status": "complete"},
                "generation": {
                    "status": "complete",
                    "schema_version": build_card_catalog.VISION_SCHEMA_VERSION,
                },
            }
        ]
    }

    merged = build_card_catalog.merge_resume_metadata(fresh, old)

    assert merged[0]["hairstyle"]["description"] == "侧编发"
    assert merged[0]["generation"]["status"] == "complete"


def test_vision_prompt_replaces_identity_without_formatting_json_schema():
    prompt = build_card_catalog._build_vision_prompt(
        {"character_name": "青柳冬弥", "hairstyle": {"available": True}}
    )

    assert "青柳冬弥" in prompt
    assert "深蓝与浅灰分区发色" in prompt
    assert "花后可能更换发型" in prompt
    assert '"normal_art": {' in prompt
    assert '"confidence":' not in prompt
    assert "__CHARACTER_NAME__" not in prompt


def test_hairstyle_prompt_uses_trained_art_without_reclassifying_identity():
    prompt = build_card_catalog._build_hairstyle_prompt({"character_name": "小豆泽心羽"})

    assert "第二张图片是花后" in prompt
    assert "不要重新判断角色" in prompt
    assert "环境光" in prompt
    assert '"headwear"' in prompt


def test_normalize_art_builds_owner_relative_summary_and_quality_gate():
    result = build_card_catalog._normalize_art(
        {
            "owner_visibility": "clear",
            "owner_position": "右侧前景",
            "owner_action": "与左侧人物碰拳",
            "owner_clothing": ["深色外套"],
            "scene": "黄昏街道",
            "distinctive_anchors": ["碰拳", "黄昏街道", "黄色连帽衫", "蓝色长发"],
        },
        "青柳冬弥",
    )

    assert result["status"] == "verified"
    assert result["summary"].startswith("青柳冬弥位于右侧前景")
    assert result["tags"] == ["碰拳", "黄昏街道", "黄色连帽衫"]


def test_normalize_art_rejects_unlocated_owner():
    result = build_card_catalog._normalize_art(
        {
            "owner_visibility": "unclear",
            "other_people": ["左侧一名男性", "右侧一名男性"],
            "distinctive_anchors": ["后台", "舞台设备"],
        },
        "东云彰人",
    )

    assert result["status"] == "rejected"
    assert "无法可靠定位" in result["summary"]
    assert "owner_unclear" in result["quality_flags"]


def test_normalize_hairstyle_uses_trained_structure_and_separates_lighting():
    result = build_card_catalog._normalize_hairstyle(
        {
            "owner_location": "中央前景",
            "clarity": "clear",
            "structure": {
                "length": "后颈长度",
                "silhouette": "蓬松后掠",
                "bangs": "中分长刘海",
                "tied_part": "无束发",
            },
            "headwear": ["猫耳帽"],
            "observed_color": "偏紫蓝",
            "lighting_effect": "红紫色环境光明显",
        }
    )

    assert result["source_image"] == "trained"
    assert result["visible_in"] == "trained"
    assert result["status"] == "needs_review"
    assert "猫耳帽" not in result["description"]
    assert result["headwear"] == ["猫耳帽"]
    assert "偏紫蓝" not in result["features"]
    assert "observed_color_affected_by_lighting" in result["quality_flags"]


def test_failed_art_generation_has_specific_review_reason():
    card = {
        "normal_art": None,
        "trained_art": None,
        "hairstyle": {"available": True, "status": "pending"},
        "generation": {"status": "failed"},
    }

    assert build_card_catalog._card_review_reasons(card) == ["art_generation_failed"]


def test_manual_review_override_replaces_generated_hairstyle():
    cards = [
        {
            "id": 917,
            "normal_art": {"status": "verified"},
            "trained_art": {"status": "verified"},
            "hairstyle": {
                "available": True,
                "description": "模型误报",
                "structure": {"length": "短发"},
                "headwear": ["帽子"],
                "status": "generated",
                "source": "vision_model",
            },
            "generation": {
                "status": "complete",
                "art_status": "complete",
                "hairstyle_status": "complete",
                "quality_status": "needs_review",
                "review_reasons": ["hairstyle_needs_review"],
                "error": "",
            },
        }
    ]
    overrides = {
        "cards": {
            "917": {
                "hairstyle": {
                    "description": "蓝灰双色短发，无束发",
                    "features": ["蓝灰双色", "无束发"],
                    "review_note": "人工核对",
                }
            }
        }
    }

    result = build_card_catalog.apply_review_overrides(cards, overrides)

    assert result[0]["hairstyle"]["description"] == "蓝灰双色短发，无束发"
    assert result[0]["hairstyle"]["source"] == "manual_review"
    assert result[0]["hairstyle"]["status"] == "reviewed"
    assert result[0]["hairstyle"]["structure"] == {"length": "短发"}
    assert result[0]["hairstyle"]["headwear"] == ["帽子"]
    assert result[0]["generation"]["hairstyle_status"] == "reviewed"
    assert result[0]["generation"]["quality_status"] == "verified"
    assert result[0]["generation"]["review_reasons"] == []


async def test_vision_request_retries_transient_provider_error():
    response = SimpleNamespace(choices=[])
    create = mock.AsyncMock(side_effect=[RuntimeError("429 访问量过大"), response])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    result = await build_card_catalog._request_vision_description(
        client,
        normal_data=b"normal",
        trained_data=b"trained",
        vision_model="test-model",
        retries=2,
        retry_delay=0,
        prompt="test prompt",
    )

    assert result is response
    assert create.await_count == 2


async def test_vision_request_does_not_retry_invalid_response_error():
    create = mock.AsyncMock(side_effect=ValueError("invalid JSON request"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with mock.patch.object(build_card_catalog.asyncio, "sleep", new=mock.AsyncMock()) as sleep:
        try:
            await build_card_catalog._request_vision_description(
                client,
                normal_data=b"normal",
                trained_data=b"trained",
                vision_model="test-model",
                retries=2,
                retry_delay=0,
                prompt="test prompt",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-transient errors must be raised")

    assert create.await_count == 1
    sleep.assert_not_awaited()


async def test_limited_card_runs_separate_hairstyle_pass_and_requires_review():
    art_payload = {
        key: {
            "owner_visibility": "clear",
            "owner_position": "中央前景",
            "owner_action": "手持麦克风",
            "owner_clothing": ["舞台服装"],
            "scene": "舞台",
            "distinctive_anchors": ["麦克风", "红色灯光", "金属舞台架"],
        }
        for key in ("normal_art", "trained_art")
    }
    hairstyle_payload = {
        "owner_location": "中央前景",
        "clarity": "clear",
        "structure": {
            "length": "后颈长度",
            "silhouette": "蓬松后掠",
            "bangs": "斜落长刘海",
            "tied_part": "无束发",
        },
        "observed_color": "暖橙色",
        "lighting_effect": "红色舞台光明显",
    }

    def response(payload):
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    card = {
        "id": 1,
        "character_name": "东云彰人",
        "sequence_alias": "彰1",
        "title": "测试限定",
        "assetbundle_name": "asset",
        "supply_type": "term_limited",
        "hairstyle": build_card_catalog._hairstyle_metadata("term_limited"),
        "normal_art": None,
        "trained_art": None,
        "generation": {
            "status": "metadata_only",
            "schema_version": build_card_catalog.VISION_SCHEMA_VERSION,
        },
    }
    request = mock.AsyncMock(side_effect=[response(art_payload), response(hairstyle_payload)])
    with (
        mock.patch.object(build_card_catalog, "_load_image", new=mock.AsyncMock(return_value=b"image")),
        mock.patch.object(build_card_catalog, "_request_vision_description", new=request),
    ):
        result, review_reason = await build_card_catalog._enrich_card(
            card,
            semaphore=asyncio.Semaphore(1),
            session=None,
            client=None,
            images_dir=None,
            asset_url_template="",
            vision_model="test-model",
            retries=0,
            retry_delay=0,
        )

    assert request.await_count == 2
    assert request.await_args_list[1].kwargs["thinking"] == "enabled"
    assert result["normal_art"]["status"] == "verified"
    assert result["hairstyle"]["source_image"] == "trained"
    assert result["hairstyle"]["status"] == "needs_review"
    assert result["generation"]["status"] == "complete"
    assert result["generation"]["quality_status"] == "needs_review"
    assert review_reason == "hairstyle_needs_review"

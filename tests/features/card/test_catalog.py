"""结构化 PJSK 卡面库的实体解析、澄清与别称维护测试。"""

from __future__ import annotations

import json
from unittest import mock

from nonebot_plugin_akito.core.retrieval import RetrievalResult
from nonebot_plugin_akito.features.card import catalog as card_catalog
from nonebot_plugin_akito.features.card.retrieval import card_retrieval_text


def _cards() -> list[dict]:
    return [
        {
            "id": 136,
            "character_name": "东云彰人",
            "sequence_alias": "彰1",
            "title": "相棒だから",
            "aliases": ["彰1", "彰人1", "相棒だから"],
            "event_name": "いつか、背中あわせのリリックを",
            "supply_label": "常驻",
            "release_at": 1,
            "normal_art": None,
            "trained_art": None,
        },
        {
            "id": 500,
            "character_name": "东云彰人",
            "sequence_alias": "彰10",
            "title": "かけてくれた言葉の理由",
            "aliases": ["彰10", "彰人10", "かけてくれた言葉の理由"],
            "event_name": "Find A Way Out",
            "supply_label": "常驻",
            "release_at": 10,
            "normal_art": {"summary": "练习室里侧身站着。", "tags": ["练习室"], "status": "verified"},
            "trained_art": {
                "summary": "橙红灯光下握着麦克风。",
                "tags": ["火焰", "麦克风"],
                "status": "verified",
            },
        },
        {
            "id": 700,
            "character_name": "青柳冬弥",
            "sequence_alias": "冬10",
            "title": "同名卡",
            "aliases": ["冬10", "同名卡"],
            "event_name": "测试活动",
            "supply_label": "期间限定",
            "release_at": 11,
        },
        {
            "id": 701,
            "character_name": "白石杏",
            "sequence_alias": "杏10",
            "title": "同名卡",
            "aliases": ["杏10", "同名卡"],
            "event_name": "测试活动二",
            "supply_label": "常驻",
            "release_at": 12,
        },
    ]


def test_init_card_catalog_loads_scoped_aliases_and_notes():
    snapshot = {
        "CARD_DB": list(card_catalog.CARD_DB),
        "CARD_ALIASES": dict(card_catalog.CARD_ALIASES),
        "CARD_ALIAS_NOTES": dict(card_catalog.CARD_ALIAS_NOTES),
        "CARD_GROUP_ALIASES": dict(card_catalog.CARD_GROUP_ALIASES),
        "CARD_GROUP_ALIAS_NOTES": dict(card_catalog.CARD_GROUP_ALIAS_NOTES),
    }

    def fake_optional(name: str):
        if name == "pjsk_cards.json":
            return {"cards": [{"id": 915}, {"id": 916}, {"id": 1059}]}
        if name == "pjsk_card_aliases.json":
            return {
                "card_aliases": {"老蛇": {"card_id": 1059, "note": "眼神像蛇"}},
                "group_aliases": {"烈火": {"card_ids": [915, 916], "note": "同期限定"}},
            }
        return None

    try:
        with mock.patch.object(card_catalog, "_load_optional_json", side_effect=fake_optional):
            card_catalog.init_card_catalog()

        assert card_catalog.CARD_ALIASES == {"老蛇": 1059}
        assert card_catalog.CARD_ALIAS_NOTES == {"老蛇": "眼神像蛇"}
        assert card_catalog.CARD_GROUP_ALIASES == {"烈火": [915, 916]}
        assert card_catalog.CARD_GROUP_ALIAS_NOTES == {"烈火": "同期限定"}
    finally:
        card_catalog.CARD_DB[:] = snapshot["CARD_DB"]
        card_catalog.CARD_ALIASES.clear()
        card_catalog.CARD_ALIASES.update(snapshot["CARD_ALIASES"])
        card_catalog.CARD_ALIAS_NOTES.clear()
        card_catalog.CARD_ALIAS_NOTES.update(snapshot["CARD_ALIAS_NOTES"])
        card_catalog.CARD_GROUP_ALIASES.clear()
        card_catalog.CARD_GROUP_ALIASES.update(snapshot["CARD_GROUP_ALIASES"])
        card_catalog.CARD_GROUP_ALIAS_NOTES.clear()
        card_catalog.CARD_GROUP_ALIAS_NOTES.update(snapshot["CARD_GROUP_ALIAS_NOTES"])


def test_normalize_card_alias_handles_width_case_and_brackets():
    assert card_catalog.normalize_card_alias(" 《ＡＢＣ 彰１０》 ") == "abc彰10"


def test_resolve_sequence_alias_prefers_longest_match():
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
    ):
        result = card_catalog.resolve_card_mentions("彰10是哪张卡")

    assert result.status == "hit"
    assert result.cards[0]["id"] == 500


def test_resolve_duplicate_official_title_is_ambiguous():
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
    ):
        result = card_catalog.resolve_card_mentions("同名卡的花后")

    assert result.status == "ambiguous"
    assert {card["id"] for card in result.cards} == {700, 701}


def test_manual_alias_overrides_to_unique_card():
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {"烈火": 500}),
    ):
        result = card_catalog.resolve_card_mentions("烈火是哪张卡")

    assert result.status == "hit"
    assert result.cards[0]["sequence_alias"] == "彰10"


async def test_manual_single_card_alias_keeps_note_and_unique_identity():
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {"老蛇": 500}),
        mock.patch.object(card_catalog, "CARD_ALIAS_NOTES", {"老蛇": "因花后眼神像蛇"}),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIASES", {}),
    ):
        result = await card_catalog.get_relevant_cards("老蛇是什么发型")

    assert "彰10" in result
    assert "因花后眼神像蛇" in result
    assert "同名" not in result


async def test_manual_group_alias_lists_members_and_character_narrows():
    cards = _cards()
    cards[1]["character_id"] = 11
    cards[2]["character_id"] = 12
    cards[3]["character_id"] = 10
    with (
        mock.patch.object(card_catalog, "CARD_DB", cards),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIASES", {"烈火": [500, 700, 701]}),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIAS_NOTES", {"烈火": "指烈火活动同期限定"}),
    ):
        group_result = await card_catalog.get_relevant_cards("烈火是哪几张")
        toya_result = await card_catalog.get_relevant_cards("冬弥烈火头是什么样")

    assert "指向一组同期卡" in group_result
    assert all(alias in group_result for alias in ("彰10", "冬10", "杏10"))
    assert "冬10" in toya_result
    assert "彰10" not in toya_result
    assert "杏10" not in toya_result


async def test_unknown_alias_prompts_clarification_before_semantic_retrieval():
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
        mock.patch.object(card_catalog, "retrieve_result", new=mock.AsyncMock()) as retrieve_mock,
    ):
        result = await card_catalog.get_relevant_cards("烈火是哪张卡")

    assert "未知卡面别称" in result
    assert "烈火" in result
    retrieve_mock.assert_not_awaited()


async def test_description_query_uses_cards_retrieval():
    retrieval = RetrievalResult(status="hit", ids=[1], used_query="有火焰的彰人卡面")
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
        mock.patch.object(card_catalog, "retrieve_result", new=mock.AsyncMock(return_value=retrieval)),
    ):
        result = await card_catalog.get_relevant_cards("有火焰的彰人卡面是哪张")

    assert "可能相关的卡面事实" in result
    assert "彰10" in result
    assert "橙红灯光" in result


def test_runtime_fact_hides_unreviewed_visual_draft():
    card = _cards()[1]
    card["trained_art"] = {
        "summary": "错误地描述成其他人物。",
        "status": "needs_review",
    }
    card["hairstyle"] = {
        "available": True,
        "description": "未经确认的错误发型",
        "status": "needs_review",
        "source": "vision_model",
    }

    result = card_catalog.render_card_fact(card)

    assert "错误地描述成其他人物" not in result
    assert "未经确认的错误发型" not in result
    assert "自动识别初稿待人工复核" in result


def test_card_embedding_text_only_includes_trusted_visual_fields():
    card = _cards()[1]
    card["normal_art"]["status"] = "needs_review"
    card["trained_art"]["distinctive_anchors"] = ["红色逆光", "麦克风"]

    result = card_retrieval_text(card)

    assert "练习室里侧身站着" not in result
    assert "橙红灯光下握着麦克风" in result
    assert "红色逆光" in result


def test_bind_and_unbind_alias_write_atomic_payload(tmp_path):
    aliases: dict[str, int] = {}
    alias_path = tmp_path / "content" / "pjsk_card_aliases.json"
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", aliases),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIASES", {}),
        mock.patch.object(card_catalog, "_alias_file_path", return_value=alias_path),
        mock.patch.object(card_catalog, "init_card_catalog"),
    ):
        ok, _message = card_catalog.bind_card_alias("烈火", "彰10")
        assert ok is True
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
        assert payload["version"] == 2
        assert payload["card_aliases"] == {"烈火": {"card_id": 500}}

        ok, _message = card_catalog.unbind_card_alias("烈火")
        assert ok is True
        assert json.loads(alias_path.read_text(encoding="utf-8"))["card_aliases"] == {}


def test_bind_alias_rejects_conflicting_manual_binding(tmp_path):
    alias_path = tmp_path / "content" / "pjsk_card_aliases.json"
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {"烈火": 136}),
        mock.patch.object(card_catalog, "_alias_file_path", return_value=alias_path),
    ):
        ok, message = card_catalog.bind_card_alias("烈火", "彰10")

    assert ok is False
    assert "先解绑" in message


def test_bind_group_alias_writes_separate_scope_and_note(tmp_path):
    alias_path = tmp_path / "content" / "pjsk_card_aliases.json"
    with (
        mock.patch.object(card_catalog, "CARD_DB", _cards()),
        mock.patch.object(card_catalog, "CARD_ALIASES", {}),
        mock.patch.object(card_catalog, "CARD_ALIAS_NOTES", {}),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIASES", {}),
        mock.patch.object(card_catalog, "CARD_GROUP_ALIAS_NOTES", {}),
        mock.patch.object(card_catalog, "_alias_file_path", return_value=alias_path),
        mock.patch.object(card_catalog, "init_card_catalog"),
    ):
        ok, _message = card_catalog.bind_card_group_alias(
            "烈火",
            ["彰10", "冬10", "杏10"],
            note="指烈火活动同期限定",
        )

    assert ok is True
    payload = json.loads(alias_path.read_text(encoding="utf-8"))
    assert payload["card_aliases"] == {}
    assert payload["group_aliases"]["烈火"] == {
        "card_ids": [500, 700, 701],
        "note": "指烈火活动同期限定",
    }

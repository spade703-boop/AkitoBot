"""测试 core/data.py 中的真实数据加载与热重载辅助函数。"""

from __future__ import annotations

import copy
import sys
import types
from unittest import mock

import nonebot_plugin_akito.core.data as data


def test_load_json_file_reads_utf8_sig(tmp_path):
    path = tmp_path / "sample.json"
    path.write_text('{"name": "测试"}', encoding="utf-8-sig")

    with mock.patch.object(data, "_find_data_path", return_value=path):
        result = data.load_json_file("sample.json", {})

    assert result == {"name": "测试"}


def test_load_json_file_returns_default_when_json_is_invalid(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    with mock.patch.object(data, "_find_data_path", return_value=path):
        result = data.load_json_file("broken.json", {"fallback": True})

    assert result == {"fallback": True}


def test_load_prompt_template_returns_text_when_found(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_text("你是东云彰人。", encoding="utf-8")

    with mock.patch.object(data, "_find_data_path", return_value=path):
        result = data.load_prompt_template("prompt.txt")

    assert result == "你是东云彰人。"


def test_load_reactions_merges_optional_sources():
    def fake_optional(name: str):
        mapping = {
            "akito_reactions.json": {"legacy": 1},
            "gallery_text.json": {"save_img_replies": {"self": ["收到"]}},
            "greetings.json": {"greetings": {"morning": ["早"], "night": ["晚安"]}},
        }
        return mapping.get(name)

    with mock.patch.object(data, "_load_optional_json", side_effect=fake_optional):
        result = data._load_reactions()

    assert result["legacy"] == 1
    assert result["save_img_replies"]["self"] == ["收到"]
    assert result["greetings"]["morning"] == ["早"]
    assert result["send_img_angles"]


def test_load_prompts_merges_split_files_before_defaults():
    def fake_optional(name: str):
        mapping = {
            "prompts_system.json": {"system_header": "SYSTEM", "schema_dialogue": "台词"},
            "prompts_character.json": {"tone_limiter": "冷一点"},
        }
        return mapping.get(name)

    with mock.patch.object(data, "_load_optional_json", side_effect=fake_optional):
        result = data._load_prompts()

    assert result["system_header"] == "SYSTEM"
    assert result["tone_limiter"] == "冷一点"
    assert result["schema_inner_os"] == data.PROMPTS_DEFAULTS["schema_inner_os"]


def test_init_pjsk_knowledge_flattens_entries():
    snapshot = (data.PJSK_KNOWLEDGE_BASE, data.PJSK_INTRO, copy.deepcopy(data.PJSK_ENTRIES))
    fake_payload = {
        "introduction": "前言",
        "knowledge_list": [
            {"category": "术语", "entries": ["A", "B"]},
            {"category": "角色", "entries": ["C"]},
        ],
    }

    try:
        with mock.patch.object(data, "load_json_file", return_value=fake_payload):
            data.init_pjsk_knowledge()

        assert data.PJSK_INTRO == "前言"
        assert "术语" in data.PJSK_KNOWLEDGE_BASE
        assert [entry["category"] for entry in data.PJSK_ENTRIES] == ["术语", "术语", "角色"]
        assert [entry["text"] for entry in data.PJSK_ENTRIES] == ["A", "B", "C"]
        assert all(entry["status"] == "active" for entry in data.PJSK_ENTRIES)
    finally:
        data.PJSK_KNOWLEDGE_BASE, data.PJSK_INTRO = snapshot[0], snapshot[1]
        data.PJSK_ENTRIES.clear()
        data.PJSK_ENTRIES.extend(snapshot[2])


def test_reload_assets_updates_existing_containers_and_counts_hooks():
    snapshot = {
        "DIRECTOR_DB": copy.deepcopy(data.DIRECTOR_DB),
        "DAILY_ROUTINE": copy.deepcopy(data.DAILY_ROUTINE),
        "WL2_ROUTINE": copy.deepcopy(data.WL2_ROUTINE),
        "SONG_DATA": copy.deepcopy(data.SONG_DATA),
        "REACTIONS_DB": copy.deepcopy(data.REACTIONS_DB),
        "PROMPTS_DB": copy.deepcopy(data.PROMPTS_DB),
        "SCRIPT_DB": list(data.SCRIPT_DB),
        "RELATIONSHIP_DATA": list(data.RELATIONSHIP_DATA),
        "SLEEP_DB": copy.deepcopy(data.SLEEP_DB),
    }
    hook_calls: list[str] = []

    def fake_load_json(filename: str, default):
        mapping = {
            "akito_director.json": {"director": "new"},
            "akito_routine.json": {"morning": [{"status": "练习中"}]},
            "wl2_routine.json": {"night": ["沉默"]},
            "akito_songs.json": {"song": {"song_name": "Test"}},
            "akito_scripts.json": [{"context": "ctx", "dialogue": "dlg"}],
            "akito_relationships.json": [{"keywords": ["冬弥"], "content": "搭档"}],
            "akito_sleep.json": {"complaints": ["困"]},
        }
        return copy.deepcopy(mapping.get(filename, default))

    fake_paro = types.ModuleType("nonebot_plugin_akito.features.random_paro")
    fake_paro.reload_paro_data = lambda: hook_calls.append("paro")
    fake_keyword = types.ModuleType("nonebot_plugin_akito.features.random_keyword")
    fake_keyword.reload_keyword_data = lambda: hook_calls.append("keyword")
    fake_retrieval = types.ModuleType("nonebot_plugin_akito.core.retrieval")
    fake_retrieval.reload_indices = lambda: hook_calls.append("retrieval") or 2

    try:
        with (
            mock.patch.object(data, "load_json_file", side_effect=fake_load_json),
            mock.patch.object(data, "_load_reactions", return_value={"merged": True}),
            mock.patch.object(data, "_load_prompts", return_value={"system_header": "NEW"}),
            mock.patch.object(data, "init_pjsk_knowledge", side_effect=lambda: hook_calls.append("pjsk")),
            mock.patch.dict(
                sys.modules,
                {
                    "nonebot_plugin_akito.features.random_paro": fake_paro,
                    "nonebot_plugin_akito.features.random_keyword": fake_keyword,
                    "nonebot_plugin_akito.core.retrieval": fake_retrieval,
                },
            ),
        ):
            count = data.reload_assets()

        assert count == 13
        assert data.DIRECTOR_DB == {"director": "new"}
        assert data.DAILY_ROUTINE == {"morning": [{"status": "练习中"}]}
        assert data.WL2_ROUTINE == {"night": ["沉默"]}
        assert data.SONG_DATA == {"song": {"song_name": "Test"}}
        assert data.REACTIONS_DB == {"merged": True}
        assert data.PROMPTS_DB == {"system_header": "NEW"}
        assert data.SCRIPT_DB == [{"context": "ctx", "dialogue": "dlg"}]
        assert data.RELATIONSHIP_DATA == [{"keywords": ["冬弥"], "content": "搭档"}]
        assert data.SLEEP_DB == {"complaints": ["困"]}
        assert hook_calls == ["paro", "keyword", "pjsk", "retrieval"]
    finally:
        data.DIRECTOR_DB.clear()
        data.DIRECTOR_DB.update(snapshot["DIRECTOR_DB"])
        data.DAILY_ROUTINE.clear()
        data.DAILY_ROUTINE.update(snapshot["DAILY_ROUTINE"])
        data.WL2_ROUTINE.clear()
        data.WL2_ROUTINE.update(snapshot["WL2_ROUTINE"])
        data.SONG_DATA.clear()
        data.SONG_DATA.update(snapshot["SONG_DATA"])
        data.REACTIONS_DB.clear()
        data.REACTIONS_DB.update(snapshot["REACTIONS_DB"])
        data.PROMPTS_DB.clear()
        data.PROMPTS_DB.update(snapshot["PROMPTS_DB"])
        data.SCRIPT_DB.clear()
        data.SCRIPT_DB.extend(snapshot["SCRIPT_DB"])
        data.RELATIONSHIP_DATA.clear()
        data.RELATIONSHIP_DATA.extend(snapshot["RELATIONSHIP_DATA"])
        data.SLEEP_DB.clear()
        data.SLEEP_DB.update(snapshot["SLEEP_DB"])

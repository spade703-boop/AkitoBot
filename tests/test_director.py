"""测试 director.py 的导演骰子纯函数。"""

from __future__ import annotations

from unittest import mock

import nonebot_plugin_akito.features.director as director


def test_detect_spicy_stage_uses_priority_order():
    assert director._detect_spicy_stage("停下，我真的不行了") == "aftercare"
    assert director._detect_spicy_stage("快高潮了，真的要去了") == "climax"
    assert director._detect_spicy_stage("慢点插进去，先扩张一下") == "mid_game"
    assert director._detect_spicy_stage("只是贴近了一点") == "foreplay"


def test_build_dynamic_words_supports_stage_dict_and_plain_list():
    with mock.patch.object(director.random, "sample", side_effect=lambda seq, count: list(seq)[:count]):
        from_dict = director._build_dynamic_words(
            "mid_game",
            {"dynamic_lexicon": {"mid_game": ["压住", "顶开"], "general": ["喘息", "发烫", "发抖"]}},
        )
        from_list = director._build_dynamic_words(
            "mid_game",
            {"dynamic_lexicon": ["靠近", "纠缠", "呼吸"]},
        )

    assert from_dict == "👉 压住, 顶开, 喘息"
    assert from_list == "👉 靠近, 纠缠, 呼吸"


def test_build_director_note_adds_cool_filter_for_non_toya_physical_scene():
    result = director.build_director_note(
        text="(抓住你的手)",
        is_toya_context=False,
        long_term_memory_text="",
        prompts_db={"cool_guy_filter": "别太软。"},
        director_db={},
    )

    assert result["is_physical_or_drama"] is True
    assert result["is_really_spicy"] is False
    assert result["acting_guide"] == "别太软。"
    assert result["format_breaker"] == ""


def test_build_spicy_format_breaker_contains_stage_specific_rules():
    with (
        mock.patch.object(director.random, "choice", side_effect=lambda seq: seq[0]),
        mock.patch.object(director.random, "sample", side_effect=lambda seq, count: list(seq)[:count]),
        mock.patch.object(director.random, "random", return_value=0.1),
    ):
        result = director._build_spicy_format_breaker(
            "先扩张，用手指弄松一点，别急",
            "",
            {"dynamic_lexicon": {"mid_game": ["压住", "顶开"], "general": ["喘息", "发烫", "发抖"]}},
        )

    assert "扩张/前戏铁律" in result
    assert "【感官描写重点】" in result
    assert "👉 压住, 顶开, 喘息" in result

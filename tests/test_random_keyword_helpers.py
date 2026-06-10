"""测试 random_keyword.py 中抽出的关键词辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.features.random_keyword as random_keyword


def _pick_first(options):
    return options[0]


def _sample_first(items, k):
    return list(items)[:k]


def test_resolve_keyword_category_name_supports_exact_and_prefix():
    names = ["科学隐喻", "自然意象", "关系张力"]

    assert random_keyword._resolve_keyword_category_name("自然意象", names) == "自然意象"
    assert random_keyword._resolve_keyword_category_name("科学", names) == "科学隐喻"
    assert random_keyword._resolve_keyword_category_name("不存在", names) is None


def test_get_existing_keyword_draw_message_detects_same_day_record():
    msg = random_keyword._get_existing_keyword_draw_message(
        {"date": "2026-06-11", "items": ["洛希极限", "雨夜"]},
        "2026-06-11",
    )
    stale = random_keyword._get_existing_keyword_draw_message(
        {"date": "2026-06-10", "items": ["旧词"]},
        "2026-06-11",
    )

    assert "你今天已经领取过关键词了" in msg
    assert "洛希极限、雨夜" in msg
    assert stale is None


def test_select_daily_keywords_picks_one_per_sampled_category():
    result = random_keyword._select_daily_keywords(
        [("科学隐喻", ["洛希极限", "热寂"]), ("自然意象", ["雨夜"]), ("关系张力", ["错位"])],
        2,
        sample_fn=_sample_first,
        choice_fn=_pick_first,
    )

    assert result == ["洛希极限", "雨夜"]

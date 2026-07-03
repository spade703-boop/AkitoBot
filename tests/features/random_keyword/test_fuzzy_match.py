"""测试 random_keyword 的关键词模糊匹配。"""

from __future__ import annotations

from unittest import mock

import nonebot_plugin_akito.features.random_keyword as random_keyword

_FAKE_CATS = {"categories": {"猫": ["布偶猫", "橘猫"], "狗": ["柴犬"]}}


def test_keyword_fuzzy_exact():
    """精确匹配返回 (条目, 所属分类)。"""
    with mock.patch.object(random_keyword, "KEYWORD_DATA", _FAKE_CATS):
        item, cat = random_keyword._fuzzy_match_in_categories("柴犬")
    assert item == "柴犬"
    assert cat == "狗"


def test_keyword_fuzzy_ambiguous_returns_list():
    """多个包含匹配返回 (候选列表, None)。"""
    with mock.patch.object(random_keyword, "KEYWORD_DATA", _FAKE_CATS):
        items, cat = random_keyword._fuzzy_match_in_categories("猫")
    assert isinstance(items, list)
    assert set(items) == {"布偶猫", "橘猫"}
    assert cat is None


def test_keyword_fuzzy_no_match():
    """无匹配返回 (None, None)。"""
    with mock.patch.object(random_keyword, "KEYWORD_DATA", _FAKE_CATS):
        item, cat = random_keyword._fuzzy_match_in_categories("不存在的东西")
    assert item is None
    assert cat is None

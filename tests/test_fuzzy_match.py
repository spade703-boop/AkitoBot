"""测试 feature 纯函数：派生 / 关键词的模糊匹配。

feature 模块会 import onebot 适配器等重依赖（已由 conftest mock）。
conftest 默认把 nonebot_plugin_akito.features 整包 mock 掉以隔离 core 测试，
这里先解除该 mock，让真实 feature 模块加载后再测其纯函数。
"""
import sys
from unittest import mock

# 解除 conftest 对 features 子包的预 mock，改为加载真实模块
for _name in list(sys.modules):
    if _name == "nonebot_plugin_akito.features" or _name.startswith("nonebot_plugin_akito.features."):
        del sys.modules[_name]

from nonebot_plugin_akito.features import random_keyword, random_paro  # noqa: E402

# ── random_paro._fuzzy_match（pool 作为参数传入，纯函数）────────────────────

def test_paro_fuzzy_exact_match():
    """精确匹配（忽略大小写）返回原始条目名。"""
    assert random_paro._fuzzy_match("akito", ["Akito", "Toya"]) == "Akito"


def test_paro_fuzzy_prefix_unique():
    """唯一前缀匹配返回该条目。"""
    assert random_paro._fuzzy_match("toy", ["Akito", "Toya"]) == "Toya"


def test_paro_fuzzy_prefix_ambiguous_returns_list():
    """多个前缀匹配返回候选列表（歧义）。"""
    result = random_paro._fuzzy_match("ak", ["Akito", "Akari"])
    assert isinstance(result, list)
    assert set(result) == {"Akito", "Akari"}


def test_paro_fuzzy_contains_single():
    """唯一包含匹配返回该条目。"""
    assert random_paro._fuzzy_match("ito", ["Akito", "Toya"]) == "Akito"


def test_paro_fuzzy_no_match_returns_none():
    """无匹配返回 None。"""
    assert random_paro._fuzzy_match("zzz", ["Akito", "Toya"]) is None


# ── random_keyword._fuzzy_match_in_categories（读模块全局 KEYWORD_DATA）──────

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

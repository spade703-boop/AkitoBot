"""?? random_paro ??????????"""

from __future__ import annotations

import nonebot_plugin_akito.features.random_paro as random_paro


def test_paro_fuzzy_exact_match():
    """???????????????????"""
    assert random_paro._fuzzy_match("akito", ["Akito", "Toya"]) == "Akito"


def test_paro_fuzzy_prefix_unique():
    """????????????"""
    assert random_paro._fuzzy_match("toy", ["Akito", "Toya"]) == "Toya"


def test_paro_fuzzy_prefix_ambiguous_returns_list():
    """?????????????????"""
    result = random_paro._fuzzy_match("ak", ["Akito", "Akari"])
    assert isinstance(result, list)
    assert set(result) == {"Akito", "Akari"}


def test_paro_fuzzy_contains_single():
    """????????????"""
    assert random_paro._fuzzy_match("ito", ["Akito", "Toya"]) == "Akito"


def test_paro_fuzzy_no_match_returns_none():
    """????? None?"""
    assert random_paro._fuzzy_match("zzz", ["Akito", "Toya"]) is None

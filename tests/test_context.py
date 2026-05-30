"""测试 context.py 的 Prompt 片段拼装纯函数（参考剧本、歌曲记忆）。"""
from unittest import mock

from nonebot_plugin_akito.core import context

# ── get_random_examples ─────────────────────────────────────────────────────

def test_get_random_examples_empty_returns_empty():
    """SCRIPT_DB 为空时返回空串。"""
    with mock.patch.object(context, "SCRIPT_DB", []):
        assert context.get_random_examples() == ""


def test_get_random_examples_formats_entries():
    """非空时返回含情境 / 台词的参考剧本文本。"""
    fake = [{"context": "在练习室", "dialogue": "再来一遍。"}]
    with mock.patch.object(context, "SCRIPT_DB", fake):
        result = context.get_random_examples(num=5)
    assert "参考剧本" in result
    assert "在练习室" in result
    assert "再来一遍。" in result


def test_get_random_examples_respects_count():
    """num 限制抽样数量（不超过池子大小）。"""
    fake = [{"context": f"c{i}", "dialogue": f"d{i}"} for i in range(10)]
    with mock.patch.object(context, "SCRIPT_DB", fake):
        result = context.get_random_examples(num=3)
    assert result.count("- 情境") == 3


# ── get_song_memories ───────────────────────────────────────────────────────

def test_get_song_memories_empty_returns_empty():
    """SONG_DATA 为空时返回空串。"""
    with mock.patch.object(context, "SONG_DATA", {}):
        assert context.get_song_memories() == ""


def test_get_song_memories_uses_description():
    """优先使用 description 字段拼装歌曲记忆。"""
    fake = {"s1": {"song_name": "ヒバナ", "description": "和冬弥一起练的曲子。"}}
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_memories()
    assert "歌曲记忆" in result
    assert "ヒバナ" in result
    assert "和冬弥一起练的曲子。" in result


def test_get_song_memories_truncates_long_summary():
    """超长摘要(>120)被截断并加省略号。"""
    fake = {"s1": {"song_name": "Song", "description": "啊" * 200}}
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_memories()
    assert "……" in result
    assert "啊" * 121 not in result

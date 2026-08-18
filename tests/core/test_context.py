"""测试 context.py 的 Prompt 片段拼装纯函数（参考剧本、歌曲记忆）。"""
from unittest import mock

from nonebot_plugin_akito.core import context

# ── get_random_examples ─────────────────────────────────────────────────────

def test_get_random_examples_empty_returns_empty():
    """SCRIPT_DB 为空时返回空串。"""
    with mock.patch.object(context, "SCRIPT_DB", []):
        assert context.get_random_examples() == ""


def test_get_random_examples_formats_entries():
    """非空时返回含情境 / 台词和归因锁的参考剧本文本。"""
    fake = [{"context": "在练习室", "dialogue": "再来一遍。"}]
    with mock.patch.object(context, "SCRIPT_DB", fake):
        result = context.get_random_examples(num=5)
    assert "参考剧本" in result
    assert "在练习室" in result
    assert "再来一遍。" in result
    assert "本条发言者：彰人" in result
    assert "不得交换主语/宾语" in result
    assert "历史事件" in result
    assert "一次性地点、临时住宿或活动状态" in result


def test_get_random_examples_respects_count():
    """num 限制抽样数量（不超过池子大小）。"""
    fake = [{"context": f"c{i}", "dialogue": f"d{i}"} for i in range(10)]
    with mock.patch.object(context, "SCRIPT_DB", fake):
        result = context.get_random_examples(num=3)
    assert result.count("本条发言者：彰人") == 3


# ── get_song_memories ───────────────────────────────────────────────────────

def test_get_song_memories_empty_returns_empty():
    """SONG_DATA 为空时返回空串。"""
    with mock.patch.object(context, "SONG_DATA", {}):
        assert context.get_song_memories() == ""


def test_get_song_memories_returns_song_name_list():
    """静态注入只保留曲名清单，不展开正文描述。"""
    fake = {
        "s1": {"song_name": "《ヒバナ》", "description": "和冬弥一起练的曲子。"},
        "s2": {"song_name": "《阿吽のビーツ》", "description": "谱子挺烦，但能唱。"},
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_memories()
    assert "你会唱的歌" in result
    assert "《ヒバナ》/《阿吽のビーツ》" in result
    assert "和冬弥一起练的曲子。" not in result
    assert "谱子挺烦，但能唱。" not in result


def test_get_song_memories_skips_entries_without_song_name():
    """缺失曲名的条目不会污染清单。"""
    fake = {
        "s1": {"description": "无名条目"},
        "s2": {"song_name": "《Cinema》", "description": "正文不应被注入"},
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_memories()
    assert "《Cinema》" in result
    assert "无名条目" not in result
    assert "正文不应被注入" not in result


# ── get_song_mention ────────────────────────────────────────────────────────

def test_get_song_mention_returns_empty_when_no_match():
    """未命中任何关键词时返回空串。"""
    fake = {"s1": {"song_name": "《Cinema》", "description": "回忆", "keywords": ["cinema"]}}
    with mock.patch.object(context, "SONG_DATA", fake):
        assert context.get_song_mention("今天在练歌") == ""


def test_get_song_mention_injects_full_description_for_single_match():
    """命中单首歌时注入完整 description。"""
    fake = {
        "s1": {
            "song_name": "《阿吽のビーツ》",
            "description": "这首歌的情绪得压着唱，急了就全毁了。",
            "keywords": ["阿吽", "阿吽のビーツ"],
        }
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_mention("阿吽的谱子难吗")
    assert "歌曲话题" in result
    assert "《阿吽のビーツ》" in result
    assert "这首歌的情绪得压着唱，急了就全毁了。" in result


def test_get_song_mention_limits_to_two_matches():
    """同一条消息最多注入两首歌。"""
    fake = {
        "s1": {"song_name": "《A》", "description": "desc-a", "keywords": ["alpha"]},
        "s2": {"song_name": "《B》", "description": "desc-b", "keywords": ["beta"]},
        "s3": {"song_name": "《C》", "description": "desc-c", "keywords": ["gamma"]},
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_mention("alpha beta gamma")
    assert "《A》" in result
    assert "《B》" in result
    assert "《C》" not in result
    assert result.count("\n- ") == 2


def test_get_song_mention_is_case_insensitive():
    """英文别名匹配不区分大小写。"""
    fake = {
        "s1": {"song_name": "《Cinema》", "description": "desc", "keywords": ["Cinema"]},
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_mention("我想问一下 CINEMA 的谱面")
    assert "《Cinema》" in result


def test_get_song_mention_ignores_missing_or_empty_keywords():
    """keywords 缺失或为空列表时不报错，也不会误命中。"""
    fake = {
        "s1": {"song_name": "《A》", "description": "desc-a"},
        "s2": {"song_name": "《B》", "description": "desc-b", "keywords": []},
        "s3": {"song_name": "《C》", "description": "desc-c", "keywords": ["valid"]},
    }
    with mock.patch.object(context, "SONG_DATA", fake):
        result = context.get_song_mention("valid")
    assert "《A》" not in result
    assert "《B》" not in result
    assert "《C》" in result


async def test_hybrid_relationship_uses_local_profile_without_web_content():
    fake = [{"keywords": ["冬弥"], "content": "冬弥是彰人的搭档。"}]
    with mock.patch.object(context, "RELATIONSHIP_DATA", fake):
        result = await context.get_hybrid_relationship("你怎么看冬弥？")
    assert "冬弥是彰人的搭档。" in result
    assert "网络搜索结果" not in result


def test_relationship_match_formats_main_chat_context():
    fake = [{"keywords": ["冬弥", "Toya"], "content": "冬弥是彰人的搭档。"}]
    with mock.patch.object(context, "RELATIONSHIP_DATA", fake):
        match = context.find_relationship_match("TOYA 最近怎么样")

    assert match == context.RelationshipMatch(keyword="Toya", content="冬弥是彰人的搭档。")
    assert context.format_relationship_context(match) == (
        '\n【检测到用户正在询问关于"Toya"的话题】\n'
        "📖【长期记忆库 (基础认知)】📖\n冬弥是彰人的搭档。\n"
    )

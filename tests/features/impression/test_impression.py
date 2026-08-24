"""测试 impression.py 中抽出的目标判断与回复解析辅助函数。"""

from __future__ import annotations

import datetime
import types
from unittest import mock

import aiosqlite
import pytest

from nonebot_plugin_akito.core.memory import MessageReader
import nonebot_plugin_akito.features.impression as impression


class _Seg:
    def __init__(self, seg_type: str, data: dict):
        self.type = seg_type
        self.data = data


def _message_row(
    row_id: int,
    user_id: str,
    nickname: str,
    content: str,
    message_id: str,
    timestamp: str,
):
    return (row_id, user_id, nickname, content, message_id, timestamp)


def test_resolve_impression_target_defaults_to_sender():
    event = types.SimpleNamespace(
        user_id="123",
        sender=types.SimpleNamespace(card="", nickname="测试用户"),
        original_message=[],
    )

    target_id, target_name, is_querying_other, is_querying_bot = impression._resolve_impression_target(event, "999")

    assert target_id == "123"
    assert target_name == "测试用户"
    assert is_querying_other is False
    assert is_querying_bot is False


def test_resolve_impression_target_detects_querying_bot():
    event = types.SimpleNamespace(
        user_id="123",
        sender=types.SimpleNamespace(card="群名片", nickname="测试用户"),
        original_message=[_Seg("at", {"qq": "999"})],
    )

    target_id, target_name, is_querying_other, is_querying_bot = impression._resolve_impression_target(event, "999")

    assert target_id == "999"
    assert target_name == "群名片"
    assert is_querying_other is True
    assert is_querying_bot is True


def test_impression_prompt_distinguishes_self_and_other_addressing():
    analysis_prompt = impression._build_impression_analysis_system_prompt(target_name="小明")
    self_prompt = impression._build_impression_reply_system_prompt(
        persona="基础人设",
        state_overlay_prompt="",
        target_name="小明",
        is_querying_other=False,
    )
    other_prompt = impression._build_impression_reply_system_prompt(
        persona="基础人设",
        state_overlay_prompt="",
        target_name="小红",
        is_querying_other=True,
    )

    assert "使用第二人称“你”" in self_prompt
    assert "使用女性第三人称“她”" in other_prompt
    assert "中立的材料分析器" in analysis_prompt
    assert "包含2-4个字符串的 JSON 数组" in analysis_prompt
    assert "候选之间" in self_prompt
    assert "focus" not in analysis_prompt
    assert "focus" not in self_prompt
    for phrase in ("这种劲儿不赖", "至少不装", "嘴上怎样", "看来里面"):
        assert phrase not in analysis_prompt
        assert phrase not in self_prompt


def test_impression_relationship_context_resolves_self_and_all_matching_people():
    relationship_data = [
        {"keywords": ["冬弥", "青柳"], "content": "【关系：青柳冬弥】搭档资料"},
        {"keywords": ["青柳春道"], "content": "【关系：青柳春道】关系资料"},
    ]

    with mock.patch.object(impression, "RELATIONSHIP_DATA", relationship_data):
        context = impression._build_impression_relationship_context(
            "她说akt最近又提到彰人和冬弥，也提过青柳春道。"
        )

    assert "东云彰人（你自己）" in context
    assert "材料命中：彰人、akt" in context
    assert "【关系：青柳冬弥】" in context
    assert "【关系：青柳春道】" in context
    assert "【关系：青柳】" not in context


def test_auto_chat_prompt_keeps_task_schema_and_shared_sections():
    result = impression._build_auto_chat_system_prompt(
        persona="基础人设",
        time_str="上午8点00分",
        toya_anchor="冬弥锚点",
        scene_desc="当前消息",
        group_context="群聊背景",
        relation_info="关系资料",
        song_info="歌曲命中",
        script_examples="剧本示例",
        pjsk_block="PJSK 内容",
        cool_guy_filter="语气限制",
        task_logic="决定是否回复",
        inner_os_guide="分析当前消息",
    )

    for fragment in (
        "基础人设",
        "上午8点00分",
        "冬弥锚点",
        "当前消息",
        "群聊背景",
        "关系资料",
        "歌曲命中",
        "剧本示例",
        "PJSK 内容",
        "语气限制",
        "决定是否回复",
    ):
        assert fragment in result
    assert '"inner_os": "分析当前消息"' in result
    assert '"anchor":' in result
    assert '"reply":' in result


def test_impression_addressing_filter_rejects_wrong_target_pronouns():
    assert impression._find_impression_addressing_issue(
        "对小明的印象是……你最近一直惦记那张卡。",
        is_querying_other=False,
    ) == ""
    assert impression._find_impression_addressing_issue(
        "对小明的印象是……他最近一直惦记那张卡。",
        is_querying_other=False,
    )
    assert impression._find_impression_addressing_issue(
        "对小红的印象是……她最近一直惦记那张卡。",
        is_querying_other=True,
    ) == ""
    assert impression._find_impression_addressing_issue(
        "对小红的印象是……你最近一直惦记那张卡。",
        is_querying_other=True,
    )


def test_exact_impression_request_rejects_command_prefix_sentences():
    exact_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象",
        original_message=[_Seg("text", {"text": "我的印象"})],
    )
    querying_other_event = types.SimpleNamespace(
        get_plaintext=lambda: "群印象 ",
        original_message=[_Seg("text", {"text": "群印象 "}), _Seg("at", {"qq": "123"})],
    )
    sentence_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象他是一个什么样的人",
        original_message=[_Seg("text", {"text": "我的印象他是一个什么样的人"})],
    )
    image_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象",
        original_message=[_Seg("text", {"text": "我的印象"}), _Seg("image", {"file": "x"})],
    )

    assert impression._is_exact_impression_request_message(exact_event) is True
    assert impression._is_exact_impression_request_message(querying_other_event) is True
    assert impression._is_exact_impression_request_message(sentence_event) is False
    assert impression._is_exact_impression_request_message(image_event) is False


def test_build_impression_history_text_reverses_rows_into_prompt_order():
    rows = [
        _message_row(2, "123", "小明", "第二句", "m2", "2026-08-01 01:00:00"),
        _message_row(1, "123", "小明", "第一句", "m1", "2026-08-01 00:00:00"),
    ]

    result = impression._build_impression_history_text(rows, "小明")

    assert result == "[08-01 08:00]【小明】: 第一句\n[08-01 09:00]【小明】: 第二句"


@pytest.mark.asyncio
async def test_load_impression_material_builds_history_context_away_from_command():
    connection = await aiosqlite.connect(":memory:")
    await connection.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            group_id TEXT,
            user_id TEXT,
            nickname TEXT,
            content TEXT,
            message_id TEXT,
            timestamp DATETIME
        )
        """
    )
    rows = [
        (1, "1", "other", "群友A", "明天不是要演出吗", "m1", "2026-08-01 12:00:00"),
        (2, "1", "target", "小明", "还有两首没练完", "m2", "2026-08-01 12:01:00"),
        (3, "1", "other", "群友A", "你今天又不睡？", "m3", "2026-08-01 12:02:00"),
        (4, "1", "target", "小明", "六点起来继续", "m4", "2026-08-01 12:03:00"),
        (5, "1", "other", "群友A", "行吧，加油", "m5", "2026-08-01 12:04:00"),
        (6, "1", "target", "小明", "群印象", "cmd-current", "2026-08-02 00:00:00"),
        (7, "1", "target", "小明", "较早的完整发言", "old", "2026-07-01 00:00:00"),
    ]
    await connection.executemany(
        "INSERT INTO messages (id, group_id, user_id, nickname, content, message_id, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    await connection.commit()
    reader = MessageReader(connection)
    history_rows, blocks = await impression._load_impression_material(
        reader,
        group_id="1",
        target_id="target",
        current_message_id="cmd-current",
        now=datetime.datetime(2026, 8, 2, tzinfo=datetime.timezone.utc),
    )

    assert "群印象" not in [row[3] for row in history_rows]
    assert "较早的完整发言" in [row[3] for row in history_rows]
    assert len(blocks) == 1
    assert [row[3] for row in blocks[0]] == [
        "明天不是要演出吗",
        "还有两首没练完",
        "你今天又不睡？",
        "六点起来继续",
        "行吧，加油",
    ]
    await connection.close()


def test_merge_context_windows_keeps_latest_six_blocks():
    windows = [
        [_message_row(index, "target", "小明", f"发言{index}", f"m{index}", f"2026-08-01 {index:02d}:00:00")]
        for index in range(1, 8)
    ]

    blocks = impression._merge_context_windows(windows)

    assert len(blocks) == 6
    assert blocks[0][0][3] == "发言2"
    assert blocks[-1][0][3] == "发言7"


def test_resolve_wl2_overlay_uses_active_stored_content():
    memory = {
        "temp_implants": [
            {"id": "WL2", "content": "过期世界线", "expire_at": 50.0},
            {"id": "WL2", "content": "当前世界线", "expire_at": 200.0},
        ]
    }

    is_active, overlay = impression._resolve_wl2_overlay(memory, now_ts=100.0)

    assert is_active is True
    assert overlay == "当前世界线"


def test_validate_impression_analysis_requires_target_evidence():
    valid, reason = impression._validate_impression_analysis(
        analysis=impression.ImpressionAnalysis(
            mode="specific",
            evidence=("还有两首没练完", "六点起来继续"),
            observations=("没有练完却已经安排了下一次练习",),
            uncertainties=(),
            avoid_patterns=(),
        ),
        target_evidence_source="还有两首没练完\n六点起来继续",
    )
    invalid, invalid_reason = impression._validate_impression_analysis(
        analysis=impression.ImpressionAnalysis(
            mode="specific",
            evidence=("还有两首没练完", "明天不是要演出吗"),
            observations=("熬夜练歌还安排了第二天继续",),
            uncertainties=(),
            avoid_patterns=(),
        ),
        target_evidence_source="还有两首没练完\n六点起来继续",
    )

    assert valid is True
    assert reason == ""
    assert invalid is False
    assert "evidence 不在目标发言中" in invalid_reason


def test_parse_impression_analysis_reads_observations_and_style_patterns():
    raw = (
        '{"mode":"specific","evidence":["还有两首没练完","六点起来继续"],'
        '"observations":["没有练完却已经安排了下一次练习"],'
        '"uncertainties":["动机无法确认"],"avoid_patterns":["固定的认可式收尾"]}'
    )

    analysis = impression._parse_impression_analysis(raw)

    assert analysis is not None
    assert analysis.mode == "specific"
    assert analysis.evidence == ("还有两首没练完", "六点起来继续")
    assert analysis.observations == ("没有练完却已经安排了下一次练习",)
    assert analysis.uncertainties == ("动机无法确认",)
    assert analysis.avoid_patterns == ("固定的认可式收尾",)


def test_parse_impression_candidates_reads_multiple_replies():
    raw = (
        '{"inner_os":"先说观察就停","replies":['
        '"对小明的印象是……你最近一直在盯那张卡。",'
        '"对小明的印象是……那张卡你是真没放下。",'
        '"对小明的印象是……能让你反复提起，看来确实很喜欢。"]}'
    )

    inner_os, candidates = impression._parse_impression_candidates(raw)

    assert inner_os == "先说观察就停"
    assert len(candidates) == 3
    assert candidates[0].startswith("对小明的印象是")


def test_impression_style_filter_rejects_generic_profile_templates():
    generic_replies = [
        "对寒星的印象是...又一个把精力砸在游戏和周边上的家伙。嘴上说着不想打了，结果聊的全是这些。"
        "不过说话挺随意的，估计挺好相处。",
        "对复习傻鼠⪩. .⪨的印象是：一个挺能聊的普通网友，聊吃的、打游戏都来劲，偶尔冒出点没头没尾的话。"
        "感觉挺随和的，不过也就那样。",
        "对谨言慎行的印象是……就是个普通玩家吧，聊游戏、抱怨几句，偶尔吐槽下生活，没什么特别的。",
        "对舟的印象是……就是个普通人吧，平时打打游戏吃吃东西，偶尔吐槽下生活里的破事。"
        "挺正常的，没什么值得多说的。",
    ]

    for reply in generic_replies:
        assert impression._find_impression_style_issue(reply)


def test_impression_style_filter_allows_multiple_specific_observations():
    reply = (
        "对夙沙茗的印象是...也是个玩烤的，聊游戏挺起劲。居然也怕狗，这点倒是跟我一样。"
        "写文加班熬到挺晚，看来也是个能肝的家伙。"
    )

    assert impression._find_impression_style_issue(reply) == ""


def test_impression_similarity_score_ignores_name_but_compares_reply_body():
    candidate = "对小明的印象是……聊游戏一来劲就停不下来，嘴上嫌麻烦，真开打又比谁都认真。"
    recent = ["对小红的印象是……聊游戏一来劲就停不下来，嘴上嫌麻烦，真开打又比谁都认真。"]

    score = impression._score_impression_style_reuse(candidate, recent)

    assert score.full == 1.0


def test_candidate_selection_prefers_a_fresh_ending_without_global_ban():
    analysis = impression.ImpressionAnalysis(
        mode="specific",
        evidence=("一直盯着排名", "再来一把"),
        observations=("会持续关注游戏结果",),
        uncertainties=(),
        avoid_patterns=(),
    )
    recent = [
        "对小红的印象是……她一直盯着排名，这种劲儿不赖。",
        "对小蓝的印象是……她一直盯着排名，至少不装。",
    ]
    candidates = [
        "对小明的印象是……你一直盯着排名，这种劲儿不赖。",
        "对小明的印象是……你会把结果记下来，下一把还想继续试。",
    ]

    evaluations = impression._evaluate_impression_candidates(
        candidates,
        analysis=analysis,
        target_name="小明",
        is_querying_other=False,
        recent_replies=recent,
    )
    selected = impression._select_impression_candidate(evaluations)

    assert selected is not None
    assert selected.reply == candidates[1]
    allowed, reason, _score = impression._validate_impression_candidate(
        reply="对小明的印象是……你一直盯着排名，至少不装。",
        mode="specific",
        target_name="小明",
        is_querying_other=False,
    )
    assert allowed is True
    assert reason == ""


def test_validate_impression_candidate_rejects_recently_reused_wording():
    recent_reply = "对小红的印象是……聊游戏一来劲就停不下来，嘴上嫌麻烦，真开打又比谁都认真。"

    valid, reason, score = impression._validate_impression_candidate(
        reply="对小明的印象是……聊游戏一来劲就停不下来，嘴上嫌麻烦，真开打又比谁都认真。",
        mode="specific",
        target_name="小明",
        is_querying_other=False,
        recent_replies=[recent_reply],
    )

    assert valid is False
    assert "近期评价过于相似" in reason
    assert score.full == 1.0


def test_validate_limited_impression_requires_honest_uncertainty():
    valid, reason, _score = impression._validate_impression_candidate(
        reply="对小明的印象是……目前能看出的只有你经常接游戏话题，再往下说就有点硬猜了。",
        mode="limited",
        target_name="小明",
        is_querying_other=False,
    )
    invalid, invalid_reason, _invalid_score = impression._validate_impression_candidate(
        reply="对小明的印象是个普通玩家，应该挺好相处的。",
        mode="limited",
        target_name="小明",
        is_querying_other=False,
    )

    assert valid is True
    assert reason == ""
    assert invalid is False
    assert "limited 模式必须明确表达暂时看不准" in invalid_reason


def test_validate_specific_impression_allows_detail_beyond_old_eighty_char_limit():
    reply = (
        "对小明的印象是……每次说不打了，下一局开得又比谁都快；掉东西时抱怨得响，真有人问配队又讲得很细。"
        "嘴上总嫌麻烦，手上倒一次没停，连别人漏掉的小地方也会顺手指出来，至少对游戏这件事不是随便混混。"
    )

    valid, reason, _score = impression._validate_impression_candidate(
        reply=reply,
        mode="specific",
        target_name="小明",
        is_querying_other=False,
    )

    assert 80 < len(reply) <= impression.IMPRESSION_SPECIFIC_MAX_LENGTH
    assert valid is True
    assert reason == ""


def test_parse_impression_candidates_rescues_broken_reply_field():
    raw = '{"inner_os":"有点熟","reply":"对小明的印象是还算活跃","bad":"没关上}'

    inner_os, candidates = impression._parse_impression_candidates(raw)

    assert candidates == ["对小明的印象是还算活跃"]
    assert inner_os == ""


def test_should_skip_random_chat_blocks_prefix_and_keywords():
    assert impression._should_skip_random_chat("/help") is True
    assert impression._should_skip_random_chat("开始进货 表情") is True
    assert impression._should_skip_random_chat("你好") is False


def test_grounded_random_reply_requires_anchor_from_current_message():
    assert impression._is_grounded_random_reply("今天又下雨了", "下雨", "出门记得带伞。") is True
    assert impression._is_grounded_random_reply("今天又下雨了", "昨天考试", "考得怎么样？") is False
    assert impression._is_grounded_random_reply("今天又下雨了", "雨", "带伞。") is False


def test_grounded_random_reply_allows_silence_without_anchor():
    assert impression._is_grounded_random_reply("哈哈哈哈", "", "") is True

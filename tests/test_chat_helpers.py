"""测试 chat.py 中抽出的核心辅助函数。"""

from __future__ import annotations

from unittest import mock

from nonebot_plugin_akito.handlers import chat


def test_build_interact_instruction_for_toya_reply_bridge():
    result = chat._build_interact_instruction(
        plain_text_content="帮我回冬弥一句",
        sender_nickname="测试群友",
        user_id="12345",
        is_talking_to_toya=False,
        reply_target_is_toya=True,
        has_reply=True,
        current_image_identity="",
        origin_sender="青柳冬弥",
    )
    assert "中转站模式" in result
    assert "测试群友" in result
    assert "青柳冬弥" in result


def test_fold_stale_history_into_time_gap_prompt_clears_history():
    user_mem = {
        "history": [
            {"role": "user", "content": "[测试群友(123)]: 你在忙什么"},
            {"role": "assistant", "content": '{"reply": "关你什么事。"}'},
        ]
    }

    result = chat._fold_stale_history_into_time_gap_prompt(
        user_mem=user_mem,
        time_gap_awareness="⏱️【时间流逝感知】",
        group_id=1001,
    )

    assert "上次对话摘要" in result
    assert "你在忙什么" in result
    assert "关你什么事" in result
    assert user_mem["history"] == []


def test_build_final_system_prompt_contains_all_major_sections():
    result = chat._build_final_system_prompt(
        system_header="HEADER",
        current_time="2026年6月10日 上午8点00分",
        daily_status="正在练歌。",
        toya_anchor="冬弥在附近。",
        time_gap_awareness="时间过去了。",
        festival_buff="无",
        morning_run_buff="无",
        sleep_buffer_buff="无",
        relationship_context="关系文本",
        group_context="[A]: hi",
        interact_instruction="对象说明",
        base_persona="人设文本",
        script_examples="剧本示例",
        pjsk_block="PJSK 内容",
        song_memories="歌曲记忆",
        long_term_memory_text="长期记忆",
        reality_overwrite_instruction="临时状态",
        acting_guide="演技提示",
        sleep_instruction="",
        vitality_guide="活力提示",
        memory_capture_rule="记忆规则",
        tone_limiter="语气限制",
        schema_inner_os="内心",
        schema_action="动作",
        schema_dialogue="台词",
    )

    assert "HEADER" in result
    assert "物理现实与环境" in result
    assert "社交上下文" in result
    assert "核心人设与记忆" in result
    assert '"inner_os": "内心"' in result


def test_parse_model_reply_handles_directive_action_layout():
    raw = '{"inner_os":"想了下","action":"递过去","dialogue":"拿着。"}'

    with mock.patch.object(chat.random, "choice", return_value="(递过去)拿着。"):
        result, inner_os = chat._parse_model_reply(raw, is_toya_context=False)

    assert result == "(递过去)拿着。"
    assert inner_os == "想了下"


def test_parse_model_reply_rescues_broken_json():
    raw = '{"inner_os":"想了下","reply":"救援内容","bad":"没关上}'

    result, inner_os = chat._parse_model_reply(raw, is_toya_context=False)

    assert result == "救援内容"
    assert inner_os == ""

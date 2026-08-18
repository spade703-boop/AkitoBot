"""测试 chat.py 中抽出的核心辅助函数。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

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
    assert "中转站" in result
    assert "测试群友" in result
    assert "青柳冬弥" in result


def test_third_party_toya_reference_keeps_audience_and_relationship_axes_separate():
    audience = chat._build_interact_instruction(
        plain_text_content="冬弥说你作业还没写完",
        sender_nickname="测试群友",
        user_id="12345",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        has_reply=False,
        current_image_identity="",
        origin_sender="",
    )
    relationship = chat._build_referenced_relationship_instruction(
        "冬弥说你作业还没写完",
        "测试群友",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        is_wl2=False,
    )

    assert "普通群友" in audience
    assert "第三方提到冬弥" in relationship
    assert "两个独立维度" in relationship
    assert "他的事不关我事" in relationship
    assert "不能改写你和冬弥的关系" in relationship


@pytest.mark.parametrize("behavior", ["擦鞋", "像佣人一样服侍", "替人做危险实验", "为了别人牺牲自己"])
def test_toya_relationship_boundary_covers_general_harmful_behavior(behavior):
    result = chat._build_referenced_relationship_instruction(
        f"冬弥正在{behavior}",
        "测试群友",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        is_wl2=False,
    )

    assert "通用原则" in result
    assert "被使唤、服侍化、羞辱、危险、自我牺牲" in result
    assert "制止、接手危险部分或把相处方式纠正回平等互助" in result


@pytest.mark.parametrize("behavior", ["互相整理演出服", "一起练习", "辅导彰人学习", "受伤后帮忙包扎"])
def test_toya_relationship_boundary_allows_normal_cooperation(behavior):
    result = chat._build_referenced_relationship_instruction(
        f"冬弥正在{behavior}",
        "测试群友",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        is_wl2=False,
    )

    assert "属于正常互动，可以接受，不要过度阻止" in result
    assert "目的、自愿性、必要性、风险和双方是否平等" in result


def test_toya_relationship_boundary_requires_confirmation_when_details_are_missing():
    result = chat._build_referenced_relationship_instruction(
        "冬弥在帮我做事",
        "测试群友",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        is_wl2=False,
    )

    assert "不能直接当成事实" in result
    assert "是否自愿、是否有必要" in result
    assert "如果真是这样" in result


def test_toya_roleplay_message_is_direct_interaction():
    assert chat._is_toya_roleplay_message("冬弥：（把作业放到彰人面前）写完了吗") is True
    assert chat._is_toya_roleplay_message("群友说冬弥要查你作业") is False


def test_wl2_skips_normal_toya_relationship_axis():
    relationship = chat._build_referenced_relationship_instruction(
        "冬弥最近怎么样",
        "测试群友",
        is_talking_to_toya=False,
        reply_target_is_toya=False,
        is_wl2=True,
    )
    assert relationship == ""


def test_toya_fact_grounding_locks_grades_and_remedial_subjects():
    result = chat._build_fact_grounding_instruction(is_toya_context=True, is_wl2=False)
    assert "冬弥成绩优异" in result
    assert "彰人和杏" in result
    assert "出席日数" in result
    assert "不能改写成他学习差" in result


def test_toya_fact_grounding_locks_local_evidence_and_residence_scope():
    result = chat._build_fact_grounding_instruction(is_toya_context=True, is_wl2=False)

    assert "住处、近况和当前位置只使用本地" in result
    assert "住在家里" in result
    assert "纽约音乐院学生寮" in result
    assert "一次性临时住宿" in result
    assert "不得编造宿舍、寄宿、搬家、独居" in result
    assert "有效临时覆写或用户确认信息优先" in result


def test_wl2_fact_grounding_skips_normal_toya_residence_default():
    result = chat._build_fact_grounding_instruction(is_toya_context=True, is_wl2=True)

    assert "住在家里" not in result
    assert "纽约音乐院学生寮" not in result


def test_role_fact_query_stays_local_without_explicit_web_request():
    intent = SimpleNamespace(intent="local_question", explicit_search=False)
    assert chat._select_search_mode(intent, has_image=False) == "local"


def test_explicit_web_request_can_force_search():
    intent = SimpleNamespace(intent="web_search", explicit_search=True)
    assert chat._select_search_mode(intent, has_image=False) == "forced"


def test_time_sensitive_external_fact_can_use_search_agent():
    intent = SimpleNamespace(intent="web_search", explicit_search=False)
    assert chat._select_search_mode(intent, has_image=False) == "agent"


def test_image_never_uses_search_agent():
    intent = SimpleNamespace(intent="web_search", explicit_search=False)
    assert chat._select_search_mode(intent, has_image=True) == "local"


def test_third_party_toya_topic_does_not_roll_intimate_director():
    with (
        mock.patch.object(chat.random, "choice") as choice_mock,
        mock.patch.object(chat.random, "random") as random_mock,
    ):
        result = chat._build_toya_acting_guide(
            is_direct_toya_interaction=False,
            is_physical_or_drama=False,
            prompts_db={"toya_acting_guide": "风格：{selected}"},
            director_db={"toya_directions": ["亲密动作"]},
        )

    assert result == ""
    choice_mock.assert_not_called()
    random_mock.assert_not_called()


def test_direct_toya_director_keeps_groupmate_as_relay_only():
    with (
        mock.patch.object(chat.random, "choice", return_value="侧重信任"),
        mock.patch.object(chat.random, "random", return_value=0.1),
    ):
        result = chat._build_toya_acting_guide(
            is_direct_toya_interaction=True,
            is_physical_or_drama=False,
            prompts_db={"toya_acting_guide": "风格：{selected}"},
            director_db={"toya_directions": ["侧重信任"]},
        )

    assert "风格：侧重信任" in result
    assert "群友仍只是中转者" in result
    assert "不能把你对冬弥的态度切换成疏离" in result


def test_build_image_director_instruction_akito():
    assert "照片里是你自己" in chat._build_image_director_instruction("akito")


def test_build_image_director_instruction_toya():
    result = chat._build_image_director_instruction("toya")
    assert "照片里是冬弥" in result
    assert "护短" in result


def test_build_image_director_instruction_pair():
    assert "合照" in chat._build_image_director_instruction("pair")


def test_build_image_director_instruction_others_fall_to_generic():
    # kaito/绘名/none 等一律走通用吐槽分支；选择只看裁决标签，OCR 文本含"彰人"不再误触发
    for label in ("kaito", "ena", "tsukasa", "none"):
        assert "严禁" in chat._build_image_director_instruction(label)


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
        referenced_relationship_instruction="被谈论人物关系",
        base_persona="人设文本",
        script_examples="剧本示例",
        pjsk_block="PJSK 内容",
        song_memories="歌曲记忆",
        long_term_memory_text="长期记忆",
        reality_overwrite_instruction="临时状态",
        acting_guide="演技提示",
        sleep_instruction="",
        fact_grounding_instruction="事实归因规则",
        vitality_guide="活力提示",
        memory_capture_rule="记忆规则",
        tone_limiter="语气限制",
        schema_inner_os="内心",
        schema_action="动作",
        schema_dialogue="台词",
    )

    assert "HEADER" in result
    assert "环境与状态" in result
    assert "角色与知识" in result
    assert "当前任务上下文" in result
    assert "任务规则" in result
    assert "被谈论人物关系" in result
    assert "事实归因规则" in result
    assert "强制输出格式 (JSON)" in result
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


def test_search_miss_note_mentions_query_and_fallback_behavior():
    note = chat._search_miss_note("东京天气")
    assert "东京天气" in note
    assert "没有在手机上搜到" in note


def test_build_search_aside_with_hit_forces_in_character_restate():
    # 命中结果：必须把原文塞进来，同时强制"用自己的语气复述、别照原文念"——不直出摘要
    aside = chat._build_search_aside("世界计划 演唱会", "- 标题: 12月开演")
    assert "12月开演" in aside
    assert "东云彰人" in aside
    assert "别照着原文念" in aside


def test_build_search_aside_without_hit_falls_back_to_miss_note():
    # 无结果：复用兜底注入，让模型凭记忆/常识回答
    aside = chat._build_search_aside("不存在的东西", "")
    assert aside.strip() == chat._search_miss_note("不存在的东西")


@pytest.mark.asyncio
async def test_smart_finish_quotes_trigger_for_plain_text():
    matcher = SimpleNamespace(finish=mock.AsyncMock())

    await chat.smart_finish(matcher, "普通回复", "msg-42")

    payload = matcher.finish.await_args.args[0]
    assert payload == "[reply:msg-42]普通回复"


@pytest.mark.asyncio
async def test_smart_finish_quotes_trigger_for_image_reply():
    matcher = SimpleNamespace(finish=mock.AsyncMock())

    await chat.smart_finish(matcher, "配文\n![图](https://example.com/a.png)", "msg-43")

    payload = matcher.finish.await_args.args[0]
    assert payload == "[reply:msg-43]配文\n[image]"


@pytest.mark.asyncio
async def test_smart_finish_quotes_trigger_for_long_reply_image():
    matcher = SimpleNamespace(finish=mock.AsyncMock())

    await chat.smart_finish(matcher, "长" * 801, "msg-44")

    payload = matcher.finish.await_args.args[0]
    assert payload == "[reply:msg-44][image]"


@pytest.mark.asyncio
async def test_smart_finish_without_message_id_keeps_text_fallback():
    matcher = SimpleNamespace(finish=mock.AsyncMock())

    await chat.smart_finish(matcher, "普通回复")

    assert matcher.finish.await_args.args[0] == "普通回复"

"""测试 chat.py 中抽出的核心辅助函数。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from nonebot_plugin_akito.handlers import chat, chat_pipeline


def _incoming_turn(**overrides):
    values = {
        "session_key": "group_1001",
        "message_id": "msg-1",
        "user_id": "12345",
        "group_id": 1001,
        "sender_nickname": "测试群友",
        "plain_text_content": "你好",
        "has_image": False,
        "current_image_identity": "",
        "image_analysis": None,
        "has_reply": False,
        "reply_target_is_toya": False,
        "origin_sender": "",
    }
    values.update(overrides)
    return chat_pipeline.IncomingTurn(**values)


def _prepared_turn(**overrides):
    values = {
        "turn": _incoming_turn(),
        "user_mem": {"history": [], "temp_implants": [], "long_term_facts": []},
        "messages_list": [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "[测试群友(12345)]: 你好"},
        ],
        "tagged_user_msg_for_llm": "[测试群友(12345)]: 你好",
        "tagged_user_msg_for_history": "[测试群友(12345)]: 你好",
        "is_toya_context": False,
        "search_mode": "local",
        "query_intent": SimpleNamespace(intent="mention", explicit_search=False, query=""),
    }
    values.update(overrides)
    return chat_pipeline.PreparedTurn(**values)


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


def test_pipeline_gate_preserves_sleep_ignore_and_empty_message_paths():
    with mock.patch.object(chat_pipeline, "check_sleep_status", return_value=(True, "ignore")):
        ignored = chat_pipeline.decide_gate(_incoming_turn())

    assert ignored.skip_send is True
    assert ignored.text is None

    with mock.patch.object(chat_pipeline, "check_sleep_status", return_value=(False, "")):
        empty = chat_pipeline.decide_gate(_incoming_turn(plain_text_content=""))

    assert empty.skip_send is False
    assert empty.text == "干嘛……"
    assert empty.delay_seconds == 0


@pytest.mark.asyncio
async def test_pipeline_collect_turn_strips_trigger_and_keeps_event_identity():
    event = SimpleNamespace(
        reply=None,
        sender=SimpleNamespace(card="群名片", nickname="昵称"),
        group_id=1001,
        message_id="msg-9",
        get_user_id=lambda: "12345",
    )
    with mock.patch.object(chat_pipeline, "get_memory_key", return_value="group_1001"):
        result = await chat_pipeline.collect_turn_input(event, SimpleNamespace(), "小彰 你好")

    assert result.session_key == "group_1001"
    assert result.message_id == "msg-9"
    assert result.sender_nickname == "群名片"
    assert result.plain_text_content == "你好"
    assert result.has_image is False
    assert len(result.request_id) == 12


@pytest.mark.asyncio
async def test_pipeline_prepare_turn_preserves_message_order_and_prompt_input():
    user_mem = {
        "history": [{"role": "assistant", "content": '{"reply":"旧回复"}'}],
        "temp_implants": [],
        "long_term_facts": ["[08-01] 喜欢咖啡"],
    }
    shared_context = SimpleNamespace(
        relationship_match=None,
        persona="PERSONA",
        script_examples="EXAMPLES",
        pjsk_block="PJSK",
        song_memories="SONG_MEMORY",
        song_mention="SONG_MENTION",
    )
    query_intent = SimpleNamespace(intent="mention", explicit_search=False, query="")
    with (
        mock.patch.object(chat_pipeline, "get_daily_activity", return_value="DAILY"),
        mock.patch.object(chat_pipeline, "get_festival_buff", return_value="FESTIVAL"),
        mock.patch.object(chat_pipeline, "get_morning_run_buff", return_value="MORNING"),
        mock.patch.object(chat_pipeline, "get_sleep_buffer_buff", return_value="SLEEP_BUFFER"),
        mock.patch.object(chat_pipeline, "get_user_memory", return_value=user_mem),
        mock.patch.object(
            chat_pipeline,
            "build_shared_prompt_context",
            new=mock.AsyncMock(return_value=shared_context),
        ),
        mock.patch.object(chat_pipeline, "get_group_context", new=mock.AsyncMock(return_value="GROUP")),
        mock.patch.object(chat_pipeline, "build_time_gap_prompt", return_value=""),
        mock.patch.object(chat_pipeline, "classify_query_intent", return_value=query_intent),
        mock.patch.object(chat, "build_director_note", return_value={}),
        mock.patch.object(chat, "_build_final_system_prompt", return_value="SYSTEM") as prompt_mock,
    ):
        result = await chat_pipeline.prepare_turn(_incoming_turn(), "清醒提示")

    assert result.search_mode == "local"
    assert result.messages_list == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "assistant", "content": '{"reply":"旧回复"}'},
        {"role": "user", "content": "[测试群友(12345)]: 你好"},
    ]
    prompt_mock.assert_called_once()
    assert prompt_mock.call_args.kwargs["sleep_instruction"] == "清醒提示"
    assert prompt_mock.call_args.kwargs["long_term_memory_text"] == "[08-01] 喜欢咖啡"


@pytest.mark.asyncio
async def test_pipeline_dispatch_local_calls_standard_model_once():
    prepared = _prepared_turn()
    with (
        mock.patch.object(chat_pipeline, "call_deepseek_api", new=mock.AsyncMock(return_value="LOCAL")) as call_mock,
        mock.patch.object(chat_pipeline, "call_deepseek_api_agent", new=mock.AsyncMock()) as agent_mock,
        mock.patch.object(chat_pipeline, "smart_search", new=mock.AsyncMock()) as search_mock,
    ):
        result = await chat_pipeline._dispatch_model(prepared)

    assert result == "LOCAL"
    call_mock.assert_awaited_once_with(prepared.messages_list, force_json=True)
    agent_mock.assert_not_awaited()
    search_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_dispatch_forced_search_injects_result_before_generation():
    prepared = _prepared_turn(
        search_mode="forced",
        query_intent=SimpleNamespace(intent="web_search", explicit_search=True, query="东京天气"),
    )
    with (
        mock.patch.object(chat_pipeline, "smart_search", new=mock.AsyncMock(return_value="晴天")) as search_mock,
        mock.patch.object(chat_pipeline, "call_deepseek_api", new=mock.AsyncMock(return_value="FORCED")) as call_mock,
    ):
        result = await chat_pipeline._dispatch_model(prepared)

    assert result == "FORCED"
    search_mock.assert_awaited_once_with("东京天气")
    call_mock.assert_awaited_once_with(prepared.messages_list, force_json=True)
    assert "晴天" in prepared.messages_list[-1]["content"]
    assert "别照着原文念" in prepared.messages_list[-1]["content"]


@pytest.mark.asyncio
async def test_pipeline_dispatch_agent_preserves_tool_call_round_trip():
    prepared = _prepared_turn(
        search_mode="agent",
        query_intent=SimpleNamespace(intent="web_search", explicit_search=False, query="赛事结果"),
    )
    tool_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="search_internet", arguments='{"query":"赛事结果"}'),
    )
    agent_message = SimpleNamespace(tool_calls=[tool_call], content=None)
    with (
        mock.patch.object(
            chat_pipeline,
            "call_deepseek_api_agent",
            new=mock.AsyncMock(return_value=agent_message),
        ) as agent_mock,
        mock.patch.object(chat_pipeline, "smart_search", new=mock.AsyncMock(return_value="冠军信息")) as search_mock,
        mock.patch.object(chat_pipeline, "call_deepseek_api", new=mock.AsyncMock(return_value="AGENT")) as call_mock,
    ):
        result = await chat_pipeline._dispatch_model(prepared)

    assert result == "AGENT"
    agent_mock.assert_awaited_once_with(prepared.messages_list, tools=chat.AGENT_TOOLS)
    search_mock.assert_awaited_once_with("赛事结果")
    call_mock.assert_awaited_once_with(prepared.messages_list, force_json=True)
    assert prepared.messages_list[-2]["tool_calls"][0]["id"] == "call-1"
    assert prepared.messages_list[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "冠军信息",
    }


@pytest.mark.asyncio
async def test_pipeline_post_process_keeps_memory_and_ooc_behavior():
    prepared = _prepared_turn()
    with mock.patch.object(chat_pipeline, "save_memory") as save_mock:
        result = await chat_pipeline.post_process_reply(
            prepared,
            chat_pipeline.ChatReply(text="[[记下: 喜欢咖啡]]绘名姐啊喂", inner_os="记住了"),
        )

    assert result == chat_pipeline.ChatReply(text="绘名啊", inner_os="记住了")
    assert prepared.user_mem["long_term_facts"][0].endswith("喜欢咖啡")
    save_mock.assert_called_once_with()


@pytest.mark.asyncio
async def test_pipeline_post_process_regenerates_duplicate_reply():
    prepared = _prepared_turn(
        user_mem={
            "history": [{"role": "assistant", "content": '{"reply":"重复回复"}'}],
            "temp_implants": [],
            "long_term_facts": [],
        }
    )
    with mock.patch.object(
        chat_pipeline,
        "call_deepseek_api",
        new=mock.AsyncMock(return_value='{"inner_os":"换个思路","dialogue":"新的说法"}'),
    ) as call_mock:
        result = await chat_pipeline.post_process_reply(
            prepared,
            chat_pipeline.ChatReply(text="重复回复", inner_os="第一次想法"),
        )

    assert result == chat_pipeline.ChatReply(text="新的说法", inner_os="换个思路")
    call_mock.assert_awaited_once_with(prepared.messages_list, force_json=True)
    assert "绝对不能重复" in prepared.messages_list[-1]["content"]


@pytest.mark.asyncio
async def test_pipeline_post_process_rescues_broken_regenerated_json():
    prepared = _prepared_turn(
        user_mem={
            "history": [{"role": "assistant", "content": '{"reply":"重复回复"}'}],
            "temp_implants": [],
            "long_term_facts": [],
        }
    )
    raw_result = '{"inner_os":"换个思路","reply":"救援后的说法","bad":"未闭合}'
    with mock.patch.object(
        chat_pipeline,
        "call_deepseek_api",
        new=mock.AsyncMock(return_value=raw_result),
    ):
        result = await chat_pipeline.post_process_reply(
            prepared,
            chat_pipeline.ChatReply(text="重复回复", inner_os="第一次想法"),
        )

    assert result == chat_pipeline.ChatReply(text="救援后的说法", inner_os="")


@pytest.mark.asyncio
async def test_pipeline_post_process_passes_toya_context_to_regenerated_reply_parser():
    prepared = _prepared_turn(
        user_mem={
            "history": [{"role": "assistant", "content": '{"reply":"重复回复"}'}],
            "temp_implants": [],
            "long_term_facts": [],
        },
        is_toya_context=True,
    )
    raw_result = '{"action":"递过去","dialogue":"换一种说法。"}'
    with (
        mock.patch.object(
            chat_pipeline,
            "call_deepseek_api",
            new=mock.AsyncMock(return_value=raw_result),
        ),
        mock.patch.object(
            chat,
            "_parse_model_reply",
            return_value=("(递过去)换一种说法。", "第二次想法"),
        ) as parse_mock,
    ):
        result = await chat_pipeline.post_process_reply(
            prepared,
            chat_pipeline.ChatReply(text="重复回复", inner_os="第一次想法"),
        )

    assert result == chat_pipeline.ChatReply(text="(递过去)换一种说法。", inner_os="第二次想法")
    parse_mock.assert_called_once_with(raw_result, True)


@pytest.mark.asyncio
async def test_pipeline_commit_updates_history_and_group_records_once():
    prepared = _prepared_turn()
    reply = chat_pipeline.ChatReply(text="回复内容", inner_os="真实想法")
    with (
        mock.patch.object(chat_pipeline, "AKITO_STATUS", {}),
        mock.patch.object(chat_pipeline, "save_memory") as save_mock,
        mock.patch.object(chat_pipeline, "record_bot_response") as response_mock,
        mock.patch.object(chat_pipeline, "record_bot_message", new=mock.AsyncMock()) as message_mock,
    ):
        await chat_pipeline.commit_turn(prepared, reply, "bot-1")
        assert chat_pipeline.AKITO_STATUS["last_trigger_user"] == "12345"

    assert prepared.user_mem["history"] == [
        {"role": "user", "content": "[测试群友(12345)]: 你好"},
        {"role": "assistant", "content": '{"inner_os": "真实想法", "reply": "回复内容"}'},
    ]
    save_mock.assert_called_once_with()
    response_mock.assert_called_once_with(1001)
    message_mock.assert_awaited_once_with(1001, "回复内容", "bot-1")


@pytest.mark.asyncio
async def test_pipeline_run_stops_before_prepare_for_silent_gate():
    turn = _incoming_turn()
    with (
        mock.patch.object(chat_pipeline, "collect_turn_input", new=mock.AsyncMock(return_value=turn)),
        mock.patch.object(
            chat_pipeline,
            "decide_gate",
            return_value=chat_pipeline.GateDecision(
                text=None,
                delay_seconds=0,
                skip_send=True,
                sleep_instruction="",
            ),
        ),
        mock.patch.object(chat_pipeline, "prepare_turn", new=mock.AsyncMock()) as prepare_mock,
    ):
        result = await chat_pipeline.run_chat_turn(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

    assert result == chat_pipeline.PipelineResult(text=None, delay_seconds=0, finish_silently=True)
    prepare_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_handler_uses_matcher_finish_for_silent_pipeline_result():
    event = SimpleNamespace(message_id="msg-silent")
    finish_error = chat.FinishedException()
    with (
        mock.patch.object(chat, "get_memory_key", return_value="group_1001"),
        mock.patch.object(
            chat,
            "run_chat_turn",
            new=mock.AsyncMock(
                return_value=chat_pipeline.PipelineResult(
                    text=None,
                    delay_seconds=0,
                    finish_silently=True,
                )
            ),
        ),
        mock.patch.object(chat.chat, "finish", new=mock.AsyncMock(side_effect=finish_error)) as finish_mock,
    ):
        with pytest.raises(chat.FinishedException):
            await chat._(event, SimpleNamespace(), SimpleNamespace())

    finish_mock.assert_awaited_once_with()

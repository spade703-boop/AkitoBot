"""Tests for task-neutral prompt context assembly."""

from unittest import mock

import pytest

from nonebot_plugin_akito.core import prompt_builder
from nonebot_plugin_akito.core.context import RelationshipMatch
from nonebot_plugin_akito.core.retrieval import RetrievalContext


@pytest.mark.asyncio
async def test_build_shared_prompt_context_gathers_all_fragments_once():
    retrieval_ctx = RetrievalContext(
        original_query="冬弥最近怎么样",
        query="冬弥最近怎么样 青柳冬弥 近况",
        expanded_query="青柳冬弥 近况",
    )
    relationship_match = RelationshipMatch(keyword="冬弥", content="冬弥是彰人的搭档。")

    with mock.patch.object(
        prompt_builder,
        "build_retrieval_context",
        new=mock.AsyncMock(return_value=retrieval_ctx),
    ) as build_mock:
        with mock.patch.object(
            prompt_builder,
            "get_relevant_examples",
            new=mock.AsyncMock(return_value="剧本片段"),
        ) as examples_mock:
            with mock.patch.object(
                prompt_builder,
                "get_relevant_pjsk",
                new=mock.AsyncMock(return_value="PJSK 片段"),
            ) as pjsk_mock:
                with mock.patch.object(prompt_builder, "get_base_persona", return_value="基础人设"):
                    with mock.patch.object(
                        prompt_builder,
                        "find_relationship_match",
                        return_value=relationship_match,
                    ):
                        with mock.patch.object(prompt_builder, "get_song_memories", return_value="歌曲清单"):
                            with mock.patch.object(prompt_builder, "get_song_mention", return_value="歌曲命中"):
                                result = await prompt_builder.build_shared_prompt_context("冬弥最近怎么样")

    build_mock.assert_awaited_once_with("冬弥最近怎么样", enable_expansion=True)
    examples_mock.assert_awaited_once_with("冬弥最近怎么样", 5, retrieval_ctx=retrieval_ctx)
    pjsk_mock.assert_awaited_once_with("冬弥最近怎么样", 6, retrieval_ctx=retrieval_ctx)
    assert result == prompt_builder.SharedPromptContext(
        persona="基础人设",
        relationship_match=relationship_match,
        script_examples="剧本片段",
        pjsk_block="PJSK 片段",
        song_memories="歌曲清单",
        song_mention="歌曲命中",
    )


@pytest.mark.asyncio
async def test_build_shared_prompt_context_reuses_supplied_retrieval_context():
    retrieval_ctx = RetrievalContext(original_query="早", query="早")

    with mock.patch.object(prompt_builder, "build_retrieval_context", new=mock.AsyncMock()) as build_mock:
        with mock.patch.object(
            prompt_builder,
            "get_relevant_examples",
            new=mock.AsyncMock(return_value=""),
        ) as examples_mock:
            with mock.patch.object(
                prompt_builder,
                "get_relevant_pjsk",
                new=mock.AsyncMock(return_value=""),
            ) as pjsk_mock:
                with mock.patch.object(prompt_builder, "get_base_persona", return_value="基础人设"):
                    with mock.patch.object(prompt_builder, "find_relationship_match", return_value=None):
                        with mock.patch.object(prompt_builder, "get_song_memories", return_value=""):
                            with mock.patch.object(prompt_builder, "get_song_mention", return_value=""):
                                result = await prompt_builder.build_shared_prompt_context(
                                    "早",
                                    retrieval_ctx=retrieval_ctx,
                                    script_limit=2,
                                    pjsk_limit=3,
                                )

    build_mock.assert_not_awaited()
    examples_mock.assert_awaited_once_with("早", 2, retrieval_ctx=retrieval_ctx)
    pjsk_mock.assert_awaited_once_with("早", 3, retrieval_ctx=retrieval_ctx)
    assert result.relationship_match is None
    assert result.script_examples == ""
    assert result.pjsk_block == ""


def test_render_json_schema_preserves_order_and_escapes_dynamic_values():
    schema = prompt_builder.JsonSchemaSpec(
        instruction="只输出 JSON",
        fields=(
            prompt_builder.JsonFieldSpec("inner_os", '说"你好"'),
            prompt_builder.JsonFieldSpec("evidence", ["原话1", "原话2"]),
        ),
    )

    result = prompt_builder.render_json_schema(schema)

    assert result.splitlines() == [
        "只输出 JSON",
        "{",
        '  "inner_os": "说\\"你好\\"",',
        '  "evidence": ["原话1", "原话2"]',
        "}",
    ]


def test_render_prompt_frame_uses_the_fixed_five_section_order():
    frame = prompt_builder.PromptFrame(
        system_header="HEADER",
        environment_blocks=("ENV",),
        role_knowledge_blocks=("ROLE",),
        task_context_blocks=("CONTEXT",),
        task_rule_blocks=("RULE",),
    )
    schema = prompt_builder.JsonSchemaSpec(
        fields=(prompt_builder.JsonFieldSpec("reply", "REPLY"),)
    )

    result = prompt_builder.render_prompt_frame(frame, schema)
    headings = ["环境与状态", "角色与知识", "当前任务上下文", "任务规则", "强制输出格式 (JSON)"]

    assert all(value in result for value in ("HEADER", "ENV", "ROLE", "CONTEXT", "RULE", '"reply": "REPLY"'))
    assert [result.index(heading) for heading in headings] == sorted(result.index(heading) for heading in headings)
    normalized = " ".join(result.split())
    assert (
        "HEADER # 1. 环境与状态 ENV # 2. 角色与知识 ROLE # 3. 当前任务上下文 CONTEXT "
        "# 4. 任务规则 RULE # 5. 强制输出格式 (JSON) { \"reply\": \"REPLY\" }"
        in normalized
    )


def test_task_renderers_keep_distinct_json_field_sets():
    main = prompt_builder.render_main_chat_prompt(
        system_header="HEADER",
        current_time="现在",
        daily_status="状态",
        toya_anchor="",
        time_gap_awareness="",
        festival_buff="",
        morning_run_buff="",
        sleep_buffer_buff="",
        relationship_context="",
        group_context="",
        interact_instruction="",
        referenced_relationship_instruction="",
        base_persona="人设",
        script_examples="",
        pjsk_block="",
        song_memories="",
        long_term_memory_text="",
        reality_overwrite_instruction="",
        acting_guide="",
        sleep_instruction="",
        fact_grounding_instruction="",
        vitality_guide="",
        memory_capture_rule="",
        tone_limiter="",
        schema_inner_os="OS",
        schema_action="ACTION",
        schema_dialogue="DIALOGUE",
    )
    impression_analysis = prompt_builder.render_impression_analysis_prompt(
        target_name="小明",
        recent_reply_limit=8,
        relationship_context="自我归因与冬弥关系资料",
    )
    impression_reply = prompt_builder.render_impression_reply_prompt(
        persona="人设",
        state_overlay_prompt="",
        target_name="小明",
        is_querying_other=False,
        specific_max_length=100,
        limited_max_length=60,
        relationship_context="自我归因与冬弥关系资料",
    )
    auto = prompt_builder.render_auto_chat_prompt(
        persona="人设",
        time_str="现在",
        toya_anchor="",
        scene_desc="消息",
        group_context="背景",
        relation_info="",
        song_info="",
        script_examples="",
        pjsk_block="",
        cool_guy_filter="",
        task_logic="规则",
        inner_os_guide="OS",
    )

    assert '"action": "ACTION"' in main
    assert '"dialogue": "DIALOGUE"' in main
    assert '"evidence": [' in impression_analysis
    assert "包含2-4个字符串的 JSON 数组" in impression_analysis
    assert "自我归因与冬弥关系资料" in impression_analysis
    assert '"observations": [' in impression_analysis
    assert '"replies": [' in impression_reply
    assert "自我归因与冬弥关系资料" in impression_reply
    assert '"action":' not in impression_analysis
    assert '"evidence":' not in impression_reply
    assert '"anchor": "若要回复' in auto
    assert '"reply": "你实际发在群里的话' in auto

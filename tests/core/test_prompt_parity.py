"""Cross-entry regression tests for shared Prompt fragments."""

from nonebot_plugin_akito.core.context import RelationshipMatch, format_relationship_context
from nonebot_plugin_akito.core.prompt_builder import SharedPromptContext
from nonebot_plugin_akito.core.retrieval import RetrievalContext
from nonebot_plugin_akito.features import impression
from nonebot_plugin_akito.handlers import chat


def _shared_context() -> SharedPromptContext:
    return SharedPromptContext(
        persona="PERSONA_FRAGMENT",
        relationship_match=RelationshipMatch(keyword="冬弥", content="RELATION_FRAGMENT"),
        script_examples="SCRIPT_FRAGMENT",
        pjsk_block="PJSK_FRAGMENT",
        song_memories="SONG_CATALOG_FRAGMENT",
        song_mention="SONG_MENTION_FRAGMENT",
        retrieval_context=RetrievalContext(original_query="冬弥", query="冬弥"),
    )


def _render_main_chat_prompt(context: SharedPromptContext) -> str:
    return chat._build_final_system_prompt(
        system_header="HEADER",
        current_time="上午8点00分",
        daily_status="练歌中",
        toya_anchor="",
        time_gap_awareness="",
        festival_buff="",
        morning_run_buff="",
        sleep_buffer_buff="",
        relationship_context=format_relationship_context(context.relationship_match),
        group_context="GROUP_CONTEXT",
        interact_instruction="INTERACT",
        referenced_relationship_instruction="",
        base_persona=context.persona,
        script_examples=context.script_examples,
        pjsk_block=context.pjsk_block,
        song_memories=context.song_memories + context.song_mention,
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


def _render_auto_chat_prompt(context: SharedPromptContext) -> str:
    return impression._build_auto_chat_system_prompt(
        persona=context.persona,
        time_str="上午8点00分",
        toya_anchor="",
        scene_desc="CURRENT_MESSAGE",
        group_context="GROUP_CONTEXT",
        relation_info=context.relationship_match.content if context.relationship_match else "",
        song_info=context.song_mention,
        script_examples=context.script_examples,
        pjsk_block=context.pjsk_block,
        cool_guy_filter="",
        task_logic="TASK_LOGIC",
        inner_os_guide="OS_GUIDE",
    )


def test_main_and_auto_chat_keep_shared_prompt_fragments_in_sync():
    context = _shared_context()
    main_prompt = _render_main_chat_prompt(context)
    auto_prompt = _render_auto_chat_prompt(context)

    for fragment in (
        context.persona,
        context.relationship_match.content,
        context.script_examples,
        context.pjsk_block,
        context.song_mention,
    ):
        assert fragment in main_prompt
        assert fragment in auto_prompt

    assert main_prompt.index(context.persona) < main_prompt.index(context.script_examples)
    assert main_prompt.index(context.script_examples) < main_prompt.index(context.pjsk_block)
    assert main_prompt.index(context.pjsk_block) < main_prompt.index(context.song_mention)

    assert auto_prompt.index(context.persona) < auto_prompt.index(context.script_examples)
    assert auto_prompt.index(context.script_examples) < auto_prompt.index(context.pjsk_block)
    assert auto_prompt.index(context.pjsk_block) < auto_prompt.index(context.song_mention)

    assert context.song_memories in main_prompt
    assert context.song_memories not in auto_prompt

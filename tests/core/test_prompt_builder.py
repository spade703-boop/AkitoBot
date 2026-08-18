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

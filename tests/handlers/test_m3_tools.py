from types import SimpleNamespace
from unittest import mock

import pytest

from nonebot_plugin_akito.handlers import chat_pipeline


def _prepared(mode="on", search_mode="agent"):
    turn = chat_pipeline.IncomingTurn(
        session_key="group_1",
        message_id="m1",
        user_id="u1",
        group_id=1,
        sender_nickname="群友",
        plain_text_content="今天的天气？",
        has_image=False,
        current_image_identity="",
        image_analysis=None,
        has_reply=False,
        reply_target_is_toya=False,
        origin_sender="",
        request_id="m3-test",
    )
    return chat_pipeline.PreparedTurn(
        turn=turn,
        user_mem={"history": [], "temp_implants": [], "long_term_facts": []},
        messages_list=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "今天的天气？"},
        ],
        tagged_user_msg_for_llm="今天的天气？",
        tagged_user_msg_for_history="今天的天气？",
        is_toya_context=False,
        search_mode=search_mode,
        query_intent=SimpleNamespace(intent="web_search", category="web_fact", query="天气"),
        rollout=SimpleNamespace(m3_tool_mode=mode),
    )


def _tool_message(query, call_id="call-1"):
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                type="function",
                function=SimpleNamespace(name="search_internet", arguments=f'{{"query":"{query}"}}'),
            )
        ],
    )


@pytest.mark.asyncio
async def test_m3_agent_returns_standard_tool_round_trip_with_sources():
    prepared = _prepared()
    with (
        mock.patch.object(chat_pipeline, "call_deepseek_api_agent", new=mock.AsyncMock(side_effect=[_tool_message("天气"), SimpleNamespace(content="最终答案", tool_calls=[])])),
        mock.patch.object(
            chat_pipeline,
            "smart_search_result",
            new=mock.AsyncMock(return_value={
                "name": "search",
                "status": "success",
                "query": "天气",
                "summary": "晴天",
                "content": "晴天",
                "sources": [{"title": "天气源", "url": "https://example.test/weather", "summary": "晴天"}],
                "latency_ms": 2,
            }),
        ),
    ):
        result = await chat_pipeline._dispatch_model_m3(prepared)

    assert result == "最终答案"
    assert prepared.messages_list[-1]["content"] == "今天的天气？"


@pytest.mark.asyncio
async def test_m3_rejects_duplicate_queries_without_second_search():
    prepared = _prepared()
    with (
        mock.patch.object(chat_pipeline, "call_deepseek_api_agent", new=mock.AsyncMock(side_effect=[_tool_message("天气", "one"), _tool_message("天气", "two"), SimpleNamespace(content="答案", tool_calls=[])])),
        mock.patch.object(chat_pipeline, "smart_search_result", new=mock.AsyncMock(return_value={"status": "empty", "name": "search", "query": "天气", "sources": [], "content": ""})) as search_mock,
    ):
        result = await chat_pipeline._dispatch_model_m3(prepared)

    assert result == "答案"
    search_mock.assert_awaited_once()

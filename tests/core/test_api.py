"""
测试 core/api.py 纯逻辑 —— LLM 响应 JSON 提取 / 字段救援 + rerank 门控与响应解析。

直接测试 extract_json_block / rescue_field 真函数
（chat.py 与 impression.py 共用的单一真相源），不再维护本地正则副本。
"""
import json
from unittest import mock

from nonebot_plugin_akito.core import api as api_module
from nonebot_plugin_akito.core.api import _parse_rerank_response, extract_json_block, rescue_field


def extract_json(raw: str) -> dict | None:
    """提取 JSON 块并解析；解析失败返回 None（与调用方的兜底语义一致）。"""
    try:
        return json.loads(extract_json_block(raw))
    except json.JSONDecodeError:
        return None


def rescue_dialogue(raw: str) -> str | None:
    """dialogue / reply 字段救援（chat.py 的调用形态）。"""
    return rescue_field(raw, "dialogue", "reply")


# ── extract_json 测试 ──────────────────────────────────────────────────────

def test_extract_valid_json_bare():
    """裸 JSON 对象直接解析成功。"""
    raw = '{"inner_os": "thinking...", "dialogue": "hello"}'
    result = extract_json(raw)
    assert result == {"inner_os": "thinking...", "dialogue": "hello"}


def test_extract_json_surrounded_by_text():
    """JSON 被前后文本包裹，正则提取后解析成功。"""
    raw = 'some prefix text \n{"inner_os": "嗯", "dialogue": "好"}\n some suffix'
    result = extract_json(raw)
    assert result == {"inner_os": "嗯", "dialogue": "好"}


def test_extract_json_with_nested_braces():
    """JSON 字符串值中包含花括号（如代码片段）。"""
    raw = '{"inner_os": "check", "dialogue": "用 { } 包裹代码"}'
    result = extract_json(raw)
    assert result["dialogue"] == "用 { } 包裹代码"


def test_extract_json_with_escaped_quotes():
    """dialogue 值中包含转义引号。"""
    raw = '{"inner_os": "ok", "dialogue": "他说 \\"你好\\""}'
    result = extract_json(raw)
    assert result["dialogue"] == '他说 "你好"'


def test_extract_json_invalid_returns_none():
    """无有效 JSON 时返回 None。"""
    raw = "just some plain text with no json at all"
    result = extract_json(raw)
    assert result is None


def test_extract_json_malformed_returns_none():
    """残缺 JSON（如截断）返回 None。"""
    raw = '{"inner_os": "thinking", "dialogue": "incomplete'
    result = extract_json(raw)
    assert result is None


# ── rescue_dialogue 测试 ───────────────────────────────────────────────────

def test_rescue_dialogue_from_truncated_json():
    """正则救援从截断 JSON 中提取 dialogue 值（值已完整但缺少闭合括号）。"""
    raw = '{"inner_os": "ok", "dialogue": "这是回复内容"}'
    result = rescue_dialogue(raw)
    assert result == "这是回复内容"


def test_rescue_reply_field():
    """正则救援也匹配 reply 字段名。"""
    raw = '{"inner_os": "ok", "reply": "用reply字段的回复"}'
    result = rescue_dialogue(raw)
    assert result == "用reply字段的回复"


def test_rescue_no_match_returns_none():
    """无 dialogue 或 reply 字段时救援失败。"""
    raw = '{"inner_os": "only os", "action": "nod"}'
    result = rescue_dialogue(raw)
    assert result is None


# ── 实际场景模拟 ───────────────────────────────────────────────────────────

def test_real_world_deepseek_response_format():
    """模拟 DeepSeek 实际返回格式：三字段 JSON。"""
    raw = (
        "一些前置说明文本\n"
        '{"inner_os": "他说得对，我应该认真回应", '
        '"action": "（点头）", '
        '"dialogue": "嗯，这件事交给我吧。"}\n'
        "后置补充"
    )
    result = extract_json(raw)
    assert result is not None
    assert result["inner_os"] == "他说得对，我应该认真回应"
    assert result["action"] == "（点头）"
    assert result["dialogue"] == "嗯，这件事交给我吧。"


def test_real_world_impression_response_format():
    """模拟 impression.py 的两字段格式。"""
    raw = '{"inner_os": "这个人很活跃", "reply": "他经常在群里聊天"}'
    result = extract_json(raw)
    assert result is not None
    assert result["inner_os"] == "这个人很活跃"
    assert result["reply"] == "他经常在群里聊天"


# ── rerank_documents 门控与降级 ────────────────────────────────────────────

async def test_rerank_documents_no_client_returns_none():
    """未配置 SILICONFLOW_API_KEY（embedding_client=None）→ 直接降级 None。"""
    with mock.patch.object(api_module, "embedding_client", None):
        result = await api_module.rerank_documents("query", ["doc"], top_n=1)
    assert result is None


async def test_rerank_documents_empty_inputs_return_none():
    """空 query / 空 documents → 门控直接 None（不触发 HTTP）。"""
    with mock.patch.object(api_module, "embedding_client", mock.MagicMock()):
        assert await api_module.rerank_documents("", ["doc"], top_n=1) is None
        assert await api_module.rerank_documents("   ", ["doc"], top_n=1) is None
        assert await api_module.rerank_documents("query", [], top_n=1) is None


async def test_rerank_documents_http_failure_returns_none():
    """HTTP 层任意异常 → None 不外抛。"""
    fake_aiohttp = mock.MagicMock()
    fake_aiohttp.ClientSession.side_effect = RuntimeError("连接失败")
    with mock.patch.object(api_module, "embedding_client", mock.MagicMock()):
        with mock.patch.object(api_module, "aiohttp", fake_aiohttp):
            result = await api_module.rerank_documents("query", ["d0", "d1"], top_n=2)
    assert result is None


# ── _parse_rerank_response 解析 ─────────────────────────────────────────────

def test_parse_rerank_response_happy():
    """标准返回 → [(index, score)] 按分降序。"""
    data = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.3}]}
    assert _parse_rerank_response(data, 2) == [(1, 0.9), (0, 0.3)]


def test_parse_rerank_response_sorts_desc():
    """不信任 API 顺序：乱序输入按分重排。"""
    data = {"results": [{"index": 0, "relevance_score": 0.2}, {"index": 1, "relevance_score": 0.8}]}
    assert _parse_rerank_response(data, 2) == [(1, 0.8), (0, 0.2)]


def test_parse_rerank_response_filters_invalid_entries():
    """坏条目（越界 / 非 int 下标 / 分数不可转 float / 非 dict）逐条跳过，保留合法项。"""
    data = {
        "results": [
            {"index": 5, "relevance_score": 0.9},
            {"index": "x", "relevance_score": 0.8},
            {"index": 1, "relevance_score": "bad"},
            {"index": 0, "relevance_score": 0.5},
            "not a dict",
        ]
    }
    assert _parse_rerank_response(data, 2) == [(0, 0.5)]


def test_parse_rerank_response_malformed_returns_none():
    """整体结构异常 / 全员非法 → None（调用方回退 cosine 顺序）。"""
    for bad in ({}, {"results": "x"}, {"results": []}, [], None, {"results": [{"index": 9, "relevance_score": 1.0}]}):
        assert _parse_rerank_response(bad, 2) is None

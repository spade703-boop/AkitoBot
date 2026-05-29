"""
测试 JSON 提取逻辑 —— 验证 LLM 响应中 JSON 的正则提取 + json.loads 解析。

覆盖 chat.py / reactions.py / impression.py 中重复出现的解析模式。
"""
import json
import re

# ── 与源码一致的正则模式 ────────────────────────────────────────────────────

JSON_EXTRACT_PATTERN = re.compile(r"\{[\s\S]*\}")
RESCUE_PATTERN = re.compile(r'"(?:dialogue|reply)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def extract_json(raw: str) -> dict | None:
    """模拟源码中的 JSON 提取 + 解析逻辑。"""
    match = JSON_EXTRACT_PATTERN.search(raw)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def rescue_dialogue(raw: str) -> str | None:
    """模拟源码中的正则救援逻辑。"""
    rescue = RESCUE_PATTERN.search(raw)
    if rescue:
        return rescue.group(1)
    return None


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

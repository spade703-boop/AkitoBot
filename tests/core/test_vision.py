"""测试 GLM-4.6V 视觉识别链路：解析降级、布尔特征裁决、格式化与两段式调用编排。

预处理依赖真实 PIL（conftest 中为 MagicMock 假件），因此 describe_image 类测试
一律 monkeypatch ``_prepare_image_payloads``，只单测纯决策函数与编排逻辑。
"""

from __future__ import annotations

import asyncio
import json
import types
from unittest import mock

from nonebot_plugin_akito.core import api


def _features(**overrides):
    base = {
        "orange_hair": False,
        "yellow_streak_bangs": False,
        "blue_gray_split_hair": False,
        "tear_mole": False,
        "full_blue_hair": False,
        "two_persons": False,
    }
    base.update(overrides)
    return base


def _parsed(scene="character_art", characters=None, confidence=0.9, **feature_overrides):
    return {
        "scene_label": scene,
        "characters": characters or [],
        "confidence": confidence,
        "features": _features(**feature_overrides),
        "summary": "测试画面",
        "ocr_text": "",
        "details": "无",
    }


def _fake_response(content, finish_reason="stop"):
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def _patch_vision(monkeypatch, create_mock):
    monkeypatch.setattr(
        api,
        "vision_client",
        types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create_mock))),
    )
    monkeypatch.setattr(api, "_prepare_image_payloads", lambda images, **kwargs: [("YWJj", "image/jpeg")])


# ── 裁决 _adjudicate ─────────────────────────────────────────────────────────


def test_adjudicate_akito_with_streak():
    result = api._adjudicate(_parsed(characters=["akito"], orange_hair=True, yellow_streak_bangs=True))
    assert result.character_label == "akito"


def test_adjudicate_akito_without_streak_is_none():
    # 模型在 characters 里硬称 akito，但布尔特征缺黄挑染：RP 标签降级，角色名仅留在描述层
    result = api._adjudicate(_parsed(characters=["akito"], orange_hair=True))
    assert result.character_label == "none"
    assert result.characters == ["akito"]


def test_adjudicate_full_blue_hair_forces_kaito_over_toya():
    result = api._adjudicate(_parsed(characters=["toya"], blue_gray_split_hair=True, full_blue_hair=True))
    assert result.character_label == "kaito"


def test_adjudicate_toya_split_hair():
    result = api._adjudicate(_parsed(confidence=0.8, blue_gray_split_hair=True))
    assert result.character_label == "toya"


def test_adjudicate_pair_requires_two_persons():
    both = dict(orange_hair=True, yellow_streak_bangs=True, blue_gray_split_hair=True)
    assert api._adjudicate(_parsed(two_persons=True, **both)).character_label == "pair"
    assert api._adjudicate(_parsed(**both)).character_label == "akito"


def test_adjudicate_low_confidence_gates_to_none():
    result = api._adjudicate(_parsed(confidence=0.5, orange_hair=True, yellow_streak_bangs=True))
    assert result.character_label == "none"


def test_adjudicate_handles_missing_fields():
    result = api._adjudicate({})
    assert result.character_label == "none"
    assert result.scene_label == "unknown"
    assert result.confidence == 0.0
    assert result.characters == []


def test_adjudicate_invalid_scene_label_becomes_unknown():
    result = api._adjudicate(_parsed(scene="梗图"))
    assert result.scene_label == "unknown"


def test_adjudicate_filters_invalid_character_ids():
    raw = ["akito", "akito", "蛤", 123, "miku", "toya", "ena", "rin", "len", "luka"]
    result = api._adjudicate(_parsed(characters=raw))
    assert result.characters == ["akito", "miku", "toya", "ena", "rin", "len"]  # 去重过滤后截 6


# ── 解析 _parse_vision_reply ─────────────────────────────────────────────────


def test_parse_vision_reply_json_in_prose():
    raw = '好的，分析如下：{"scene_label": "meme", "summary": "一张梗图"} 以上。'
    parsed = api._parse_vision_reply(raw)
    assert parsed["scene_label"] == "meme"
    assert parsed["summary"] == "一张梗图"


def test_parse_vision_reply_truncated_rescues_fields():
    raw = '{"scene_label": "meme", "summary": "一张梗图", "ocr_text": "哈哈哈", "details": "气氛'
    parsed = api._parse_vision_reply(raw)
    assert parsed["summary"] == "一张梗图"
    assert parsed["ocr_text"] == "哈哈哈"
    assert parsed["scene_label"] == "unknown"


def test_parse_vision_reply_plain_text_wraps_as_unknown():
    parsed = api._parse_vision_reply("这是一张看不出名堂的图")
    assert parsed["scene_label"] == "unknown"
    assert parsed["summary"] == "这是一张看不出名堂的图"


# ── 格式化 format_image_analysis_for_chat ────────────────────────────────────


def test_format_image_analysis_five_sections():
    analysis = api.ImageAnalysis(
        scene_label="character_art",
        character_label="akito",
        characters=["akito", "toya"],
        confidence=0.9,
        summary="彰人和冬弥的同人图",
        ocr_text="",
        details="氛围温馨",
    )
    text = api.format_image_analysis_for_chat(analysis)
    assert "【标签】：[彰人]" in text
    assert "【识别角色】：彰人、冬弥" in text
    assert "【画面核心】：彰人和冬弥的同人图" in text
    assert "【OCR提取】：无" in text
    assert "【关键细节】：氛围温馨" in text


def test_format_image_analysis_falls_back_to_scene_tag():
    analysis = api.ImageAnalysis(scene_label="food", summary="一碗拉面")
    text = api.format_image_analysis_for_chat(analysis)
    assert "【标签】：[美食]" in text
    assert "【识别角色】：无" in text


def test_format_image_analysis_caps_long_ocr():
    analysis = api.ImageAnalysis(scene_label="screenshot_or_text", ocr_text="字" * 2000)
    text = api.format_image_analysis_for_chat(analysis)
    ocr_line = next(line for line in text.splitlines() if line.startswith("【OCR提取】"))
    assert len(ocr_line) <= 520


# ── 预处理纯决策函数 ─────────────────────────────────────────────────────────


def test_sniff_image_mime():
    assert api._sniff_image_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert api._sniff_image_mime(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert api._sniff_image_mime(b"GIF89a-data") == "image/gif"
    assert api._sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPrest") == "image/webp"
    assert api._sniff_image_mime(b"garbage-bytes") is None


def test_should_passthrough_rules():
    assert api._should_passthrough("image/jpeg", 1000, 100_000, False, 1536)
    assert api._should_passthrough("image/png", 1536, 3_500_000, False, 1536)
    assert not api._should_passthrough("image/jpeg", 2000, 100_000, False, 1536)  # 超边长
    assert not api._should_passthrough("image/gif", 1000, 100_000, True, 1536)    # 动图
    assert not api._should_passthrough("image/webp", 1000, 100_000, False, 1536)  # 非透传格式
    assert not api._should_passthrough(None, 1000, 100_000, False, 1536)
    assert not api._should_passthrough("image/jpeg", 1000, 4_000_000, False, 1536)  # 超体积


def test_select_frame_indices():
    assert api._select_frame_indices(1) == [0]
    assert api._select_frame_indices(2) == [0, 1]
    assert api._select_frame_indices(3) == [0, 1, 2]
    assert api._select_frame_indices(10) == [0, 4, 9]  # 首/中/尾均匀采样
    assert api._select_frame_indices(10, max_frames=1) == [0]


# ── 二轮触发与编排 ───────────────────────────────────────────────────────────


def test_should_run_ocr_pass():
    assert api._should_run_ocr_pass("screenshot_or_text", "stop")
    assert api._should_run_ocr_pass("meme", "length")  # 首轮被截断也补 OCR
    assert not api._should_run_ocr_pass("meme", "stop")
    assert not api._should_run_ocr_pass("meme", None)


def test_describe_image_happy_path_single_call(monkeypatch):
    create = mock.AsyncMock(return_value=_fake_response(json.dumps(_parsed(scene="meme"))))
    _patch_vision(monkeypatch, create)

    analysis = asyncio.run(api.describe_image([b"img"]))

    assert analysis is not None
    assert analysis.scene_label == "meme"
    assert create.await_count == 1
    content = create.await_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert [part["type"] for part in content[1:]] == ["image_url"]
    assert create.await_args.kwargs["extra_body"] == {"thinking": {"type": api._VISION_THINKING}}


def test_describe_image_multi_image_entries(monkeypatch):
    create = mock.AsyncMock(return_value=_fake_response(json.dumps(_parsed(scene="meme"))))
    _patch_vision(monkeypatch, create)
    monkeypatch.setattr(
        api, "_prepare_image_payloads",
        lambda images, **kwargs: [("YWJj", "image/jpeg"), ("ZGVm", "image/png")],
    )

    asyncio.run(api.describe_image([b"img1", b"img2"]))

    content = create.await_args.kwargs["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "image_url", "image_url"]
    assert "data:image/png;base64,ZGVm" in content[2]["image_url"]["url"]


def test_describe_image_screenshot_triggers_second_ocr_call(monkeypatch):
    first = _fake_response(json.dumps(_parsed(scene="screenshot_or_text")))
    second = _fake_response("提取出来的完整长文字")
    create = mock.AsyncMock(side_effect=[first, second])
    _patch_vision(monkeypatch, create)

    analysis = asyncio.run(api.describe_image([b"img"]))

    assert create.await_count == 2
    assert analysis.ocr_text == "提取出来的完整长文字"
    ocr_kwargs = create.await_args_list[1].kwargs
    assert ocr_kwargs["extra_body"] == {"thinking": {"type": api._OCR_THINKING}}


def test_describe_image_ocr_pass_failure_keeps_first_result(monkeypatch):
    parsed = _parsed(scene="screenshot_or_text")
    parsed["ocr_text"] = "首轮粗提取"
    create = mock.AsyncMock(side_effect=[_fake_response(json.dumps(parsed)), RuntimeError("boom")])
    _patch_vision(monkeypatch, create)

    analysis = asyncio.run(api.describe_image([b"img"]))

    assert analysis is not None
    assert analysis.ocr_text == "首轮粗提取"


def test_describe_image_returns_none_when_payload_fails(monkeypatch):
    create = mock.AsyncMock()
    _patch_vision(monkeypatch, create)
    monkeypatch.setattr(api, "_prepare_image_payloads", lambda images, **kwargs: [])

    assert asyncio.run(api.describe_image([b"bad"])) is None
    assert create.await_count == 0


def test_describe_image_timeout_returns_none(monkeypatch):
    create = mock.AsyncMock(side_effect=asyncio.TimeoutError())
    _patch_vision(monkeypatch, create)

    assert asyncio.run(api.describe_image([b"img"])) is None

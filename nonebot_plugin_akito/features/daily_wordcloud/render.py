"""Render daily word-cloud reports as a single image."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from nonebot_plugin_htmlrender import html_to_pic

from .._shared import FONT_PATH
from .analysis import MAX_WORDS

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "xml")),
)
_RENDER_SEMAPHORE = asyncio.Semaphore(2)

_WORD_COLORS = ("#ff9750", "#ffb27d", "#6faeff", "#9bd7ff", "#fff8ef", "#eef4ff")
_VOLUME_COLORS = ("#ff9750", "#ffb27d", "#6faeff", "#9bd7ff", "#d1d5db")
_OTHER_VOLUME_COLOR = "#667085"
MESSAGE_VOLUME_VISIBLE_USERS = 5
COMMAND_HELP_ITEMS = (
    {"category": "查看类", "command": "今日群聊词云 / 实时群聊词云 / 群聊词云 今天", "description": "查看今天的实时词云。"},
    {"category": "查看类", "command": "群聊词云 [YYYY-MM-DD]", "description": "查看指定日期的词云日报。"},
    {"category": "管理类", "command": "重算群聊词云 YYYY-MM-DD", "description": "按本地消息重新生成日报。"},
    {"category": "管理类", "command": "回填群聊词云 YYYY-MM-DD", "description": "从历史消息库回填日报。"},
    {"category": "管理类", "command": "测试群聊词云", "description": "预览词云图片布局。"},
    {"category": "过滤设置", "command": "词云屏蔽词 查看", "description": "查看当前全局屏蔽词。"},
    {"category": "过滤设置", "command": "词云屏蔽词 添加 词1 词2", "description": "新增全局屏蔽词。"},
    {"category": "过滤设置", "command": "词云屏蔽词 取消 词1 词2", "description": "移除全局屏蔽词。"},
    {"category": "过滤设置", "command": "词云排除用户 查看", "description": "查看当前全局排除用户。"},
    {"category": "过滤设置", "command": "词云排除用户 添加 QQ号1 QQ号2", "description": "新增全局排除用户。"},
    {"category": "过滤设置", "command": "词云排除用户 取消 QQ号1 QQ号2", "description": "移除全局排除用户。"},
    {"category": "其他", "command": "词云帮助 / 词云指令 / 群聊词云帮助", "description": "查看这份指令列表。"},
)
COMMAND_HELP_NOTES = (
    "实时词云对目标群全体成员开放；同一群共享 30 分钟冷却。",
    "除实时查看外，日报维护、测试和过滤管理指令仅限超管。",
    "查询指令省略日期时查看昨天；重算仅适用于原始消息留存窗口内的日期。",
    "屏蔽词和排除用户为全局设置；已注册 Bot 指令及其参数会自动排除。",
)


def qq_avatar_uri(user_id: str) -> str:
    return f"https://q.qlogo.cn/g?b=qq&nk={user_id}&s=100"


def _wordcloud_data_uri(report: dict[str, Any]) -> str:
    from wordcloud import WordCloud

    frequencies = {str(word): int(count) for word, count in report.get("frequencies", []) if int(count) > 0}
    if not frequencies:
        return ""

    seed_source = f"{report.get('group_id', '')}:{report.get('report_date', '')}".encode()
    random_seed = int.from_bytes(hashlib.sha256(seed_source).digest()[:4], "big")

    def color_function(*_args, random_state=None, **_kwargs):
        chooser = random_state if random_state is not None else __import__("random")
        return chooser.choice(_WORD_COLORS)

    cloud = WordCloud(
        width=820,
        height=390,
        background_color=None,
        mode="RGBA",
        font_path=str(FONT_PATH),
        max_words=MAX_WORDS,
        prefer_horizontal=0.9,
        relative_scaling=0.45,
        min_font_size=13,
        margin=3,
        collocations=False,
        random_state=random_seed,
        color_func=color_function,
    ).generate_from_frequencies(frequencies)
    buffer = io.BytesIO()
    cloud.to_image().save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _message_volume_data(report: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw_items = []
    for item in report.get("message_volume", []):
        try:
            count = int(item.get("count", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if count <= 0:
            continue
        user_id = str(item.get("user_id", ""))
        nickname = str(item.get("nickname", "")).strip() or f"用户{user_id}"
        raw_items.append({"user_id": user_id, "nickname": nickname, "count": count})

    raw_items.sort(key=lambda item: (-item["count"], item["user_id"]))
    if not raw_items:
        return [], ""

    visible_items = raw_items[:MESSAGE_VOLUME_VISIBLE_USERS]
    other_count = sum(item["count"] for item in raw_items[MESSAGE_VOLUME_VISIBLE_USERS:])
    if other_count:
        visible_items.append({"user_id": "", "nickname": "其他", "count": other_count})

    total = sum(item["count"] for item in visible_items)
    offset = 0.0
    chart_segments = []
    rendered_items = []
    for index, item in enumerate(visible_items):
        percentage = item["count"] / total * 100
        end = 100.0 if index == len(visible_items) - 1 else offset + percentage
        color = _OTHER_VOLUME_COLOR if not item["user_id"] else _VOLUME_COLORS[index % len(_VOLUME_COLORS)]
        rendered_items.append(
            {
                **item,
                "color": color,
                "percentage": f"{percentage:.1f}",
                "initial": item["nickname"][:1],
                "avatar": qq_avatar_uri(item["user_id"]) if item["user_id"] else "",
            }
        )
        chart_segments.append(f"{color} {offset:.4f}% {end:.4f}%")
        offset = end
    return rendered_items, f"background: conic-gradient({', '.join(chart_segments)});"


def build_page_data(report: dict[str, Any]) -> dict[str, Any]:
    top_words = []
    for index, item in enumerate(report.get("top_words", []), start=1):
        contributors = []
        for contributor in item.get("contributors", []):
            user_id = str(contributor.get("user_id", ""))
            nickname = str(contributor.get("nickname", "")).strip() or f"用户{user_id}"
            contributors.append(
                {
                    **contributor,
                    "user_id": user_id,
                    "nickname": nickname,
                    "initial": nickname[:1],
                    "avatar": qq_avatar_uri(user_id),
                }
            )
        top_words.append({**item, "rank": index, "contributors": contributors})

    message_volume, volume_chart_style = _message_volume_data(report)
    return {
        **report,
        "cloud_image": _wordcloud_data_uri(report),
        "message_volume": message_volume,
        "volume_chart_style": volume_chart_style,
        "top_words": top_words,
    }


async def render_report(report: dict[str, Any]) -> bytes:
    data = build_page_data(report)
    html = _TEMPLATE_ENV.get_template("daily_report.html").render(**data)
    async with _RENDER_SEMAPHORE:
        return await html_to_pic(
            html,
            viewport={"width": 900, "height": 100},
            type="jpeg",
            quality=86,
        )


async def render_command_help() -> bytes:
    html = _TEMPLATE_ENV.get_template("command_help.html").render(
        items=COMMAND_HELP_ITEMS,
        notes=COMMAND_HELP_NOTES,
        max_words=MAX_WORDS,
    )
    async with _RENDER_SEMAPHORE:
        return await html_to_pic(
            html,
            viewport={"width": 900, "height": 100},
            type="jpeg",
            quality=86,
        )

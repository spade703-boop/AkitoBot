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

_WORD_COLORS = ("#ff9f57", "#ffca80", "#79b7ff", "#9bd7ff", "#f7eee6", "#c9b8ff")
_VOLUME_COLORS = ("#ff9750", "#ffc16b", "#f3d56b", "#79b7ff", "#5c9ded", "#8c7ae6", "#c9b8ff", "#667085")
_OTHER_VOLUME_COLOR = "#98A2B3"
MESSAGE_VOLUME_VISIBLE_USERS = 5
COMMAND_HELP_ITEMS = (
    {"command": "群聊词云 [YYYY-MM-DD]", "description": "超管查看昨天或指定日期的日报。"},
    {"command": "今日群聊词云", "description": "目标群成员可用；查看今天实时词云，同群共享 30 分钟冷却。"},
    {"command": "实时群聊词云", "description": "今日群聊词云的别名，目标群成员可用。"},
    {"command": "群聊词云 今天", "description": "查看今天实时词云，目标群成员可用并共享群级冷却。"},
    {"command": "重算群聊词云 YYYY-MM-DD", "description": "超管重算最近 7 天内的词云日报。"},
    {"command": "回填群聊词云 YYYY-MM-DD", "description": "超管按日期回填；当天快照会并入实时数据，零点自动刷新。"},
    {"command": "测试群聊词云", "description": "超管使用示例数据渲染测试图片，不写入数据库。"},
    {"command": "词云屏蔽词 查看", "description": "超管查看当前全局屏蔽词。"},
    {"command": "词云屏蔽词 添加 词1 词2", "description": "超管新增全局精确屏蔽词。"},
    {"command": "词云屏蔽词 取消 词1 词2", "description": "超管移除全局精确屏蔽词。"},
    {"command": "词云排除用户 查看", "description": "超管查看全局排除的 QQ 号。"},
    {"command": "词云排除用户 添加 QQ号1 QQ号2", "description": "超管新增全局消息排除对象。"},
    {"command": "词云排除用户 取消 QQ号1 QQ号2", "description": "超管移除全局消息排除对象。"},
    {"command": "Bot 指令自动过滤", "description": "所有已注册 Bot 指令、别名及带参数形式自动跳过，无需逐条添加。"},
    {"command": "词云帮助", "description": "显示本功能的全部指令。"},
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
        max_words=MAX_WORDS,
    )
    async with _RENDER_SEMAPHORE:
        return await html_to_pic(
            html,
            viewport={"width": 900, "height": 100},
            type="jpeg",
            quality=86,
        )

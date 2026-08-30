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

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "xml")),
)
_RENDER_SEMAPHORE = asyncio.Semaphore(2)

_WORD_COLORS = ("#ff9f57", "#ffca80", "#79b7ff", "#9bd7ff", "#f7eee6", "#c9b8ff")


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
        max_words=80,
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

    return {
        **report,
        "cloud_image": _wordcloud_data_uri(report),
        "top_words": top_words,
        "unique_word_count": len(report.get("frequencies", [])),
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

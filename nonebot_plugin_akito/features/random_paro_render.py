"""HTML render helpers for random_paro pages."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from nonebot_plugin_htmlrender import html_to_pic

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "random_paro"
_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "xml")),
)
_RENDER_SEM = asyncio.Semaphore(2)


async def render_random_paro_page(template_name: str, data: dict, *, viewport_width: int = 760) -> bytes:
    template = _TEMPLATE_ENV.get_template(template_name)
    html = template.render(**data)
    async with _RENDER_SEM:
        return await html_to_pic(
            html,
            viewport={"width": viewport_width, "height": 100},
            type="jpeg",
            quality=80,
        )

"""HTML render helpers for bond (羁绊) pages.

Mirrors random_paro_render.py: loads a Jinja2 template from templates/bond/,
renders it with the given data dict, and rasterizes via headless Chromium.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from nonebot_plugin_htmlrender import html_to_pic

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "bond"
_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "xml")),
)
_RENDER_SEM = asyncio.Semaphore(2)


async def render_bond_page(template_name: str, data: dict, *, viewport_width: int = 680) -> bytes:
    template = _TEMPLATE_ENV.get_template(template_name)
    html = template.render(**data)
    default_width = viewport_width
    try:
        viewport_width = int(data.get("page_width", default_width))
    except (TypeError, ValueError):
        viewport_width = default_width
    async with _RENDER_SEM:
        return await html_to_pic(
            html,
            viewport={"width": viewport_width, "height": 100},
            type="jpeg",
            quality=80,
        )

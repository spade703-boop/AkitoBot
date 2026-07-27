"""Compatibility wrapper for random_paro HTML rendering."""

from __future__ import annotations

from .random_paro.render import (
    _RENDER_SEM,
    _TEMPLATE_ENV,
    TEMPLATE_DIR,
)
from .random_paro.render import (
    html_to_pic as _html_to_pic,
)

__all__ = ("TEMPLATE_DIR", "html_to_pic", "render_random_paro_page")

html_to_pic = _html_to_pic


async def render_random_paro_page(template_name: str, data: dict, *, viewport_width: int = 760) -> bytes:
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

"""Shared helpers and assets for feature packages."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

FONT_PATH = Path(__file__).with_name("msyhbd.ttc")


def load_msyhbd_font(size: int):
    """Load the shared bold font with a PIL fallback."""
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except Exception:
        return ImageFont.load_default()

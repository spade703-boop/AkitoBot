"""Shared asset loading helpers for the random_paro feature."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...core import IMAGE_BASE_PATH
from .._shared import load_msyhbd_font

AVATAR_BASE = IMAGE_BASE_PATH / "paro_avatars"
FOXRABBIT_DIR = AVATAR_BASE / "fox&rabbit"

FONT_SIZE = 20
FONT_BOLD_SIZE = 24
ROW_H = 32
TEXT_TOP_GAP = 22
TEXT_BOTTOM_PAD = 10
AVATAR_WIDTH = 304
MIN_CANVAS_W = 380
AKITO_ACCENT = "#FF7722"
TOYA_ACCENT = "#0077DD"
SECTION_BAR_BG = "#8c9198"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_msyhbd_font(size)


def find_avatar(character: str, name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        path = AVATAR_BASE / character / f"{name}{ext}"
        if path.exists():
            return path
    return None


def load_foxrabbit_image(kind: str) -> Image.Image | None:
    for ext in (".png", ".jpg", ".jpeg"):
        path = FOXRABBIT_DIR / f"{kind}{ext}"
        if path.exists():
            return Image.open(path).convert("RGB").resize((150, 150), Image.LANCZOS)
    return None


def load_foxbun_image() -> Image.Image | None:
    for ext in (".png", ".jpg", ".jpeg"):
        path = FOXRABBIT_DIR / f"狐&兔{ext}"
        if path.exists():
            return Image.open(path).convert("RGB")
    return None


def path_to_uri(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.resolve().as_uri()
    except Exception:
        return ""


def find_foxrabbit_asset(name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = FOXRABBIT_DIR / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


def avatar_uri(character: str, name: str) -> str:
    return path_to_uri(find_avatar(character, name))


def fox_icon_uris(fox_type: str) -> list[str]:
    names = {
        "fox": ("狐",),
        "rabbit": ("兔",),
        "foxrabbit": ("狐", "兔"),
        "foxbun": ("狐&兔",),
    }.get(fox_type, ())
    return [path_to_uri(path) for name in names if (path := find_foxrabbit_asset(name))]


def resize_to_fit(image: Image.Image, *, max_w: int, max_h: int) -> Image.Image:
    width, height = image.size
    if width <= max_w and height <= max_h:
        return image.copy()
    ratio = min(max_w / width, max_h / height)
    size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    return image.resize(size, Image.LANCZOS)


def load_avatar_thumb(character: str, name: str, size: int = 56) -> Image.Image | None:
    path = find_avatar(character, name)
    if not path:
        return None
    return Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)


def load_fox_stat_icon(fox_type: str) -> Image.Image | None:
    if fox_type == "fox":
        image = load_foxrabbit_image("狐")
        return resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "rabbit":
        image = load_foxrabbit_image("兔")
        return resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "foxbun":
        image = load_foxbun_image()
        return resize_to_fit(image, max_w=96, max_h=56) if image else None
    if fox_type == "foxrabbit":
        fox = load_foxrabbit_image("狐")
        rabbit = load_foxrabbit_image("兔")
        if not fox or not rabbit:
            return None
        fox = resize_to_fit(fox, max_w=56, max_h=56)
        rabbit = resize_to_fit(rabbit, max_w=56, max_h=56)
        canvas = Image.new("RGB", (fox.width + rabbit.width + 6, max(fox.height, rabbit.height)), "#ffffff")
        canvas.paste(fox, (0, (canvas.height - fox.height) // 2))
        canvas.paste(rabbit, (fox.width + 6, (canvas.height - rabbit.height) // 2))
        return canvas
    return None


def build_placeholder_avatar(label: str, *, size: int, bg_color: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), color=bg_color)
    draw = ImageDraw.Draw(canvas)
    font = load_font(max(18, size // 2))
    draw.rectangle([(0, 0), (size - 1, size - 1)], outline="#dddddd", width=1)
    draw.text((size // 2, size // 2), label, font=font, fill="#ffffff", anchor="mm")
    return canvas


def save_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

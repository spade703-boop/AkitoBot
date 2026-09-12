"""Shared asset loading helpers for the random_paro feature."""

from __future__ import annotations

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


def load_special_images(special_type: str) -> list[Image.Image]:
    from .store import _special_outcome

    outcome = _special_outcome(special_type)
    if not outcome:
        return []
    images = []
    for asset_name in outcome.get("assets", []):
        image = load_foxbun_image() if asset_name == "狐&兔" else load_foxrabbit_image(asset_name)
        if image:
            images.append(image)
    return images


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
    from .store import _special_outcome

    outcome = _special_outcome(fox_type)
    names = outcome.get("assets", []) if outcome else []
    return [path_to_uri(path) for name in names if (path := find_foxrabbit_asset(name))]


def special_icon_uris(special_type: str | None) -> list[str]:
    return fox_icon_uris(special_type or "")


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
    from .store import _special_outcome

    outcome = _special_outcome(fox_type)
    if not outcome:
        return None
    images = [resize_to_fit(image, max_w=56, max_h=56) for image in load_special_images(fox_type)]
    if not images:
        return None
    if len(images) == 1:
        return resize_to_fit(images[0], max_w=96 if fox_type == "foxbun" else 56, max_h=56)
    canvas = Image.new(
        "RGB",
        (sum(image.width for image in images) + 6 * (len(images) - 1), max(image.height for image in images)),
        "#ffffff",
    )
    x = 0
    for image in images:
        canvas.paste(image, (x, (canvas.height - image.height) // 2))
        x += image.width + 6
    return canvas


def load_special_stat_icon(special_type: str) -> Image.Image | None:
    return load_fox_stat_icon(special_type)


def build_placeholder_avatar(label: str, *, size: int, bg_color: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), color=bg_color)
    draw = ImageDraw.Draw(canvas)
    font = load_font(max(18, size // 2))
    draw.rectangle([(0, 0), (size - 1, size - 1)], outline="#dddddd", width=1)
    draw.text((size // 2, size // 2), label, font=font, fill="#ffffff", anchor="mm")
    return canvas

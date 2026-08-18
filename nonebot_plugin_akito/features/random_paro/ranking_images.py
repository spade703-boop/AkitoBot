"""Original PIL fallbacks for random_paro ranking and profile pages."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .assets import (
    AKITO_ACCENT,
    FONT_SIZE,
    SECTION_BAR_BG,
    TOYA_ACCENT,
)
from .assets import (
    find_avatar as _find_avatar,
)
from .assets import (
    load_font as _load_font,
)
from .assets import (
    load_foxbun_image as _load_foxbun_image,
)
from .assets import (
    load_foxrabbit_image as _load_foxrabbit_image,
)
from .stats import (
    _build_character_rows,
    _build_fox_rows,
    _build_personal_cooking_pair_items,
    _build_user_rows,
    _collect_user_egg_history,
    _count_total_cooking_hits,
    _new_user_egg_history,
    _sorted_ranked_items,
    _split_pair_key,
)
from .store import _normalize_user_stats
from .views import _get_group_period_stats, _get_group_stats, _subtitle_for_scope


def _resize_to_fit(image: Image.Image, *, max_w: int, max_h: int) -> Image.Image:
    width, height = image.size
    if width <= max_w and height <= max_h:
        return image.copy()
    ratio = min(max_w / width, max_h / height)
    size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    return image.resize(size, Image.LANCZOS)


def _load_avatar_thumb(character: str, name: str, size: int = 56) -> Image.Image | None:
    path = _find_avatar(character, name)
    if not path:
        return None
    image = Image.open(path).convert("RGB")
    return image.resize((size, size), Image.LANCZOS)


def _load_fox_stat_icon(fox_type: str) -> Image.Image | None:
    if fox_type == "fox":
        image = _load_foxrabbit_image("狐")
        return _resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "rabbit":
        image = _load_foxrabbit_image("兔")
        return _resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "foxbun":
        image = _load_foxbun_image()
        return _resize_to_fit(image, max_w=96, max_h=56) if image else None
    if fox_type == "foxrabbit":
        fox = _load_foxrabbit_image("狐")
        rabbit = _load_foxrabbit_image("兔")
        if not fox or not rabbit:
            return None
        fox = _resize_to_fit(fox, max_w=56, max_h=56)
        rabbit = _resize_to_fit(rabbit, max_w=56, max_h=56)
        canvas = Image.new("RGB", (fox.width + rabbit.width + 6, max(fox.height, rabbit.height)), "#ffffff")
        canvas.paste(fox, (0, (canvas.height - fox.height) // 2))
        canvas.paste(rabbit, (fox.width + 6, (canvas.height - rabbit.height) // 2))
        return canvas
    return None


def _text_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, fallback_size: int = FONT_SIZE) -> int:
    try:
        bbox = font.getbbox(text)
        width = bbox[2]
        if isinstance(width, (int, float)):
            return int(width)
    except Exception:
        pass
    return max(len(text), 1) * fallback_size


def _text_height(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str = "Hg", fallback_size: int = FONT_SIZE
) -> int:
    try:
        bbox = font.getbbox(text)
        height = bbox[3] - bbox[1]
        if isinstance(height, (int, float)) and height > 0:
            return int(height)
    except Exception:
        pass
    return fallback_size


def _truncate_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _text_width(font, text) <= max_width:
        return text

    suffix = "..."
    trimmed = text
    while trimmed and _text_width(font, trimmed + suffix) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + suffix) if trimmed else suffix


def _draw_section_label(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str = "#333333",
    bg_fill: str | None = None,
    height: int = 34,
) -> int:
    if bg_fill:
        draw.rectangle([(left, y), (right, y + height)], fill=bg_fill)
        draw.text((left + 12, y + height // 2), text, font=font, fill=fill, anchor="lm")
        return height

    draw.text((left, y + height // 2), text, font=font, fill=fill, anchor="lm")
    return height


def _resolve_row_icon(row: dict) -> Image.Image | None:
    if row.get("icon_kind") == "fox":
        fox_type = row.get("fox_type")
        if isinstance(fox_type, str):
            return _load_fox_stat_icon(fox_type)
    return None


def _resolve_row_suffix_icons(row: dict) -> list[Image.Image]:
    names = row.get("suffix_avatar_names")
    character = row.get("suffix_character")
    if not isinstance(names, list) or not character:
        return []

    icons = []
    for name in names:
        if not isinstance(name, str):
            continue
        icon = _load_avatar_thumb(character, name, size=40)
        if icon:
            icons.append(icon)
    return icons


def _prepare_display_rows(
    rows: list[dict], *, min_row_height: int = 44
) -> list[tuple[dict, Image.Image | None, list[Image.Image], int]]:
    prepared_rows = []
    for row in rows:
        prefix_icon = _resolve_row_icon(row)
        suffix_icons = _resolve_row_suffix_icons(row)
        icon_heights = []
        if prefix_icon:
            icon_heights.append(prefix_icon.height)
        if suffix_icons:
            icon_heights.extend(icon.height for icon in suffix_icons)
        row_height = max(min_row_height, max(icon_heights, default=0) + 12)
        prepared_rows.append((row, prefix_icon, suffix_icons, row_height))
    return prepared_rows


def _build_placeholder_avatar(label: str, *, size: int, bg_color: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), color=bg_color)
    draw = ImageDraw.Draw(canvas)
    font = _load_font(max(18, size // 2))
    draw.rectangle([(0, 0), (size - 1, size - 1)], outline="#dddddd", width=1)
    draw.text((size // 2, size // 2), label, font=font, fill="#ffffff", anchor="mm")
    return canvas


def _build_pair_thumb(akito_name: str, toya_name: str, *, size: int = 52, gap: int = 4) -> Image.Image:
    akito_thumb = _load_avatar_thumb("彰人", akito_name, size=size) or _build_placeholder_avatar(
        "彰", size=size, bg_color="#f08a5d"
    )
    toya_thumb = _load_avatar_thumb("冬弥", toya_name, size=size) or _build_placeholder_avatar(
        "冬", size=size, bg_color="#5d8df0"
    )
    canvas = Image.new("RGB", (size * 2 + gap, size), color="#ffffff")
    canvas.paste(akito_thumb, (0, 0))
    canvas.paste(toya_thumb, (size + gap, 0))
    return canvas


def _build_personal_pair_items(user_stats: dict) -> list[dict]:
    items = []
    for pair_key, count in _sorted_ranked_items(user_stats["pair_hits"], user_stats.get("pair_last_hit_seq")):
        akito_name, toya_name = _split_pair_key(pair_key)
        items.append(
            {
                "pair_key": pair_key,
                "akito_name": akito_name,
                "toya_name": toya_name,
                "count": count,
            }
        )
    return items


def _render_leaderboard_card(title: str, subtitle: str, sections: list[dict]) -> bytes:
    width = 760
    pad_x = 34
    pad_y = 26
    row_gap = 8
    section_gap = 12

    font_title = _load_font(30)
    font_subtitle = _load_font(18)
    font_section = _load_font(22)
    font_row = _load_font(20)
    font_value = _load_font(20)

    prepared_sections = []
    height = pad_y + 38 + 28
    for section in sections:
        rows = []
        title_gap_after = int(section.get("title_gap_after", 0) or 0)
        height += 34 + title_gap_after
        for row in section["rows"]:
            prefix_icon = _resolve_row_icon(row)
            suffix_icons = _resolve_row_suffix_icons(row)
            icon_heights = []
            if prefix_icon:
                icon_heights.append(prefix_icon.height)
            if suffix_icons:
                icon_heights.extend(icon.height for icon in suffix_icons)
            row_height = max(44, max(icon_heights, default=0) + 12)
            rows.append((row, prefix_icon, suffix_icons, row_height))
            height += row_height + row_gap
        height += section_gap
        prepared_sections.append(
            {
                "title": section["title"],
                "title_fill": section.get("title_fill", "#333333"),
                "title_bg": section.get("title_bg"),
                "title_gap_after": title_gap_after,
                "rows": rows,
            }
        )

    height += pad_y - section_gap
    canvas = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    y = pad_y
    draw.text((width // 2, y), title, font=font_title, fill="#000000", anchor="ma")
    y += 38
    draw.text((width // 2, y), subtitle, font=font_subtitle, fill="#888888", anchor="ma")
    y += 28

    for section in prepared_sections:
        y += _draw_section_label(
            draw,
            left=pad_x,
            right=width - pad_x,
            y=y,
            text=section["title"],
            font=font_section,
            fill=section["title_fill"],
            bg_fill=section["title_bg"],
        )
        y += section.get("title_gap_after", 0)
        rows = section["rows"]
        for row, prefix_icon, suffix_icons, row_height in rows:
            text_x = pad_x
            if prefix_icon:
                icon_y = y + (row_height - prefix_icon.height) // 2
                canvas.paste(prefix_icon, (pad_x, icon_y))
                text_x += prefix_icon.width + 14

            value_text = row.get("right", "")
            value_width = _text_width(font_value, value_text)
            suffix_width = 0
            if suffix_icons:
                suffix_width = sum(icon.width for icon in suffix_icons) + 8 * len(suffix_icons)
            available_width = width - pad_x - value_width - 18 - text_x - suffix_width
            left_text = _truncate_text(row.get("left", ""), font_row, available_width)
            row_center_y = y + row_height // 2
            draw.text((text_x, row_center_y), left_text, font=font_row, fill="#000000", anchor="lm")

            left_text_width = _text_width(font_row, left_text)
            suffix_x = text_x + left_text_width + 8
            for icon in suffix_icons:
                icon_y = y + (row_height - icon.height) // 2
                canvas.paste(icon, (suffix_x, icon_y))
                suffix_x += icon.width + 8

            draw.text((width - pad_x, row_center_y), value_text, font=font_value, fill="#555555", anchor="rm")
            y += row_height + row_gap
        y += section_gap

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _render_personal_paro_card(user_id: str, display_name: str, user_stats: dict, egg_history: dict) -> bytes:
    width = 760
    pad_x = 30
    pad_y = 26
    row_gap = 6
    section_gap = 10
    pair_tile_w = 132
    pair_tile_h = 86
    pair_gap = 10
    pair_columns = 4
    pair_section_top_gap = 8
    fox_section_gap = 24

    font_section = _load_font(21)
    font_row = _load_font(19)
    font_value = _load_font(19)
    font_pair = _load_font(17)
    font_name = _load_font(38)
    name_text = display_name or f"用户{user_id}"
    name_text = _truncate_text(name_text, font_name, width - pad_x * 2)
    name_height = max(42, _text_height(font_name, name_text, fallback_size=38))

    summary_rows = [
        {"left": "累计抽取派生次数", "right": f"{user_stats['draw_count']}次"},
        {"left": "累计抽到做饭的次数", "right": f"{_count_total_cooking_hits(egg_history)}次"},
    ]
    prepared_sections = [
        {
            "title": "累计记录",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "rows": _prepare_display_rows(summary_rows, min_row_height=42),
        },
        {
            "title": "抽到最多的彰人派生 TOP 3",
            "title_fill": AKITO_ACCENT,
            "rows": _prepare_display_rows(
                _build_character_rows(
                    user_stats["akito_hits"],
                    limit=3,
                    character="彰人",
                    last_hit_seq=user_stats.get("akito_last_hit_seq"),
                )
            ),
        },
        {
            "title": "抽到最多的冬弥派生 TOP 3",
            "title_fill": TOYA_ACCENT,
            "rows": _prepare_display_rows(
                _build_character_rows(
                    user_stats["toya_hits"],
                    limit=3,
                    character="冬弥",
                    last_hit_seq=user_stats.get("toya_last_hit_seq"),
                )
            ),
        },
    ]

    pair_items = _build_personal_cooking_pair_items(egg_history)
    content_width = width - pad_x * 2
    pair_row_count = (len(pair_items) + pair_columns - 1) // pair_columns if pair_items else 0
    pair_grid_width = pair_columns * pair_tile_w + max(0, pair_columns - 1) * pair_gap
    pair_grid_x = pad_x + max(0, (content_width - pair_grid_width) // 2)

    foxbun_icon = _load_fox_stat_icon("foxbun")
    foxbun_text = f"狐兔饭：累计触发 {egg_history['foxbun_count']} 次。"
    fox_line_height = max(56, (foxbun_icon.height if foxbun_icon else 0) + 8)

    height = pad_y + name_height + 18
    for section in prepared_sections:
        height += 34
        for _row, _prefix_icon, _suffix_icons, row_height in section["rows"]:
            height += row_height + row_gap
        height += section_gap

    height += 34
    if pair_row_count:
        height += pair_section_top_gap
        height += pair_row_count * pair_tile_h + max(0, pair_row_count - 1) * pair_gap
    else:
        height += 44
    height += section_gap

    height += fox_section_gap + fox_line_height
    height += pad_y

    canvas = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    y = pad_y
    draw.text((pad_x, y), name_text, font=font_name, fill="#111111", anchor="la")
    y += name_height + 18

    for section in prepared_sections:
        y += _draw_section_label(
            draw,
            left=pad_x,
            right=width - pad_x,
            y=y,
            text=section["title"],
            font=font_section,
            fill=section.get("title_fill", "#333333"),
            bg_fill=section.get("title_bg"),
        )
        rows = section["rows"]
        for row, prefix_icon, suffix_icons, row_height in rows:
            text_x = pad_x
            if prefix_icon:
                icon_y = y + (row_height - prefix_icon.height) // 2
                canvas.paste(prefix_icon, (pad_x, icon_y))
                text_x += prefix_icon.width + 14

            value_text = row.get("right", "")
            value_width = _text_width(font_value, value_text)
            suffix_width = 0
            if suffix_icons:
                suffix_width = sum(icon.width for icon in suffix_icons) + 8 * len(suffix_icons)
            available_width = width - pad_x - value_width - 18 - text_x - suffix_width
            left_text = _truncate_text(row.get("left", ""), font_row, available_width)
            row_center_y = y + row_height // 2
            draw.text((text_x, row_center_y), left_text, font=font_row, fill="#000000", anchor="lm")

            left_text_width = _text_width(font_row, left_text)
            suffix_x = text_x + left_text_width + 8
            for icon in suffix_icons:
                icon_y = y + (row_height - icon.height) // 2
                canvas.paste(icon, (suffix_x, icon_y))
                suffix_x += icon.width + 8

            draw.text((width - pad_x, row_center_y), value_text, font=font_value, fill="#555555", anchor="rm")
            y += row_height + row_gap
        y += section_gap

    y += _draw_section_label(
        draw,
        left=pad_x,
        right=width - pad_x,
        y=y,
        text="你还没有做的派生饭……",
        font=font_section,
        fill="#ffffff",
        bg_fill=SECTION_BAR_BG,
    )
    if pair_items:
        y += pair_section_top_gap
        for index, item in enumerate(pair_items):
            row_index = index // pair_columns
            col_index = index % pair_columns
            tile_x = pair_grid_x + col_index * (pair_tile_w + pair_gap)
            tile_y = y + row_index * (pair_tile_h + pair_gap)
            draw.rectangle(
                [(tile_x, tile_y), (tile_x + pair_tile_w, tile_y + pair_tile_h)],
                fill="#fafafa",
                outline="#dddddd",
                width=1,
            )
            thumb = _build_pair_thumb(item["akito_name"], item["toya_name"], size=54)
            thumb_x = tile_x + (pair_tile_w - thumb.width) // 2
            canvas.paste(thumb, (thumb_x, tile_y + 9))
            draw.text(
                (tile_x + 10, tile_y + pair_tile_h - 12),
                f"x{item['count']}",
                font=font_pair,
                fill="#666666",
                anchor="ld",
            )
        y += pair_row_count * pair_tile_h + max(0, pair_row_count - 1) * pair_gap
    else:
        draw.text((pad_x, y + 20), "还没有抽到做饭彩蛋", font=font_row, fill="#888888", anchor="la")
        y += 44
    y += fox_section_gap

    line_y = y + fox_line_height // 2
    text_x = pad_x
    if foxbun_icon:
        icon_y = y + (fox_line_height - foxbun_icon.height) // 2
        canvas.paste(foxbun_icon, (pad_x, icon_y))
        text_x += foxbun_icon.width + 14
    draw.text((text_x, line_y), foxbun_text, font=font_section, fill="#333333", anchor="lm")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _build_paro_rank_pil_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    subtitle = _subtitle_for_scope(period_stats, scope)
    sections = [
        {
            "title": "本群累计抽取总次数",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": [{"left": "总计", "right": f"{period_stats['total_draws']}次"}],
        },
        {
            "title": "抽取次数最多的前 5 人",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_user_rows(period_stats["user_draw_counts"], group_stats["profiles"], limit=5),
        },
        {
            "title": "被抽到最多次的彰人 TOP 3",
            "title_fill": AKITO_ACCENT,
            "rows": _build_character_rows(
                period_stats["akito_hits"],
                limit=3,
                character="彰人",
                last_hit_seq=period_stats.get("akito_last_hit_seq"),
            ),
        },
        {
            "title": "被抽到最多次的冬弥 TOP 3",
            "title_fill": TOYA_ACCENT,
            "rows": _build_character_rows(
                period_stats["toya_hits"],
                limit=3,
                character="冬弥",
                last_hit_seq=period_stats.get("toya_last_hit_seq"),
            ),
        },
    ]
    title = "每日派生排行榜" if scope == "daily" else "历史派生排行榜"
    return _render_leaderboard_card(title, subtitle, sections)


def _build_egg_rank_pil_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    subtitle = _subtitle_for_scope(period_stats, scope)
    sections = [
        {
            "title": "做饭 + 狐兔饭触发最多的前 5 人",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_user_rows(period_stats["egg_user_counts"], group_stats["profiles"], limit=5),
        },
        {
            "title": "狐兔彩蛋触发次数",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_fox_rows(period_stats),
        },
    ]
    title = "每日做饭排行榜" if scope == "daily" else "历史做饭排行榜"
    return _render_leaderboard_card(title, subtitle, sections)


def _build_personal_paro_pil_image_from_user_stats(
    user_id: str,
    display_name: str,
    user_stats: dict,
    egg_history: dict | None = None,
) -> bytes:
    return _render_personal_paro_card(
        user_id,
        display_name,
        _normalize_user_stats(user_stats),
        egg_history or _new_user_egg_history(),
    )


build_paro_rank_pil_image_from_stats = _build_paro_rank_pil_image_from_stats
build_egg_rank_pil_image_from_stats = _build_egg_rank_pil_image_from_stats
build_personal_paro_pil_image_from_user_stats = _build_personal_paro_pil_image_from_user_stats


def build_paro_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return _build_paro_rank_pil_image_from_stats(group_stats, period_stats, scope)


def build_egg_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return _build_egg_rank_pil_image_from_stats(group_stats, period_stats, scope)


def build_personal_paro_image(group_id: int, user_id: str, display_name: str) -> bytes:
    group_stats = _get_group_stats(group_id)
    user_stats = _normalize_user_stats(group_stats.get("users", {}).get(user_id))
    egg_history = _collect_user_egg_history(group_id, user_id)
    return _build_personal_paro_pil_image_from_user_stats(user_id, display_name, user_stats, egg_history)


__all__ = [
    "build_paro_rank_pil_image_from_stats",
    "build_egg_rank_pil_image_from_stats",
    "build_personal_paro_pil_image_from_user_stats",
    "build_paro_rank_image",
    "build_egg_rank_image",
    "build_personal_paro_image",
]

"""Random paro feature package.

Importing this package registers the production, admin, and superuser preview
commands.  Compatibility exports remain available for hot reload and tests,
while implementations live in focused submodules.
"""

from __future__ import annotations

from . import commands as commands
from . import preview_commands as preview_commands
from .assets import (
    AKITO_ACCENT,
    AVATAR_BASE,
    FOXRABBIT_DIR,
    MIN_CANVAS_W,
    SECTION_BAR_BG,
    TOYA_ACCENT,
    avatar_uri as _avatar_uri,
    build_placeholder_avatar as _build_placeholder_avatar,
    find_foxrabbit_asset as _find_foxrabbit_asset,
    fox_icon_uris as _fox_icon_uris,
    load_avatar_thumb as _load_avatar_thumb,
    load_fox_stat_icon as _load_fox_stat_icon,
    load_foxbun_image as _load_foxbun_image,
    load_foxrabbit_image as _load_foxrabbit_image,
    load_font as _load_font,
    path_to_uri as _path_to_uri,
    resize_to_fit as _resize_to_fit,
)
from .commands import (
    PARO_USE_HTML_RENDER,
    _DRAW_LOCKS,
    _PARO_HELP_TEXT,
    add_akito_cmd,
    add_toya_cmd,
    daily_egg_rank_cmd,
    daily_rank_cmd,
    del_akito_cmd,
    del_toya_cmd,
    draw_cmd,
    event_display_name as _event_display_name,
    help_cmd,
    history_egg_rank_cmd,
    history_rank_cmd,
    my_paro_cmd,
    render_draw_result_image as _render_draw_result_image,
    render_draw_result_preview_image as _render_draw_result_preview_image,
    resolve_group_command as _resolve_group_command,
    view_akito_cmd,
    view_toya_cmd,
)
from .draw import (
    DRAW_LIMIT as _DRAW_LIMIT,
    DRAW_WINDOW as _DRAW_WINDOW,
    EASTER_EGG_RATE as _EASTER_EGG_RATE,
    FOXRABBIT_RATE as _FOXRABBIT_RATE,
    build_draw_limit_message as _build_draw_limit_message,
    draw_results as _draw_results,
    fuzzy_match as _fuzzy_match,
    get_fixed_side as _get_fixed_side,
    parse_draw_request as _parse_draw_request,
    prune_draw_history as _prune_draw_history,
    resolve_directional_draw as _resolve_directional_draw,
)
from .draw_images import (
    SEQS,
    _canvas_width,
    _draw_segmented_line,
    _measure_line_width,
    render_composite as _render_composite,
    render_multi as _render_multi,
    render_pool_image as _render_pool_image,
    render_text_only as _render_text_only,
)
from .preview import (
    build_egg_rank_preview_image as _build_egg_rank_preview_image,
    build_personal_preview_image as _build_personal_preview_image,
    build_rank_preview_image as _build_rank_preview_image,
    build_rank_preview_stats as _build_rank_preview_stats,
    render_egg_rank_preview_image as _render_egg_rank_preview_image,
    render_personal_preview_image as _render_personal_preview_image,
    render_rank_preview_image as _render_rank_preview_image,
)
from .preview_commands import (
    test_daily_egg_rank_cmd,
    test_daily_rank_cmd,
    test_egg_cmd,
    test_foxbun_cmd,
    test_fr_cmd,
    test_history_egg_rank_cmd,
    test_history_rank_cmd,
    test_multi_cmd,
    test_my_paro_cmd,
)
from .ranking_images import (
    _build_pair_thumb,
    _build_personal_pair_items,
    _draw_section_label,
    _prepare_display_rows,
    _render_leaderboard_card,
    _render_personal_paro_card,
    _resolve_row_icon,
    _text_height,
    _text_width,
    _truncate_text,
    build_egg_rank_image as _build_egg_rank_image,
    build_egg_rank_pil_image_from_stats as _build_egg_rank_image_from_stats,
    build_egg_rank_pil_image_from_stats as _build_egg_rank_pil_image_from_stats,
    build_paro_rank_image as _build_paro_rank_image,
    build_paro_rank_pil_image_from_stats as _build_paro_rank_image_from_stats,
    build_paro_rank_pil_image_from_stats as _build_paro_rank_pil_image_from_stats,
    build_personal_paro_image as _build_personal_paro_image,
    build_personal_paro_pil_image_from_user_stats as _build_personal_paro_image_from_user_stats,
    build_personal_paro_pil_image_from_user_stats as _build_personal_paro_pil_image_from_user_stats,
)
from .render import TEMPLATE_DIR, _TEMPLATE_ENV, html_to_pic, render_random_paro_page
from .stats import (
    _build_character_rows,
    _build_fox_rows,
    _build_personal_cooking_pair_items,
    _build_user_rows,
    _collect_user_egg_history,
    _cooldown_store,
    _count_total_cooking_hits,
    _get_or_create_group_stats,
    _make_pair_key,
    _new_user_egg_history,
    _record_draw_stats_for_period,
    _record_group_draw_stats,
    _record_user_draw_stats,
    _record_user_egg_history_entry,
    _roll_daily_stats,
    _sorted_counter_items,
    _sorted_ranked_items,
    _split_pair_key,
    _today_str,
)
from .store import (
    DATA_FILE,
    DEFAULT_DATA,
    EGG_LOG_FILE,
    PARO_DATA,
    PARO_STATS,
    STATS_FILE,
    _append_egg_log,
    _egg_log_path,
    _load_stats,
    _new_group_stats,
    _new_period_stats,
    _new_stats_state,
    _new_user_stats,
    _normalize_group_stats,
    _normalize_period_stats,
    _normalize_user_stats,
    _save,
    _save_stats,
    _stats_path,
    reload_paro_data,
)
from .views import (
    _build_character_contract,
    _build_draw_result_page_data,
    _build_egg_rank_page_data_from_stats,
    _build_fox_rows_contract,
    _build_paro_rank_page_data_from_stats,
    _build_personal_paro_page_data_from_user_stats,
    _build_profile_pair_contract,
    _build_user_contract,
    _get_group_period_stats,
    _get_group_stats,
    _render_html_page,
    _subtitle_for_scope,
    render_egg_rank_image as _render_egg_rank_image,
    render_egg_rank_image_from_stats as _render_egg_rank_image_from_stats,
    render_paro_rank_image as _render_paro_rank_image,
    render_paro_rank_image_from_stats as _render_paro_rank_image_from_stats,
    render_personal_paro_image as _render_personal_paro_image,
    render_personal_paro_image_from_user_stats as _render_personal_paro_image_from_user_stats,
)


def _find_avatar(character: str, name: str):
    for extension in (".png", ".jpg", ".jpeg"):
        path = AVATAR_BASE / character / f"{name}{extension}"
        if path.exists():
            return path
    return None


def _resolve_row_suffix_icons(row: dict) -> list:
    from PIL import Image

    names = row.get("suffix_avatar_names")
    character = row.get("suffix_character")
    if not isinstance(names, list) or not character:
        return []
    icons = []
    for name in names:
        if not isinstance(name, str):
            continue
        path = _find_avatar(character, name)
        if path:
            icons.append(Image.open(path).convert("RGB").resize((40, 40)))
    return icons


__all__ = [name for name in globals() if not name.startswith("__")]

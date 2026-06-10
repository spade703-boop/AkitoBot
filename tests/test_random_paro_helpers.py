"""测试 random_paro.py 中抽出的参数与限频辅助函数。"""

from __future__ import annotations

import copy
import json

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.random_paro as random_paro


def test_parse_draw_request_extracts_count_and_directional_part():
    count, directional = random_paro._parse_draw_request("3 彰人 黑百合")

    assert count == 3
    assert directional == "彰人 黑百合"


def test_parse_draw_request_defaults_to_single_draw():
    count, directional = random_paro._parse_draw_request("冬弥 王子冬")

    assert count == 1
    assert directional == "冬弥 王子冬"


def test_resolve_directional_draw_handles_success_and_ambiguity():
    fixed_a, fixed_b, error = random_paro._resolve_directional_draw(
        "彰人 黑百",
        ["黑百合", "白骑"],
        ["王子冬"],
    )
    _fa2, _fb2, error2 = random_paro._resolve_directional_draw(
        "冬弥 王",
        ["黑百合"],
        ["王子冬", "王者冬"],
    )

    assert fixed_a == "黑百合"
    assert fixed_b is None
    assert error is None
    assert "匹配到多个条目" in error2


def test_resolve_directional_draw_rejects_unknown_prefix():
    _fixed_a, _fixed_b, error = random_paro._resolve_directional_draw(
        "别的角色 测试",
        ["黑百合"],
        ["王子冬"],
    )

    assert "请指定要固定哪一方" in error


def test_prune_draw_history_and_limit_message():
    history = random_paro._prune_draw_history([0.0, 100.0, 1700.0], now_ts=1800.0, window=1800)
    exhausted = random_paro._build_draw_limit_message(
        remaining_before=0,
        requested_count=1,
        history=[100.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )
    partial = random_paro._build_draw_limit_message(
        remaining_before=1,
        requested_count=3,
        history=[100.0, 200.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )

    assert history == [100.0, 1700.0]
    assert "你已用完次数" in exhausted
    assert "仅剩 1 次" in partial


@pytest.fixture()
def isolated_paro_stats():
    stats_snapshot = copy.deepcopy(random_paro.PARO_STATS)
    stats_path = random_paro._stats_path()
    egg_log_path = random_paro._egg_log_path()
    stats_text = stats_path.read_text(encoding="utf-8") if stats_path.exists() else None
    egg_log_text = egg_log_path.read_text(encoding="utf-8") if egg_log_path.exists() else None

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(random_paro._new_stats_state())
    if stats_path.exists():
        stats_path.unlink()
    if egg_log_path.exists():
        egg_log_path.unlink()

    yield

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(stats_snapshot)
    if stats_text is None:
        if stats_path.exists():
            stats_path.unlink()
    else:
        stats_path.write_text(stats_text, encoding="utf-8")
    if egg_log_text is None:
        if egg_log_path.exists():
            egg_log_path.unlink()
    else:
        egg_log_path.write_text(egg_log_text, encoding="utf-8")


def test_roll_daily_stats_resets_only_daily_bucket():
    group_stats = {
        "profiles": {"42": "测试用户"},
        "daily": random_paro._new_period_stats(date="2026-06-10"),
        "history": random_paro._new_period_stats(),
    }
    group_stats["daily"]["total_draws"] = 5
    group_stats["history"]["total_draws"] = 9

    rolled = random_paro._roll_daily_stats(group_stats, "2026-06-11")

    assert rolled is True
    assert group_stats["profiles"] == {"42": "测试用户"}
    assert group_stats["daily"]["date"] == "2026-06-11"
    assert group_stats["daily"]["total_draws"] == 0
    assert group_stats["history"]["total_draws"] == 9


def test_record_draw_stats_respects_fixed_side_and_hidden_pairs():
    period_stats = random_paro._new_period_stats()
    results = [
        ("黑百合", "王子冬", False, None),
        ("白骑", "黑骑", True, None),
        ("Callboy彰", "Callboy冬", False, "fox"),
        ("神代类", "天马司", False, "foxbun"),
        ("东云彰人", "青柳冬弥", False, "foxrabbit"),
        ("白石杏", "小豆泽心羽", False, "rabbit"),
    ]

    random_paro._record_draw_stats_for_period(
        period_stats,
        user_id="42",
        results=results,
        fixed_side="akito",
    )

    assert period_stats["total_draws"] == 6
    assert period_stats["user_draw_counts"] == {"42": 6}
    assert period_stats["akito_hits"] == {}
    assert period_stats["toya_hits"] == {"王子冬": 1, "黑骑": 1}
    assert period_stats["egg_user_counts"] == {"42": 2}
    assert period_stats["fox_total"] == 1
    assert period_stats["rabbit_total"] == 1
    assert period_stats["foxrabbit_total"] == 1
    assert period_stats["foxbun_total"] == 1
    assert period_stats["toya_last_hit_seq"] == {"王子冬": 1, "黑骑": 2}


def test_build_character_rows_keeps_first_three_tied_names_and_ellipsis():
    rows = random_paro._build_character_rows(
        {
            "白骑": 8,
            "王子彰": 8,
            "WL2彰": 8,
            "白恶魔": 8,
            "Callboy彰": 9,
        },
        limit=3,
        character="彰人",
        last_hit_seq={
            "Callboy彰": 9,
            "白骑": 12,
            "王子彰": 15,
            "WL2彰": 20,
            "白恶魔": 23,
        },
    )

    assert rows[0]["left"] == "TOP1 Callboy彰"
    assert rows[0]["suffix_avatar_names"] == ["Callboy彰"]
    assert rows[1]["left"] == "TOP2 白骑 / 王子彰 / WL2彰 / ..."
    assert rows[1]["suffix_avatar_names"] == ["白骑", "王子彰", "WL2彰"]


def test_build_fox_rows_sorts_by_count_desc():
    rows = random_paro._build_fox_rows(
        {
            "foxrabbit_total": 3,
            "foxbun_total": 7,
            "fox_total": 2,
            "rabbit_total": 9,
        }
    )

    assert [row["fox_type"] for row in rows] == ["rabbit", "foxbun", "foxrabbit", "fox"]


def test_record_group_draw_stats_persists_counts_and_egg_log(isolated_paro_stats):
    results = [
        ("黑百合", "王子冬", True, None),
        ("白骑", "黑骑", False, "foxbun"),
        ("Callboy彰", "Callboy冬", False, "fox"),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=results,
        fixed_side=None,
        fixed_name=None,
        requested_count=3,
        now_ts=123.0,
    )

    group_stats = random_paro.PARO_STATS["groups"]["1001"]
    daily = group_stats["daily"]
    history = group_stats["history"]
    egg_lines = random_paro._egg_log_path().read_text(encoding="utf-8").strip().splitlines()
    egg_entries = [json.loads(line) for line in egg_lines]

    assert group_stats["profiles"] == {"42": "测试用户"}
    assert daily["total_draws"] == 3
    assert history["total_draws"] == 3
    assert daily["egg_user_counts"] == {"42": 2}
    assert daily["foxbun_total"] == 1
    assert daily["fox_total"] == 1
    assert len(egg_entries) == 2
    assert {entry["egg_type"] for entry in egg_entries} == {"cooking", "foxbun"}
    assert all(entry["user_id"] == "42" for entry in egg_entries)


def test_record_group_draw_stats_tracks_personal_visible_history(isolated_paro_stats):
    results = [
        ("黑百合", "王子冬", False, None),
        ("白骑士", "黑骑士", True, None),
        ("Callboy彰", "Callboy冬", False, "fox"),
        ("神代类", "天马司", False, "foxbun"),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=results,
        fixed_side=None,
        fixed_name=None,
        requested_count=4,
        now_ts=123.0,
    )

    user_stats = random_paro.PARO_STATS["groups"]["1001"]["users"]["42"]

    assert user_stats["draw_count"] == 4
    assert user_stats["egg_count"] == 2
    assert user_stats["foxbun_count"] == 1
    assert user_stats["akito_hits"] == {"黑百合": 1, "白骑士": 1}
    assert user_stats["toya_hits"] == {"王子冬": 1, "黑骑士": 1}
    assert user_stats["pair_hits"] == {
        random_paro._make_pair_key("黑百合", "王子冬"): 1,
        random_paro._make_pair_key("白骑士", "黑骑士"): 1,
    }


def test_record_group_draw_stats_keeps_personal_visible_history_for_fixed_side(isolated_paro_stats):
    results = [
        ("白骑士", "王子冬", False, None),
        ("白骑士", "黑骑士", False, None),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=results,
        fixed_side="akito",
        fixed_name="白骑士",
        requested_count=2,
        now_ts=123.0,
    )

    group_stats = random_paro.PARO_STATS["groups"]["1001"]
    user_stats = group_stats["users"]["42"]

    assert group_stats["history"]["akito_hits"] == {}
    assert user_stats["akito_hits"] == {"白骑士": 2}
    assert user_stats["toya_hits"] == {"王子冬": 1, "黑骑士": 1}
    assert user_stats["pair_hits"] == {
        random_paro._make_pair_key("白骑士", "王子冬"): 1,
        random_paro._make_pair_key("白骑士", "黑骑士"): 1,
    }


def test_save_and_reload_stats_preserves_cooldowns(isolated_paro_stats):
    random_paro._cooldown_store()["42"] = [100.0, 200.0]
    random_paro._save_stats()

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(random_paro._new_stats_state())
    random_paro.reload_paro_data()

    assert random_paro._cooldown_store()["42"] == [100.0, 200.0]


def test_build_rank_images_do_not_crash_without_assets(isolated_paro_stats):
    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=[("黑百合", "王子冬", True, None), ("白骑", "黑骑", False, "foxbun")],
        fixed_side=None,
        fixed_name=None,
        requested_count=2,
        now_ts=123.0,
    )

    assert isinstance(random_paro._build_paro_rank_image(1001, "daily"), bytes)
    assert isinstance(random_paro._build_egg_rank_image(1001, "history"), bytes)
    assert isinstance(random_paro._build_personal_paro_image(1001, "42", "测试用户"), bytes)
    assert isinstance(random_paro._build_rank_preview_image("daily"), bytes)
    assert isinstance(random_paro._build_egg_rank_preview_image("history"), bytes)
    assert isinstance(random_paro._build_personal_preview_image(), bytes)


@pytest.mark.asyncio
async def test_draw_command_rejects_private_chat():
    event = Event()

    with pytest.raises(FinishedException) as exc:
        await random_paro.draw_cmd.handlers[0](event, event.message)

    assert "该指令仅支持群聊使用" in str(exc.value.result)


@pytest.mark.asyncio
async def test_daily_rank_command_rejects_private_chat():
    event = Event()

    with pytest.raises(FinishedException) as exc:
        await random_paro.daily_rank_cmd.handlers[0](event)

    assert "该指令仅支持群聊使用" in str(exc.value.result)


@pytest.mark.asyncio
async def test_my_paro_command_rejects_private_chat():
    event = Event()

    with pytest.raises(FinishedException) as exc:
        await random_paro.my_paro_cmd.handlers[0](event)

    assert "该指令仅支持群聊使用" in str(exc.value.result)


@pytest.mark.asyncio
async def test_preview_rank_commands_ignore_non_superuser_group_event():
    event = Event(group_id=1001, user_id="12345")

    await random_paro.test_daily_rank_cmd.handlers[0](event)
    await random_paro.test_history_rank_cmd.handlers[0](event)
    await random_paro.test_daily_egg_rank_cmd.handlers[0](event)
    await random_paro.test_history_egg_rank_cmd.handlers[0](event)
    await random_paro.test_my_paro_cmd.handlers[0](event)

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.bond_pages as bond_pages
import nonebot_plugin_akito.features.bond_render as bond_render
import nonebot_plugin_akito.features.rpg.boss as boss
import nonebot_plugin_akito.features.rpg.config as rpg_config

from .helpers import _bot, _equipped_user, _patch_io, _team_event, _world_boss_record


def test_game_store_normalize_preserves_group_rpg():
    data = game_store._normalize_data({
        "groups": {
            "1001": {
                "users": {},
                "intimacy": {},
                "counts": {},
                "rpg": {"world_boss": {"hp": 9}},
            }
        }
    })
    assert data["groups"]["1001"]["rpg"]["world_boss"]["hp"] == 9


def test_world_boss_snapshot_uses_recent_active_not_today():
    group = game_store._new_group()
    recent_days = [
        "2026-06-22",
        "2026-06-21",
        "2026-06-20",
        "2026-06-19",
        "2026-06-18",
        "2026-06-17",
        "2026-06-16",
        "2026-06-16",
    ]
    for idx, day in enumerate(recent_days, 1):
        group["users"][f"u{idx}"] = {"exp": 0, "last_sign_in": day, "signin_last_date": day}

    snapshot = boss._world_boss_snapshot(group, "2026-06-22")

    assert snapshot["recent_active_count"] == 8
    assert snapshot["scale_count"] == 8
    assert snapshot["reward_scale_count"] == 8
    assert snapshot["max_hp"] == round(
        boss._expected_daily_power({}) * 8 * float(rpg_config._cfg("world_boss", {}).get("hp_factor", 1.0))
    )


def test_world_boss_snapshot_soft_scales_large_group():
    cfg = rpg_config._cfg("world_boss", {})
    group = game_store._new_group()
    for idx in range(1, 48):
        group["users"][f"u{idx}"] = {"exp": 0, "last_sign_in": "2026-06-22", "signin_last_date": "2026-06-22"}

    snapshot = boss._world_boss_snapshot(group, "2026-06-22")
    base_cap = int(cfg.get("activity_scale_cap", 12))
    expected_hp_scale = boss._soft_scale_count(
        47,
        base_cap=base_cap,
        extra_rate=float(cfg.get("hp_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("hp_scale_max", base_cap)),
    )
    expected_reward_scale = boss._soft_scale_count(
        47,
        base_cap=base_cap,
        extra_rate=float(cfg.get("reward_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("reward_scale_max", base_cap)),
    )

    assert snapshot["recent_active_count"] == 47
    assert snapshot["scale_count"] == expected_hp_scale
    assert snapshot["reward_scale_count"] == expected_reward_scale
    assert snapshot["max_hp"] == round(
        boss._expected_daily_power({}) * expected_hp_scale * float(cfg.get("hp_factor", 1.0))
    )


def test_cleanup_stale_world_boss_grants_scaled_compensation():
    group = game_store._new_group()
    group["users"]["u1"] = {"exp": 0, "points": 0, "display_name": "阿一"}
    group["users"]["u2"] = {"exp": 0, "points": 0, "display_name": "阿二"}
    stale_boss = _world_boss_record(
        date="2026-06-21",
        max_hp=200,
        hp=120,
        reward_scale_count=3,
        contributors={"u1": 50, "u2": 30},
    )
    group["rpg"] = {
        "world_boss": deepcopy(stale_boss)
    }

    lines, changed = boss._cleanup_stale_world_boss(group, "2026-06-22")

    reward_ratio = (80 / 200) * float(rpg_config._cfg("world_boss", {}).get("rewards", {}).get("unfinished_reward_mult", 0.5))
    reward_values = boss._world_boss_reward_values(stale_boss, reward_ratio=reward_ratio)
    exp_alloc = boss._allocate_exact(reward_values["exp_pool"], {"u1": 50, "u2": 30})
    points_alloc = boss._allocate_exact(reward_values["points_pool"], {"u1": 50, "u2": 30})

    assert changed is True
    assert "world_boss" not in group["rpg"]
    assert group["users"]["u1"]["exp"] == reward_values["exp_fixed"] + exp_alloc["u1"]
    assert group["users"]["u2"]["exp"] == reward_values["exp_fixed"] + exp_alloc["u2"]
    assert group["users"]["u1"]["points"] == reward_values["points_fixed"] + points_alloc["u1"]
    assert group["users"]["u2"]["points"] == reward_values["points_fixed"] + points_alloc["u2"]
    assert any("已经离场" in line for line in lines)


def test_world_boss_does_not_spawn_when_recent_active_under_min():
    class _SpawnRng:
        def random(self):
            return 0.0

        def choice(self, seq):
            return seq[0]

    group = game_store._new_group()
    group["users"]["u1"] = {"exp": 0, "last_sign_in": "2026-06-22"}
    group["users"]["u2"] = {"exp": 0, "last_sign_in": "2026-06-21"}

    lines = boss._maybe_spawn_world_boss_lines(group, "2026-06-22", "u1", rng=_SpawnRng())

    assert lines == []
    assert group["rpg"] == {}


@pytest.mark.asyncio
async def test_world_boss_status_no_active_boss(monkeypatch):
    _patch_io(monkeypatch, boss, store={"groups": {"1001": {"users": {}}}})

    with pytest.raises(FinishedException) as exc:
        await boss.world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    assert "当前没有可挑战的世界BOSS" in str(exc.value.result)


@pytest.mark.asyncio
async def test_world_boss_status_settles_stale_boss_before_reply(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": {"exp": 0, "points": 0, "display_name": "阿一"}},
        "rpg": {"world_boss": _world_boss_record(date="2026-06-21", max_hp=100, hp=70, contributors={"u1": 30})},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)

    with pytest.raises(FinishedException) as exc:
        await boss.world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "已经离场" in result
    assert "当前没有可挑战的世界BOSS" in result
    assert "world_boss" not in state["groups"]["1001"]["rpg"]
    assert state["groups"]["1001"]["users"]["u1"]["exp"] > 0


@pytest.mark.asyncio
async def test_world_boss_status_shows_hp_and_contributors(monkeypatch):
    _patch_io(monkeypatch, boss, store={"groups": {"1001": {
        "users": {
            "u1": {"display_name": "阿一"},
            "u2": {"display_name": "阿二"},
        },
        "rpg": {"world_boss": _world_boss_record(hp=80, contributors={"u1": 45, "u2": 20})},
    }}})

    with pytest.raises(FinishedException) as exc:
        await boss.world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "深渊巨像" in result
    assert "80/120" in result
    assert "阿一" in result and "45" in result


@pytest.mark.asyncio
async def test_force_world_boss_cmd_spawns_without_recent_activity(monkeypatch):
    state = _patch_io(monkeypatch, boss, store={"groups": {"1001": {"users": {}}}})
    monkeypatch.setattr(boss.random, "choice", lambda seq: seq[0])

    with pytest.raises(FinishedException) as exc:
        await boss.force_world_boss_cmd.handlers[0](Event(group_id=1001, user_id=boss.SUPERUSER_QQ))

    wb = state["groups"]["1001"]["rpg"]["world_boss"]
    result = str(exc.value.result)
    assert wb["recent_active_count"] == 0
    assert wb["scale_count"] == 1
    assert wb["reward_scale_count"] == 1
    assert wb["hp"] == wb["max_hp"] and wb["max_hp"] > 1
    assert "已强制开启世界BOSS测试" in result
    assert wb["name"] in result


@pytest.mark.asyncio
async def test_force_world_boss_cmd_keeps_existing_boss(monkeypatch):
    state = _patch_io(monkeypatch, boss, store={"groups": {"1001": {
        "users": {},
        "rpg": {"world_boss": _world_boss_record(hp=80)},
    }}})
    before = deepcopy(state["groups"]["1001"]["rpg"]["world_boss"])

    with pytest.raises(FinishedException) as exc:
        await boss.force_world_boss_cmd.handlers[0](Event(group_id=1001, user_id=boss.SUPERUSER_QQ))

    result = str(exc.value.result)
    assert state["groups"]["1001"]["rpg"]["world_boss"] == before
    assert "当前已经有世界BOSS了" in result
    assert "80/120" in result


@pytest.mark.asyncio
async def test_team_world_boss_guards(monkeypatch):
    state = _patch_io(monkeypatch, boss, store={"groups": {"1001": {
        "users": {"u1": _equipped_user()},
        "rpg": {"world_boss": _world_boss_record()},
    }}})
    snapshot = deepcopy(state)
    assert await boss.team_world_boss_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1", original_message=[])) is None
    assert state == snapshot
    assert await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "all")) is None
    assert state == snapshot
    assert await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "u1")) is None
    assert state == snapshot
    assert await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "114514")) is None
    assert state == snapshot


@pytest.mark.asyncio
async def test_attack_world_boss_consumes_equip_and_records_damage(monkeypatch):
    state = _patch_io(monkeypatch, boss, store={"groups": {"1001": {
        "users": {"u1": _equipped_user(hunt_total=7, hunt_wins=4)},
        "rpg": {"world_boss": _world_boss_record(max_hp=100, hp=100)},
    }}})
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(FinishedException) as exc:
        await boss.attack_world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    wb = state["groups"]["1001"]["rpg"]["world_boss"]
    participant = wb["participants"]["u1"]
    assert user["equip_used"] is False
    assert participant["equip_used"] is True
    assert wb["hp"] == 85
    assert wb["contributors"]["u1"] == 15
    assert user["hunt_total"] == 7 and user["hunt_wins"] == 4
    assert "15 点伤害" in str(exc.value.result)


@pytest.mark.asyncio
async def test_attack_world_boss_still_available_after_normal_hunt(monkeypatch):
    state = _patch_io(monkeypatch, boss, store={"groups": {"1001": {
        "users": {"u1": _equipped_user(equip_used=True)},
        "rpg": {"world_boss": _world_boss_record(max_hp=100, hp=100)},
    }}})
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(FinishedException) as exc:
        await boss.attack_world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    wb = state["groups"]["1001"]["rpg"]["world_boss"]
    participant = wb["participants"]["u1"]
    assert user["equip_used"] is True
    assert participant["equip_used"] is True
    assert wb["hp"] == 85
    assert wb["contributors"]["u1"] == 15
    assert "15 点伤害" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_world_boss_success_uses_explicit_team_power_bonus(monkeypatch):
    pair = game_store._pair_key("u1", "u2")
    store = {"groups": {"1001": {
        "users": {
            "u1": _equipped_user(points=0),
            "u2": _equipped_user(points=0),
        },
        "intimacy": {pair: 20000},
        "rpg": {"world_boss": _world_boss_record(max_hp=200, hp=200)},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)
    monkeypatch.setattr(boss.random, "random", lambda: 0.0)
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(FinishedException) as exc:
        await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    users = state["groups"]["1001"]["users"]
    wb = state["groups"]["1001"]["rpg"]["world_boss"]
    participants = wb["participants"]
    assert users["u1"]["equip_used"] is False and users["u2"]["equip_used"] is False
    assert participants["u1"]["equip_used"] is True and participants["u2"]["equip_used"] is True
    assert wb["contributors"]["u1"] == 16 and wb["contributors"]["u2"] == 16
    assert wb["hp"] == 168
    assert wb["bond_gains"] == {"u1": 1, "u2": 1}
    assert state["groups"]["1001"]["intimacy"][pair] == 20001
    result = str(exc.value.result)
    assert "16 点" in result and "32 点" in result
    assert "协作加成" in result and "2 点总伤害" in result


@pytest.mark.asyncio
async def test_team_world_boss_fail_only_consumes_initiator(monkeypatch):
    store = {"groups": {"1001": {
        "users": {
            "u1": _equipped_user(points=0),
            "u2": _equipped_user(points=0),
        },
        "rpg": {"world_boss": _world_boss_record(max_hp=100, hp=100)},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)
    monkeypatch.setattr(boss.random, "random", lambda: 0.999)
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(boss, "_roll_team_fail_flavor", lambda rng=boss.random: "hesitate")

    with pytest.raises(FinishedException) as exc:
        await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    users = state["groups"]["1001"]["users"]
    wb = state["groups"]["1001"]["rpg"]["world_boss"]
    participants = wb["participants"]
    assert users["u1"]["equip_used"] is False and users["u2"]["equip_used"] is False
    assert participants["u1"]["equip_used"] is True and participants["u2"]["equip_used"] is False
    assert wb["contributors"]["u1"] == 15
    assert "没能会合" in str(exc.value.result)


@pytest.mark.asyncio
async def test_world_boss_kill_settlement_preserves_reward_pool_and_battle_stats(monkeypatch):
    store = {"groups": {"1001": {
        "users": {
            "u1": _equipped_user(hunt_total=7, hunt_wins=3),
            "u2": {"exp": 0, "points": 0, "hunt_total": 2, "hunt_wins": 1},
        },
        "rpg": {"world_boss": _world_boss_record(max_hp=12, hp=2, scale_count=3, reward_scale_count=3, contributors={"u1": 4, "u2": 6})},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)
    async def _fake_render(_settlement):
        return b"fake-world-boss-rank"
    monkeypatch.setattr(boss, "_render_world_boss_settlement_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await boss.attack_world_boss_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    users = state["groups"]["1001"]["users"]
    rewards = rpg_config._cfg("world_boss", {}).get("rewards", {})
    exp_pool = int(rewards.get("exp_pool_per_scale", 60)) * 3
    points_pool = int(rewards.get("points_pool_per_scale", 8)) * 3
    exp_fixed = int(rewards.get("exp_fixed", 12))
    points_fixed = int(rewards.get("points_fixed", 2))
    last_hit_exp_bonus = int(rewards.get("last_hit_exp_bonus", 0))
    last_hit_points_bonus = int(rewards.get("last_hit_points_bonus", 0))
    alloc = boss._allocate_exact(exp_pool, {"u1": 6, "u2": 6})
    points_alloc = boss._allocate_exact(points_pool, {"u1": 6, "u2": 6})
    assert "world_boss" not in state["groups"]["1001"]["rpg"]
    assert users["u1"]["exp"] == exp_fixed + alloc["u1"] + last_hit_exp_bonus
    assert users["u2"]["exp"] == exp_fixed + alloc["u2"]
    assert users["u1"]["points"] == points_fixed + points_alloc["u1"] + last_hit_points_bonus
    assert users["u2"]["points"] == points_fixed + points_alloc["u2"]
    assert users["u1"]["hunt_total"] == 7 and users["u1"]["hunt_wins"] == 3
    assert users["u2"]["hunt_total"] == 2 and users["u2"]["hunt_wins"] == 1
    result = exc.value.result
    text = str(result)
    assert "世界BOSS 已经被成功击杀" in text
    assert "拿下了尾刀" in text
    assert "造成了" not in text
    assert "经验 +" not in text
    assert "[image]" in text


@pytest.mark.asyncio
async def test_team_world_boss_kill_grants_full_last_hit_bonus_to_both(monkeypatch):
    store = {"groups": {"1001": {
        "users": {
            "u1": _equipped_user(hunt_total=3, hunt_wins=1),
            "u2": _equipped_user(hunt_total=5, hunt_wins=2),
        },
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
        "rpg": {"world_boss": _world_boss_record(max_hp=20, hp=20, scale_count=3, reward_scale_count=3)},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)
    monkeypatch.setattr(boss.random, "random", lambda: 0.0)
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)
    async def _fake_render(_settlement):
        return b"fake-world-boss-rank"
    monkeypatch.setattr(boss, "_render_world_boss_settlement_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    users = state["groups"]["1001"]["users"]
    rewards = rpg_config._cfg("world_boss", {}).get("rewards", {})
    exp_pool = int(rewards.get("exp_pool_per_scale", 60)) * 3
    points_pool = int(rewards.get("points_pool_per_scale", 8)) * 3
    exp_fixed = int(rewards.get("exp_fixed", 12))
    points_fixed = int(rewards.get("points_fixed", 2))
    last_hit_exp_bonus = int(rewards.get("last_hit_exp_bonus", 0))
    last_hit_points_bonus = int(rewards.get("last_hit_points_bonus", 0))
    alloc = boss._allocate_exact(exp_pool, {"u1": 10, "u2": 10})
    points_alloc = boss._allocate_exact(points_pool, {"u1": 10, "u2": 10})

    assert "world_boss" not in state["groups"]["1001"]["rpg"]
    assert users["u1"]["exp"] == exp_fixed + alloc["u1"] + last_hit_exp_bonus
    assert users["u2"]["exp"] == exp_fixed + alloc["u2"] + last_hit_exp_bonus
    assert users["u1"]["points"] == points_fixed + points_alloc["u1"] + last_hit_points_bonus
    assert users["u2"]["points"] == points_fixed + points_alloc["u2"] + last_hit_points_bonus
    text = str(exc.value.result)
    assert "世界BOSS 已经被成功击杀" in text
    assert "u1" not in text
    assert "[image]" in text


@pytest.mark.asyncio
async def test_test_world_rank_cmd_renders_image(monkeypatch):
    async def _fake_render(_settlement):
        return b"fake-world-boss-rank"

    monkeypatch.setattr(boss, "_render_world_boss_settlement_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await boss.test_world_rank_cmd.handlers[0](Event(group_id=1001, user_id=boss.SUPERUSER_QQ))

    text = str(exc.value.result)
    assert "世界BOSS 已经被成功击杀" in text
    assert "测试冒险者03 拿下了尾刀" in text
    assert "[image]" in text


@pytest.mark.asyncio
async def test_team_world_boss_fail_kill_only_shows_settlement_lines(monkeypatch):
    store = {"groups": {"1001": {
        "users": {
            "u1": _equipped_user(points=0),
            "u2": _equipped_user(points=0),
        },
        "rpg": {"world_boss": _world_boss_record(max_hp=15, hp=15)},
    }}}
    state = _patch_io(monkeypatch, boss, store=store)
    monkeypatch.setattr(boss.random, "random", lambda: 0.999)
    monkeypatch.setattr(boss.random, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(boss.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(boss, "_roll_team_fail_flavor", lambda rng=boss.random: "hesitate")
    async def _fake_render(_settlement):
        return b"fake-world-boss-rank"
    monkeypatch.setattr(boss, "_render_world_boss_settlement_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await boss.team_world_boss_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    text = str(exc.value.result)
    assert "世界BOSS 已经被成功击杀" in text
    assert "拿下了尾刀" in text
    assert "没能会合" not in text
    assert "造成了 15 点伤害" not in text
    assert "[image]" in text
    assert "world_boss" not in state["groups"]["1001"]["rpg"]


def test_world_boss_rank_template_renders_flat_top_stats():
    page_data = bond_pages.build_world_boss_rank_page_data("赤鳞灾龙", [row.copy() for row in boss._TEST_WORLD_BOSS_ROWS])
    html = bond_render._TEMPLATE_ENV.get_template("world_boss_rank.html").render(**page_data)

    assert "本次共有" not in html
    assert "参与人数" in html
    assert "新增羁绊" in html
    assert "羁绊 +1" in html
    assert "最终榜单" in html


def test_world_boss_kill_settlement_carries_team_bond_gain():
    pair = game_store._pair_key("u1", "u2")
    group = game_store._normalize_data({"groups": {"1001": {
        "users": {
            "u1": _equipped_user(),
            "u2": _equipped_user(),
        },
        "intimacy": {pair: -40},
        "rpg": {"world_boss": _world_boss_record(
            max_hp=20,
            hp=0,
            contributors={"u1": 10, "u2": 10},
            bond_gains={"u1": 2, "u2": 2},
        )},
    }}})["groups"]["1001"]

    settlement = boss._world_boss_kill_settlement(
        group,
        group["rpg"]["world_boss"],
        last_hit_uids=["u2"],
    )

    rows = {row["uid"]: row for row in settlement["rows"]}
    assert settlement["total_bond"] == 4
    assert rows["u1"]["bond"] == 2
    assert rows["u2"]["bond"] == 2
    reward_lines = boss._world_boss_reward_lines(settlement["rows"])
    assert any("羁绊 +2" in line for line in reward_lines)


def test_world_boss_kill_settlement_shared_last_hit_bonus_for_team():
    group = game_store._normalize_data({"groups": {"1001": {
        "users": {
            "u1": _equipped_user(display_name="阿一"),
            "u2": _equipped_user(display_name="阿二"),
        },
        "rpg": {"world_boss": _world_boss_record(
            max_hp=20,
            hp=0,
            contributors={"u1": 10, "u2": 10},
            last_hit_uids=["u1", "u2"],
        )},
    }}})["groups"]["1001"]

    settlement = boss._world_boss_kill_settlement(
        group,
        group["rpg"]["world_boss"],
        last_hit_uids=["u1", "u2"],
    )

    rows = {row["uid"]: row for row in settlement["rows"]}
    reward_cfg = rpg_config._cfg("world_boss", {}).get("rewards", {})
    assert rows["u1"]["exp_bonus"] == int(reward_cfg.get("last_hit_exp_bonus", 0))
    assert rows["u2"]["exp_bonus"] == int(reward_cfg.get("last_hit_exp_bonus", 0))
    assert rows["u1"]["points_bonus"] == int(reward_cfg.get("last_hit_points_bonus", 0))
    assert rows["u2"]["points_bonus"] == int(reward_cfg.get("last_hit_points_bonus", 0))
    assert settlement["last_hit_name"] == "阿一 / 阿二"
    assert any("阿一 / 阿二 拿下了尾刀" in line for line in settlement["lines"])


def test_world_boss_special_drop_only_awards_once(monkeypatch):
    user = _equipped_user(world_boss_trophies=["赤鳞龙鳞"])
    boss_rec = _world_boss_record(name="赤鳞灾龙")

    monkeypatch.setattr(boss.random, "random", lambda: 0.0)

    assert boss._roll_world_boss_special_drop(user, boss_rec) == ""
    assert user["world_boss_trophies"] == ["赤鳞龙鳞"]


def test_world_boss_special_drop_awards_configured_item(monkeypatch):
    user = _equipped_user()
    boss_rec = _world_boss_record(name="断潮魔虾")

    monkeypatch.setattr(boss.random, "random", lambda: 0.0)

    assert boss._roll_world_boss_special_drop(user, boss_rec) == "断潮虾壳"
    assert user["world_boss_trophies"] == ["断潮虾壳"]


def test_cleanup_stale_world_boss_compensation_does_not_award_special_drop(monkeypatch):
    group = game_store._new_group()
    group["users"]["u1"] = _equipped_user(display_name="阿一")
    stale_boss = _world_boss_record(
        name="赤鳞灾龙",
        date="2026-06-21",
        max_hp=200,
        hp=120,
        reward_scale_count=3,
        contributors={"u1": 80},
    )
    group["rpg"] = {"world_boss": deepcopy(stale_boss)}

    monkeypatch.setattr(boss.random, "random", lambda: 0.0)

    lines, changed = boss._cleanup_stale_world_boss(group, "2026-06-22")

    assert changed is True
    assert group["users"]["u1"]["world_boss_trophies"] == []
    assert all("赤鳞龙鳞" not in line for line in lines)

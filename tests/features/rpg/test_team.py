from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.boss as boss
import nonebot_plugin_akito.features.rpg.combat as combat
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.events as rpg_events
import nonebot_plugin_akito.features.rpg.player as player
import nonebot_plugin_akito.features.rpg.rewards as rewards
import nonebot_plugin_akito.features.rpg.team as team
import nonebot_plugin_akito.features.rpg.utils as rpg_utils

from .helpers import _PLAIN_BUFF, _bot, _equipped_user, _patch_io, _Rng, _stub_hunt_rng, _team_event


def test_team_success_rate_scales_and_clamps():
    t = rpg_config._cfg("team", {})
    base, step = float(t["base_success"]), float(t["per_level"])
    neg_step = float(t["negative_per_level"])
    assert team._team_success_rate(1) == pytest.approx(base)              # Lv1 = base
    assert team._team_success_rate(3) == pytest.approx(base + 2 * step)   # 随羁绊等级爬升
    assert team._team_success_rate(0) == pytest.approx(base - neg_step)   # 轻度负羁绊缓降
    assert team._team_success_rate(-1) == pytest.approx(base - 2 * neg_step)
    assert team._team_success_rate(99) == pytest.approx(float(t["max_success"]))   # 封顶
    assert team._team_success_rate(-99) == pytest.approx(float(t["min_success"]))   # 深度负羁绊封底


def test_world_boss_team_success_rate_matches_normal_team_formula():
    for bond_level in (6, 3, 1, 0, -1, -5):
        assert boss._team_success_rate(bond_level) == pytest.approx(team._team_success_rate(bond_level))


def test_team_exp_bonus_scales_and_caps():
    t = rpg_config._cfg("team", {})
    per, cap = float(t["exp_bonus_per_level"]), float(t["exp_bonus_max"])
    assert team._team_exp_bonus(1) == 0.0                       # Lv1 无加成
    assert team._team_exp_bonus(3) == pytest.approx(2 * per)
    assert team._team_exp_bonus(9999) == pytest.approx(cap)     # 封顶


def test_negative_team_event_chance_tiers():
    neg = rpg_config._cfg("team", {}).get("negative", {})
    assert team._negative_team_event_chance(0) == 0.0
    assert team._negative_team_event_chance(-1) == pytest.approx(float(neg["chance_mild"]))
    assert team._negative_team_event_chance(int(neg["mild_threshold"])) == pytest.approx(float(neg["chance_medium"]))
    assert team._negative_team_event_chance(int(neg["deep_threshold"])) == pytest.approx(float(neg["chance_deep"]))


def test_team_bond_gain_respects_daily_limit_and_break_ice_bonus():
    group = game_store._new_group()
    today = "2026-06-22"
    bonus = int(team._negative_team_event_spec("break_ice").get("bond_bonus", 0))

    gain1 = team._grant_team_bond(group, "u1", "u2", today, win=True, extra=bonus)
    gain2 = team._grant_team_bond(group, "u1", "u2", today, win=True, extra=bonus)
    gain3 = team._grant_team_bond(group, "u1", "u2", "2026-06-23", win=False)

    assert gain1 == 6
    assert gain2 == 0
    assert gain3 == 2
    assert game_store._get_intimacy(group, "u1", "u2") == 8


def test_team_drop_bonus_scales_and_caps():
    t = rpg_config._cfg("team", {})
    per, cap = float(t["drop_bonus_per_level"]), float(t["drop_bonus_max"])
    assert team._team_drop_bonus(1) == 0.0
    assert team._team_drop_bonus(3) == pytest.approx(2 * per)
    assert team._team_drop_bonus(9999) == pytest.approx(cap)


@pytest.mark.asyncio
async def test_team_guards(monkeypatch):
    state = _patch_io(monkeypatch, team, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    snapshot = deepcopy(state)
    assert await team.team_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1", original_message=[])) is None
    assert state == snapshot
    assert await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u1")) is None
    assert state == snapshot
    assert await team.team_cmd.handlers[0](_bot(), _team_event("u1", "114514")) is None
    assert state == snapshot


@pytest.mark.asyncio
async def test_team_success_both_rewarded(monkeypatch):
    # 顶级羁绊 + random=0 → 必成功；合力打弱怪，双方各得经验+积分、各自装备都消耗、@ 双方
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "focus_fire")
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is True   # 双方装备都消耗
    win_pts = int(rpg_config._cfg("challenge", {})["win_points"])
    assert g["u1"]["points"] == win_pts and g["u2"]["points"] == win_pts      # 双方各得积分
    assert g["u1"]["exp"] > 0 and g["u2"]["exp"] > 0
    assert state["groups"]["1001"]["intimacy"][game_store._pair_key("u1", "u2")] == 20004
    assert "协作加成" in str(exc.value.result)
    r = str(exc.value.result)
    assert "同好羁绊 +4" in r
    assert "[at:u1]" in r and "[at:u2]" in r                                   # @ 双方


def test_settle_coop_uses_higher_level_for_encounter(monkeypatch):
    captured: dict = {}
    monster = {"name": "史莱姆", "power_req": 1, "drops": []}

    def _pick(level, rng=combat.random):
        captured["level"] = level
        return monster, False

    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}
    monkeypatch.setattr(combat, "_pick_encounter", _pick)
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        combat,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        {"win": True, "effective": int(combat_power * power_factor * fortune_factor),
         "event": event or "", "monster": eff_monster},
    )
    monkeypatch.setattr(rewards, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    rewards._settle_coop(
        _equipped_user(exp=player._cum_exp(2, player._level_base()), equip_level=2),
        _equipped_user(exp=player._cum_exp(7, player._level_base()), equip_level=7),
        "D",
    )

    assert captured["level"] == 7


def test_settle_coop_uses_average_fortune_factor(monkeypatch):
    captured: dict = {}
    monster = {"name": "slime", "power_req": 1, "drops": []}
    left = _equipped_user()
    right = _equipped_user()
    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        rpg_utils,
        "_fortune_combat_factor",
        lambda user, today, enabled=True: 1.4 if user is left else 0.8,
    )
    monkeypatch.setattr(
        combat,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"fortune_factor": fortune_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )
    monkeypatch.setattr(rewards, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    rewards._settle_coop(left, right, "D")

    assert captured["fortune_factor"] == pytest.approx(1.1)


def test_team_guard_prefers_initiator_and_only_buffs_holder_exp(monkeypatch):
    monster = {"name": "强敌", "power_req": 999, "drops": []}
    left = _equipped_user(active_battle_guard={"name": "神官的护符", "uses": 1})
    right = _equipped_user(active_battle_guard={"name": "神官的护符", "uses": 1})
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)

    out = rewards._settle_coop(left, right, "2026-06-22")

    base_exp = rewards._challenge_exp(True, 1)
    assert out["base_win"] is False and out["win"] is True
    assert out["battle_guard_owner"] == "b"
    assert out["b"]["exp_gain"] == int(base_exp * 1.5)
    assert out["a"]["exp_gain"] == base_exp
    assert "active_battle_guard" not in left
    assert right["active_battle_guard"]["uses"] == 1
    broadcast = str(team._build_coop_broadcast(out, "u1", "u2", "甲", "乙"))
    assert "没能取胜" in broadcast or "未能击败" in broadcast or "败下阵来" in broadcast or "没能拿下" in broadcast
    assert "神官的护符" in broadcast and "转为成功" in broadcast


def test_team_regular_supply_only_changes_its_owner_rewards(monkeypatch):
    monster = {"name": "史莱姆", "power_req": 1, "drops": []}
    left = _equipped_user(active_battle_supply={"name": "旅人的行囊", "uses": 2})
    right = _equipped_user()
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)

    out = rewards._settle_coop(left, right, "2026-06-22")

    base_exp = rewards._challenge_exp(True, 1)
    assert out["b"]["exp_gain"] == int(base_exp * 1.25)
    assert out["a"]["exp_gain"] == base_exp
    assert left["active_battle_supply"]["uses"] == 1
    assert "active_battle_supply" not in right


def test_team_scallion_cake_only_reduces_its_owner_rewards(monkeypatch):
    monster = {"name": "史莱姆", "power_req": 1, "drops": []}
    left = _equipped_user(active_battle_debuff={"name": "大葱味蛋糕", "uses": 1})
    right = _equipped_user()
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)

    out = rewards._settle_coop(left, right, "2026-06-22")

    base_exp = rewards._challenge_exp(True, 1)
    base_points = int(rpg_config._cfg("challenge", {})["win_points"])
    assert out["b"]["exp_gain"] == int(base_exp * 0.85)
    assert out["b"]["points_gain"] == int(base_points * 0.9)
    assert out["a"]["exp_gain"] == base_exp
    assert out["a"]["points_gain"] == base_points
    assert "active_battle_debuff" not in left
    assert "active_battle_debuff" not in right


@pytest.mark.parametrize(
    ("event_key", "expected_power", "expected_exp_mult", "expected_drop_mult"),
    [
        ("focus_fire", (1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0))) * 1.10, 1.10, 1.25),
        ("cover_route", 1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0)), 1.00, 1.35 * 1.25),
        ("follow_up", 1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0)), 1.20, 1.25),
        ("missed_beat", (1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0))) * 0.90, 1.00, 1.25),
    ],
)
def test_settle_coop_applies_team_event_and_drop_bonus(
    monkeypatch,
    event_key,
    expected_power,
    expected_exp_mult,
    expected_drop_mult,
):
    captured: dict = {"reward_kwargs": []}
    monster = {"name": "slime", "power_req": 1, "drops": []}
    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: event_key)
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        combat,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"power_factor": power_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )

    def _apply_rewards(*args, **kwargs):
        captured["reward_kwargs"].append(kwargs)
        return dict(reward)

    monkeypatch.setattr(rewards, "_apply_rewards", _apply_rewards)

    rewards._settle_coop(_equipped_user(), _equipped_user(), "D", exp_bonus=0.15, drop_bonus=0.25)

    assert captured["power_factor"] == pytest.approx(expected_power)
    assert len(captured["reward_kwargs"]) == 2
    for kwargs in captured["reward_kwargs"]:
        assert kwargs["exp_bonus"] == pytest.approx(0.15)
        assert kwargs["exp_mult"] == pytest.approx(expected_exp_mult)
        assert kwargs["drop_mult"] == pytest.approx(expected_drop_mult)


def test_settle_coop_applies_extra_negative_multipliers(monkeypatch):
    captured: dict = {"reward_kwargs": []}
    monster = {"name": "slime", "power_req": 1, "drops": []}
    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng=combat.random: (monster, False))
    monkeypatch.setattr(rewards.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        combat,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"power_factor": power_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )

    def _apply_rewards(*args, **kwargs):
        captured["reward_kwargs"].append(kwargs)
        return dict(reward)

    monkeypatch.setattr(rewards, "_apply_rewards", _apply_rewards)

    rewards._settle_coop(
        _equipped_user(),
        _equipped_user(),
        "D",
        extra_power_mult=0.92,
        extra_exp_mult=0.93,
        extra_drop_mult=0.85,
    )

    expected_power = (1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0))) * 0.92
    assert captured["power_factor"] == pytest.approx(expected_power)
    assert len(captured["reward_kwargs"]) == 2
    for kwargs in captured["reward_kwargs"]:
        assert kwargs["exp_mult"] == pytest.approx(0.93)
        assert kwargs["drop_mult"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_team_rejects_target_no_signin(monkeypatch):
    # 对方今天未签到 → 硬性拒绝，不退化单刷
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": {"exp": 0}}}}}
    state = _patch_io(monkeypatch, team, store=store)
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is False              # 发起人装备未消耗
    assert "未签到" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_rejects_target_broken_equip(monkeypatch):
    # 对方装备已损坏 → 硬性拒绝，不退化单刷
    u2 = _equipped_user(points=0)
    u2["equip_used"] = True
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": u2}}}}
    state = _patch_io(monkeypatch, team, store=store)
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is False              # 发起人装备未消耗
    assert "损坏" in str(exc.value.result)
    assert "购买装备" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_fail_by_rng_degrades_to_solo(monkeypatch):
    # 无羁绊（低成功率）+ random=0.999 → 拉不动，退化单刷；只消耗发起人装备
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)}}}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.999))
    monkeypatch.setattr(team, "_roll_fail_flavor", lambda rng=team.random: "hesitate")
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is False
    assert g["u1"]["exp"] == rewards._challenge_exp(True, 1)
    assert g["u1"]["points"] == rewards._challenge_points(True, g["u1"])
    assert g["u2"]["exp"] == 0 and g["u2"]["points"] == 0
    assert "独自前往" in str(exc.value.result)
    assert "迟疑" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_fail_rescue_runs_normal_coop_settlement(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.999))
    monkeypatch.setattr(team, "_roll_fail_flavor", lambda rng=team.random: "late_reply")
    monkeypatch.setattr(team, "_roll_team_fail_rescue", lambda rng=team.random: True)
    monkeypatch.setattr(rpg_events, "_roll_support_variant", lambda rng=rpg_events.random: "default")
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    users = state["groups"]["1001"]["users"]
    pair = game_store._pair_key("u1", "u2")
    assert users["u1"]["equip_used"] is True and users["u2"]["equip_used"] is True
    assert users["u1"]["exp"] > 0 and users["u2"]["exp"] > 0
    assert state["groups"]["1001"]["intimacy"][pair] == 4
    result = str(exc.value.result)
    assert "本次组队成立" in result
    assert "送到了" in result
    assert any(
        text in result
        for text in (
            "转机却在最后一刻出现了",
            "局势忽然有了变化",
            "等来了转机",
            "新的变化",
        )
    )
    assert "[CQ:at" not in result
    assert "独自前往" not in result
    assert "[at:u1]" in result and "[at:u2]" in result


@pytest.mark.parametrize(
    ("fail_event", "marker"),
    [
        ("hesitate", "飘荡着一股松饼的香气"),
        ("late_reply", "一起吃松饼吃得错过组队"),
        ("out_of_step", "一千个松饼挡住了敌人"),
    ],
)
def test_team_fail_rescue_dogbin_fox_variant_renders(fail_event, marker):
    member = {"exp_gain": 0, "points_gain": 0, "drops": [], "old_level": 1, "new_level": 1}
    out = {
        "monster": {"name": "史莱姆"},
        "win": True,
        "team_support_variant": "dogbin_fox",
        "team_event": "",
        "negative_event": "",
        "battle_guard_owner": "",
        "power_bonus": 0,
        "exp_bonus": 0,
        "drop_bonus": 0,
        "bond_gain": 0,
        "b": member,
        "a": member,
    }

    result = str(team._build_fail_rescue_broadcast(out, "u1", "u2", "甲", "乙", fail_event))

    assert marker in result


@pytest.mark.asyncio
async def test_team_negative_break_ice_grants_extra_bond(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): -60},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    monkeypatch.setattr(team, "_roll_negative_team_event", lambda intimacy, rng=team.random: "break_ice")
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    assert state["groups"]["1001"]["intimacy"][game_store._pair_key("u1", "u2")] == -54
    result = str(exc.value.result)
    assert "气氛似乎缓和了一点" in result
    assert "同好羁绊 +6" in result


@pytest.mark.asyncio
async def test_team_success_minor_encounter_splits_numeric_rewards(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    _stub_hunt_rng(
        monkeypatch,
        {"name": "史莱姆", "power_req": 1, "drops": []},
        minor_event="supply_cache",
    )
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    spec = rpg_events._minor_event_spec("supply_cache", team=True)
    user1 = state["groups"]["1001"]["users"]["u1"]
    user2 = state["groups"]["1001"]["users"]["u2"]
    assert user1["exp"] == rewards._challenge_exp(True, 1) + int(spec.get("exp", 0)) // 2
    assert user2["exp"] == rewards._challenge_exp(True, 1) + int(spec.get("exp", 0)) // 2
    assert user1["points"] == rewards._challenge_points(True, user1) + int(spec.get("points", 0)) // 2
    assert user2["points"] == rewards._challenge_points(True, user2) + int(spec.get("points", 0)) // 2
    result = str(exc.value.result)
    assert "两人在路边翻出一袋还没被雨淋透的补给" in result
    assert f"经验 +{int(spec.get('exp', 0)) // 2}" in result
    assert f"积分 +{int(spec.get('points', 0)) // 2}" in result


@pytest.mark.asyncio
async def test_team_success_minor_encounter_item_rewards_duplicate(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    _stub_hunt_rng(
        monkeypatch,
        {"name": "史莱姆", "power_req": 1, "drops": []},
        minor_event="worn_chest",
        minor_reward={"type": "item", "name": "彰冬无料券", "amount": 1},
    )
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng=rpg_events.random: "")

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    user1 = state["groups"]["1001"]["users"]["u1"]
    user2 = state["groups"]["1001"]["users"]["u2"]
    assert user1["inventory"]["彰冬无料券"] == 1
    assert user2["inventory"]["彰冬无料券"] == 1
    result = str(exc.value.result)
    assert "小箱子" in result
    assert "彰冬无料券 ×1" in result


@pytest.mark.asyncio
async def test_team_fail_fallback_solo_does_not_trigger_minor_encounter(monkeypatch):
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)}}}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.999))
    monkeypatch.setattr(team, "_roll_fail_flavor", lambda rng=team.random: "hesitate")
    monkeypatch.setattr(team, "_roll_team_fail_rescue", lambda rng=team.random: False)
    _stub_hunt_rng(
        monkeypatch,
        {"name": "史莱姆", "power_req": 1, "drops": []},
        minor_event="supply_cache",
    )

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == rewards._challenge_exp(True, 1)
    assert user["points"] == rewards._challenge_points(True, user)
    assert "【奇遇】" not in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_appends_world_boss_spawn_lines(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
    }}}
    _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(team, "_maybe_spawn_world_boss_lines", lambda *args, **kwargs: ["世界BOSS出现"])

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    assert "世界BOSS出现" in str(exc.value.result)

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.boss as boss
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.hunt as hunt
import nonebot_plugin_akito.features.rpg.player as player
import nonebot_plugin_akito.features.rpg.team as team

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
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "focus_fire")
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

    def _pick(level, rng=hunt.random):
        captured["level"] = level
        return monster, False

    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}
    monkeypatch.setattr(hunt, "_pick_encounter", _pick)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        hunt,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        {"win": True, "effective": int(combat_power * power_factor * fortune_factor),
         "event": event or "", "monster": eff_monster},
    )
    monkeypatch.setattr(hunt, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    hunt._settle_coop(
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

    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, False))
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(hunt, "_fortune_combat_factor", lambda user, today: 1.4 if user is left else 0.8)
    monkeypatch.setattr(
        hunt,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"fortune_factor": fortune_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )
    monkeypatch.setattr(hunt, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    hunt._settle_coop(left, right, "D")

    assert captured["fortune_factor"] == pytest.approx(1.1)


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

    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, False))
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: event_key)
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        hunt,
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

    monkeypatch.setattr(hunt, "_apply_rewards", _apply_rewards)

    hunt._settle_coop(_equipped_user(), _equipped_user(), "D", exp_bonus=0.15, drop_bonus=0.25)

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

    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, False))
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        hunt,
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

    monkeypatch.setattr(hunt, "_apply_rewards", _apply_rewards)

    hunt._settle_coop(
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
    assert g["u1"]["exp"] == hunt._challenge_exp(True, 1)
    assert g["u1"]["points"] == hunt._challenge_points(True, g["u1"])
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
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")

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
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")

    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))

    assert state["groups"]["1001"]["intimacy"][game_store._pair_key("u1", "u2")] == -54
    result = str(exc.value.result)
    assert "气氛似乎缓和了一点" in result
    assert "同好羁绊 +6" in result


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

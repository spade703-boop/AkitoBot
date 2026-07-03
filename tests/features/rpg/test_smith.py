from __future__ import annotations

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.player as player
import nonebot_plugin_akito.features.rpg.smith as smith

from .helpers import _equipped_user, _FixedRand, _patch_io, _world_boss_record


def test_forge_guards_and_success():
    today = "D"
    fcfg = rpg_config._cfg("forge", {})
    first_cost, mx = smith._forge_cost(fcfg, 0), int(fcfg["max_per_day"])
    ok, msg = smith._forge({"equip_date": ""}, today)
    assert ok is False and "还没领装备" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": True}, today)
    assert ok is False and "损坏" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 0}, today)
    assert ok is False and "积分不够" in msg
    u = {"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 1000}
    ok, msg = smith._forge(u, today)
    assert ok and u["equip_forge"] == 1 and u["points"] == 1000 - first_cost
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": mx, "points": 10 ** 9}, today)
    assert ok is False and "上限" in msg


def test_forge_costs_list_and_linear_fallback(monkeypatch):
    orig_cfg = smith._cfg
    monkeypatch.setattr(
        smith,
        "_cfg",
        lambda key, default=None: {"cost_base": 100, "costs": [30, 60, 90], "max_per_day": 3, "step": 6}
        if key == "forge" else orig_cfg(key, default),
    )
    u = {"equip_date": "D", "equip_used": False, "equip_forge": 0, "points": 1000}
    assert smith._forge(u, "D")[0] is True and u["points"] == 970
    assert smith._forge(u, "D")[0] is True and u["points"] == 910
    assert smith._forge(u, "D")[0] is True and u["points"] == 820

    monkeypatch.setattr(
        smith,
        "_cfg",
        lambda key, default=None: {"cost_base": 80, "max_per_day": 5, "step": 6}
        if key == "forge" else orig_cfg(key, default),
    )
    u2 = {"equip_date": "D", "equip_used": False, "equip_forge": 0, "points": 1000}
    assert smith._forge(u2, "D")[0] is True and u2["points"] == 920


@pytest.mark.asyncio
async def test_forge_cmd_deducts_points(monkeypatch):
    base = smith._forge_cost(rpg_config._cfg("forge", {}), 0)
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(points=1000)}}}})
    with pytest.raises(FinishedException) as exc:
        await smith.forge_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["equip_forge"] == 1 and user["points"] == 1000 - base
    assert "强化" in str(exc.value.result)


@pytest.mark.asyncio
async def test_forge_cmd_superuser_bypasses_sleep(monkeypatch):
    base = smith._forge_cost(rpg_config._cfg("forge", {}), 0)
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {"users": {
        smith.SUPERUSER_QQ: _equipped_user(points=1000)}}}})
    monkeypatch.setattr(smith, "is_sleeping", lambda: True)

    with pytest.raises(FinishedException) as exc:
        await smith.forge_cmd.handlers[0](Event(group_id=1001, user_id=smith.SUPERUSER_QQ))

    user = state["groups"]["1001"]["users"][smith.SUPERUSER_QQ]
    assert user["equip_forge"] == 1 and user["points"] == 1000 - base
    assert "强化" in str(exc.value.result)


@pytest.mark.asyncio
async def test_boss_forge_cmd_uses_independent_boss_equipment(monkeypatch):
    base = smith._forge_cost(rpg_config._cfg("forge", {}), 0)
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {
        "users": {"u1": _equipped_user(points=1000, equip_forge=3)},
        "rpg": {"world_boss": _world_boss_record()},
    }}})
    monkeypatch.setattr(smith.random, "randint", lambda _a, _b: 0)

    with pytest.raises(FinishedException) as exc:
        await smith.boss_forge_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    participant = state["groups"]["1001"]["rpg"]["world_boss"]["participants"]["u1"]
    assert user["equip_forge"] == 3
    assert participant["equip_forge"] == 1
    assert user["points"] == 1000 - base
    assert "BOSS" in str(exc.value.result)


def test_rebuy_equip_success(monkeypatch):
    from nonebot_plugin_akito.features.rpg import smith as _s
    cf = {
        "equip": {
            "base": 10,
            "per_level": 5,
            "var": 6,
            "rebuy_cost": 100,
            "rebuy_points_mult": 0.5,
            "rebuy_exp_mult": 0.5,
        }
    }
    orig = _s._cfg
    monkeypatch.setattr(_s, "_cfg", lambda key, default=None: cf.get(key, orig(key, default)))
    u = {"equip_date": "D", "equip_used": True, "points": 300, "equip_forge": 2, "equip_rebought": False}
    ok, msg = _s._rebuy_equip(u, "D")
    assert ok
    assert u["equip_used"] is False
    assert u["equip_rebought"] is True
    assert u["equip_forge"] == 0
    assert u["points"] == 200


def test_rebuy_equip_rejects():
    from nonebot_plugin_akito.features.rpg import smith as _s
    ok, msg = _s._rebuy_equip({}, "D")
    assert ok is False and "没签到" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": False}, "D")
    assert ok is False and "还好好的" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": True, "points": 10}, "D")
    assert ok is False and "积分不够" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": True, "points": 500, "equip_rebuy_count": 1}, "D")
    assert ok is False and "买过" in msg


def test_reset_group_rpg_equip_only_refreshes_signed_users():
    group = game_store._new_group()
    lv3 = player._cum_exp(3, player._level_base())
    group["users"]["u1"] = {
        "exp": lv3,
        "fortune": "daji",
        "fortune_date": "2026-06-22",
        "signin_last_date": "2026-06-22",
        "equip_date": "2026-06-22",
        "equip_used": True,
        "equip_forge": 2,
        "equip_rebought": True,
        "equip_rebuy_count": 1,
        "equip_roll": 0,
    }
    group["users"]["u2"] = {"exp": 0, "fortune_date": "", "signin_last_date": ""}

    reset = smith._reset_group_rpg_equip(group, "2026-06-22", _FixedRand(4))

    u1 = group["users"]["u1"]
    u2 = group["users"]["u2"]
    assert reset == 1
    assert u1["equip_date"] == "2026-06-22"
    assert u1["equip_used"] is False
    assert u1["equip_forge"] == 0
    assert u1["equip_rebought"] is False
    assert u1["equip_rebuy_count"] == 0
    assert u1["equip_roll"] == 4
    assert u1["equip_level"] == 3
    assert u1["fortune"] == "daji" and u1["fortune_date"] == "2026-06-22"
    assert u2.get("equip_date", "") == ""


@pytest.mark.asyncio
async def test_reset_rpg_cmd_only_regrants_equips_for_signed_users(monkeypatch):
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {"users": {
        "u1": {
            "exp": 0,
            "fortune": "ping",
            "fortune_date": "2026-06-22",
            "signin_last_date": "2026-06-22",
            "equip_date": "2026-06-22",
            "equip_used": True,
            "equip_forge": 2,
        },
        "u2": {"exp": 0},
    }}}})
    monkeypatch.setattr(smith, "random", _FixedRand(2))

    with pytest.raises(FinishedException) as exc:
        await smith.reset_rpg_cmd.handlers[0](Event(group_id=1001, user_id=smith.SUPERUSER_QQ))

    u1 = state["groups"]["1001"]["users"]["u1"]
    u2 = state["groups"]["1001"]["users"]["u2"]
    assert u1["equip_used"] is False and u1["equip_forge"] == 0 and u1["equip_roll"] == 2
    assert u2.get("equip_date", "") == ""
    assert "今天签到过的 1 人" in str(exc.value.result)

from __future__ import annotations

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift

from .helpers import _at, _bot, _patch_runtime, _steal_group


def test_steal_outcome_in_weights():
    keys = set(gift._steal_cfg()["weights"])
    assert keys == {"success", "minor_success", "caught", "whiff", "reversal"}
    assert gift._steal_outcome() in keys


def test_settle_steal_success_capped_and_moves_points():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=0, victim_pts=1000)
    out = gift._settle_steal(group, "T", "V", "success")
    amt = min(int(1000 * cfg["ratio"]), cfg["cap"], 1000)
    assert out["amount"] == amt == cfg["cap"]  # 封顶生效
    assert gift._get_user(group, "T")["points"] == amt
    assert gift._get_user(group, "V")["points"] == 1000 - amt


def test_settle_steal_minor_success_moves_small_points():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=0, victim_pts=500)
    out = gift._settle_steal(group, "T", "V", "minor_success")
    amt = max(int(cfg["minor_min_amount"]), int(500 * float(cfg["minor_ratio"])))
    amt = min(amt, int(cfg["minor_cap"]), 500)
    assert out["amount"] == amt == cfg["minor_cap"]
    assert gift._get_user(group, "T")["points"] == amt
    assert gift._get_user(group, "V")["points"] == 500 - amt


def test_settle_steal_caught_pays_victim():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "caught")
    assert out["amount"] == cfg["caught_penalty"]
    assert gift._get_user(group, "T")["points"] == 100 - cfg["caught_penalty"]
    assert gift._get_user(group, "V")["points"] == 200 + cfg["caught_penalty"]


def test_settle_steal_reversal_pays_victim():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "reversal")
    assert out["amount"] == cfg["reversal_amount"]
    assert gift._get_user(group, "T")["points"] == 100 - cfg["reversal_amount"]
    assert gift._get_user(group, "V")["points"] == 200 + cfg["reversal_amount"]


def test_settle_steal_whiff_keeps_points():
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert out["amount"] == 0
    assert gift._get_user(group, "T")["points"] == 100
    assert gift._get_user(group, "V")["points"] == 200


def test_settle_steal_success_bond_scales_with_amount():
    group_small = _steal_group(victim_pts=80, bond=0)
    out_small = gift._settle_steal(group_small, "T", "V", "success")
    group_big = _steal_group(victim_pts=1000, bond=0)
    out_big = gift._settle_steal(group_big, "T", "V", "success")
    assert out_big["amount"] > out_small["amount"]
    assert out_big["bond"] > out_small["bond"]


def test_settle_steal_whiff_bond_loss_is_light():
    cfg = gift._steal_cfg()
    spec = cfg["bond_loss"]["whiff"]
    group = _steal_group(bond=1000)
    out = gift._settle_steal(group, "T", "V", "whiff")
    expected = int(spec["base"]) + min(int(1000 * float(spec["positive_bond_ratio"])), int(spec["positive_bond_cap"]))
    assert out["bond"] == expected
    assert gift._get_intimacy(group, "T", "V") == 1000 - expected


def test_settle_steal_reversal_hurts_bond_more_than_caught():
    cfg = gift._steal_cfg()
    group_caught = _steal_group(thief_pts=100, victim_pts=200, bond=200)
    out_caught = gift._settle_steal(group_caught, "T", "V", "caught")
    group_reversal = _steal_group(thief_pts=100, victim_pts=200, bond=200)
    out_reversal = gift._settle_steal(group_reversal, "T", "V", "reversal")
    assert out_caught["amount"] == cfg["caught_penalty"]
    assert out_reversal["amount"] == cfg["reversal_amount"]
    assert out_caught["amount"] > out_reversal["amount"]
    assert out_reversal["bond"] > out_caught["bond"]


def test_settle_steal_low_or_zero_amount_no_longer_drops_huge_bond():
    group = _steal_group(bond=-100)
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert out["bond"] == gift._steal_cfg()["bond_loss"]["whiff"]["base"]
    assert gift._get_intimacy(group, "T", "V") == -100 - out["bond"]


def test_settle_steal_bond_floor():
    floor = gift._steal_cfg()["bond_floor"]
    group = _steal_group(bond=floor + 3)  # 接近下限
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert gift._get_intimacy(group, "T", "V") == floor  # 封底，不再下探
    assert out["bond"] == 3  # 实际只掉到下限的幅度


@pytest.mark.asyncio
async def test_steal_cmd_success_moves_points_and_counts(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 0}, "10002": {"points": 1000},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "_steal_outcome", lambda rng=gift.random: "success")
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    cap = gift._steal_cfg()["cap"]
    assert "[at:10001]" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == cap
    assert state["groups"]["1001"]["users"]["10002"]["points"] == 1000 - cap
    assert state["groups"]["1001"]["users"]["10001"]["steal_used"] == 1
    assert state["groups"]["1001"]["users"]["10002"]["robbed_count"] == 1


@pytest.mark.asyncio
async def test_steal_cmd_rejects_self_bot_no_target(monkeypatch):
    _patch_runtime(monkeypatch, store={"groups": {"1001": {"users": {"10001": {"points": 100}}, "intimacy": {}}}})
    r1 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[]))
    assert r1 is None  # 无 @ 目标时静默忽略
    r2 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[_at("10001")]))
    assert r2 is None  # @自己时静默忽略
    r3 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[_at("114514")]))
    assert r3 is None  # @bot 时静默忽略


@pytest.mark.asyncio
async def test_steal_cmd_blocks_too_poor(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100}, "10002": {"points": 10}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "没什么好偷" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_daily_limit_blocks(monkeypatch):
    limit = gift._steal_cfg()["daily_limit"]
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100, "steal_date": "2026-06-22", "steal_used": limit},
            "10002": {"points": 500},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "手气用完" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_blocked_by_signin_protection(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100}, "10002": {"points": 500, "protect_until": 9999.0},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 1000.0)  # < protect_until → 受保护
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "偷不了" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_victim_daily_limit_blocks(monkeypatch):
    vlimit = gift._steal_cfg()["victim_daily_limit"]
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100},
            "10002": {"points": 500, "robbed_date": "2026-06-22", "robbed_count": vlimit},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "偷不了" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_superuser_bypasses_all(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            su: {"points": 0, "steal_date": "2026-06-22", "steal_used": 99},
            "10002": {"points": 1000, "protect_until": 9e9, "robbed_date": "2026-06-22", "robbed_count": 99},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "_steal_outcome", lambda rng=gift.random: "success")
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id=su, original_message=[_at("10002")])
    with pytest.raises(FinishedException):
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert state["groups"]["1001"]["users"][su]["points"] == gift._steal_cfg()["cap"]  # 照偷不误


@pytest.mark.asyncio
async def test_steal_cmd_blocked_during_sleep(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100}, "10002": {"points": 500}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "睡" in str(exc.value.result)

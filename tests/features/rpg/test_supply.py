from __future__ import annotations

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core.game_store import run_points_status_hooks
import nonebot_plugin_akito.features.rpg.supply as supply

from .helpers import _patch_io


def test_supply_points_status_checks_weekly_limit_and_next_cost():
    user = {"points": 140}
    assert "冒险补给：可开启" in supply._supply_points_status(user, "2026-06-22")
    assert "本周已开启 0/7" in supply._supply_points_status(user, "2026-06-22")

    user["points"] = 139
    assert "冒险补给：积分不足" in supply._supply_points_status(user, "2026-06-22")
    assert "本次需要 140 积分，当前 139" in supply._supply_points_status(user, "2026-06-22")

    user["points"] = 300
    user["weekly_investment"] = {"week": "2026-W26", "supply_count": 7}
    assert "本周次数已用完（已开启 7/7）" in supply._supply_points_status(user, "2026-06-22")

    user["weekly_investment"] = {"week": "2026-W25", "supply_count": 7}
    assert "冒险补给：可开启" in supply._supply_points_status(user, "2026-06-22")
    assert "本周已开启 0/7" in supply._supply_points_status(user, "2026-06-22")


def test_supply_registers_points_status_hook():
    lines = run_points_status_hooks({"points": 140}, "2026-06-22")
    assert any("冒险补给：可开启" in line for line in lines)


def test_supply_pool_gives_scallion_cake_exactly_one_percent_weight():
    pool = supply._supply_cfg()["pool"]
    weights = {entry["item"]: int(entry["weight"]) for entry in pool}

    assert sum(weights.values()) == 100
    assert weights["大葱味蛋糕"] == 1


@pytest.mark.asyncio
async def test_supply_uses_cross_group_weekly_costs_and_limit(monkeypatch):
    state = _patch_io(
        monkeypatch,
        supply,
        store={"groups": {"1001": {"users": {"u1": {"points": 2000, "exp": 0}}}}},
    )
    monkeypatch.setattr(supply, "_pick_supply_item", lambda rng=supply.random: "旅人的行囊")

    expected_costs = [140, 140, 140, 140, 140, 200, 300]
    for index, cost in enumerate(expected_costs):
        group_id = 1001 if index % 2 == 0 else 1002
        with pytest.raises(FinishedException) as exc:
            await supply.supply_cmd.handlers[0](Event(group_id=group_id, user_id="u1"), Message(""))
        result = str(exc.value.result)
        assert f"消耗 {cost} 积分" in result
        assert "获得【旅人的行囊】×1（效果：接下来2次普通个人/组队挑战：战力+10%、经验+25%" in result
        assert "普通个人挑战 / 普通组队挑战（世界BOSS不生效）" in result

    user = state["users"]["u1"]
    assert user["points"] == 800
    assert user["exp"] == 210
    assert user["inventory"]["旅人的行囊"] == 7
    assert user["weekly_investment"] == {
        "week": "2026-W26",
        "supply_count": 7,
        "supply_spent": 1200,
        "gift_spent": 0,
    }
    metric = state["groups"]["1001"]["rpg"]["metrics"]["days"]["2026-06-22"]
    assert metric["supply_opens"] == 4
    assert metric["supply_points_spent"] == 720
    other_metric = state["groups"]["1002"]["rpg"]["metrics"]["days"]["2026-06-22"]
    assert other_metric["supply_opens"] == 3
    assert other_metric["supply_points_spent"] == 480

    with pytest.raises(FinishedException) as exc:
        await supply.supply_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message(""))
    assert "已经开过 7 次" in str(exc.value.result)
    assert state["users"]["u1"]["points"] == 800


@pytest.mark.asyncio
async def test_supply_sixth_cost_rejects_without_partial_rewards(monkeypatch):
    store = {"groups": {"1001": {"users": {"u1": {
        "points": 199,
        "exp": 10,
        "weekly_investment": {
            "week": "2026-W26",
            "supply_count": 5,
            "supply_spent": 700,
            "gift_spent": 0,
        },
    }}}}}
    state = _patch_io(monkeypatch, supply, store=store)

    with pytest.raises(FinishedException) as exc:
        await supply.supply_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message(""))

    assert "第 6 次" in str(exc.value.result) and "200 积分" in str(exc.value.result)
    user = state["users"]["u1"]
    assert user["points"] == 199 and user["exp"] == 10
    assert user["weekly_investment"]["supply_count"] == 5


@pytest.mark.asyncio
async def test_supply_open_scallion_cake_shows_gift_usage(monkeypatch):
    state = _patch_io(
        monkeypatch,
        supply,
        store={"groups": {"1001": {"users": {"u1": {"points": 140, "exp": 0}}}}},
    )
    monkeypatch.setattr(supply, "_pick_supply_item", lambda rng=supply.random: "大葱味蛋糕")

    with pytest.raises(FinishedException) as exc:
        await supply.supply_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message(""))

    result = str(exc.value.result)
    user = state["groups"]["1001"]["users"]["u1"]
    assert "获得【大葱味蛋糕】×1" in result
    assert "使用 大葱味蛋糕@某人" in result
    assert user["inventory"]["大葱味蛋糕"] == 1


@pytest.mark.asyncio
async def test_supply_command_requires_exact_phrase(monkeypatch):
    _patch_io(monkeypatch, supply)
    result = await supply.supply_cmd.handlers[0](
        Event(group_id=1001, user_id="u1"),
        Message("测试"),
    )
    assert result is None

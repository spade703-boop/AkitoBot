from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features.gift import render as bond_render
import nonebot_plugin_akito.features.rpg.analytics as analytics
import nonebot_plugin_akito.features.rpg.hunt as hunt

from .helpers import _bot, _equipped_user, _patch_io, _stub_hunt_rng


def test_metrics_aggregate_battles_teams_and_world_boss():
    group = {"rpg": {}}
    analytics.record_battle(
        group,
        "2026-07-27",
        mode="team",
        user_ids=["u1", "u2"],
        outcome={"win": True, "elite": False, "monster": {"name": "史莱姆"}},
        exp_gained=120,
        points_gained=20,
    )
    analytics.record_team_attempt(group, "2026-07-27", formed=True)
    analytics.record_world_boss_spawn(group, "2026-07-27")
    analytics.record_world_boss_attack(group, "2026-07-27", user_ids=["u1", "u2"], damage=66)
    analytics.record_world_boss_settlement(
        group,
        {"date": "2026-07-27"},
        [{"exp": 40, "points": 5}, {"exp": 35, "points": 4}],
        killed=True,
    )

    summary = analytics.aggregate_metrics(group, "2026-07-27", 7)
    assert summary["battles"] == 1
    assert summary["team_battles"] == 1
    assert summary["team_formed"] == 1
    assert summary["active_players"] == 2
    assert summary["world_boss_damage"] == 66
    assert summary["world_boss_exp_gained"] == 75
    assert summary["monsters"]["史莱姆"]["wins"] == 1

    page_data = analytics.build_metrics_page_data(group, "2026-07-27")
    assert page_data["periods"][0]["win_rate"] == "100.0%"
    assert page_data["periods"][0]["team_rate"] == "100.0%"
    assert page_data["periods"][0]["boss_spawns"] == 1


def test_metrics_template_renders_periods_and_monsters():
    group = {"rpg": {}}
    for _ in range(3):
        analytics.record_battle(
            group,
            "2026-07-27",
            mode="solo",
            user_ids=["u1"],
            outcome={"win": False, "monster": {"name": "石像鬼"}},
            exp_gained=18,
            points_gained=2,
        )

    page_data = analytics.build_metrics_page_data(group, "2026-07-27")
    html = bond_render._TEMPLATE_ENV.get_template("rpg_metrics.html").render(**page_data)

    assert "RPG 运营看板" in html
    assert "近7天" in html and "近30天" in html
    assert "石像鬼" in html


@pytest.mark.asyncio
async def test_hunt_command_records_actual_reward_deltas(monkeypatch):
    state = _patch_io(
        monkeypatch,
        hunt,
        store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}},
    )
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})

    with pytest.raises(FinishedException):
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    metric = state["groups"]["1001"]["rpg"]["metrics"]["days"]["2026-06-22"]
    assert metric["solo_battles"] == 1
    assert metric["wins"] == 1
    assert metric["exp_gained"] == user["exp"]
    assert metric["points_gained"] == user["points"]


@pytest.mark.asyncio
async def test_rpg_metrics_command_is_superuser_only(monkeypatch):
    group = {"rpg": {}}
    analytics.record_battle(
        group,
        "2026-07-27",
        mode="solo",
        user_ids=["u1"],
        outcome={"win": True, "monster": {"name": "史莱姆"}},
        exp_gained=70,
        points_gained=15,
    )
    state = game_store._normalize_data({"groups": {"1001": group}})
    monkeypatch.setattr(analytics, "_today_str", lambda: "2026-07-27")
    monkeypatch.setattr(analytics, "_load_data", lambda: deepcopy(state))

    async def _fake_render(_page_data):
        return b"fake-rpg-metrics-image"

    monkeypatch.setattr(analytics, "_render_metrics_image", _fake_render)

    assert await analytics.rpg_metrics_cmd.handlers[0](Event(group_id=1001, user_id="u1")) is None
    with pytest.raises(FinishedException) as exc:
        await analytics.rpg_metrics_cmd.handlers[0](
            Event(group_id=1001, user_id=analytics.SUPERUSER_QQ)
        )

    result = str(exc.value.result)
    assert "[image]" in result
    assert "RPG运营数据" not in result


@pytest.mark.asyncio
async def test_rpg_metrics_command_falls_back_to_text(monkeypatch):
    group = {"rpg": {}}
    analytics.record_battle(
        group,
        "2026-07-27",
        mode="solo",
        user_ids=["u1"],
        outcome={"win": True, "monster": {"name": "史莱姆"}},
        exp_gained=70,
        points_gained=15,
    )
    state = game_store._normalize_data({"groups": {"1001": group}})
    monkeypatch.setattr(analytics, "_today_str", lambda: "2026-07-27")
    monkeypatch.setattr(analytics, "_load_data", lambda: deepcopy(state))

    async def _fake_render(_page_data):
        return None

    monkeypatch.setattr(analytics, "_render_metrics_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await analytics.rpg_metrics_cmd.handlers[0](
            Event(group_id=1001, user_id=analytics.SUPERUSER_QQ)
        )

    result = str(exc.value.result)
    assert "RPG运营数据" in result
    assert "普通战斗 1 场" in result

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
    analytics.record_supply_open(group, "2026-07-27", points_spent=140, exp_gained=30)
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
    assert summary["supply_opens"] == 1
    assert summary["supply_points_spent"] == 140
    assert summary["supply_exp_gained"] == 30
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
    assert "签到与成长投放" in html


def test_metrics_records_growth_events_spending_supply_and_boss_instances():
    group = {"users": {"u1": {}}, "rpg": {}}
    analytics.record_signin(
        group,
        "2026-07-27",
        user_id="u1",
        exp_gained=65,
        streak_bonus=15,
        fortune="daji",
    )
    analytics.record_battle(
        group,
        "2026-07-27",
        mode="solo",
        user_ids=["u1"],
        outcome={
            "win": True,
            "elite": True,
            "monster": {"name": "史莱姆"},
            "event": "insight",
            "support_scene": "akito_success",
            "battle_guard_triggered": True,
            "buff": {"key": "festival"},
            "drops": ["经验书"],
            "exp_buffed": True,
        },
        exp_gained=30,
        points_gained=4,
    )
    analytics.record_battle(
        group,
        "2026-07-27",
        mode="team",
        user_ids=["u1", "u2"],
        outcome={
            "win": False,
            "monster": {"name": "史莱姆"},
            "team_event": "focus_fire",
            "negative_event": "friction",
            "team_minor_event": "campfire",
            "b": {"drops": ["地图"]},
            "a": {"drops": []},
        },
        exp_gained=10,
        points_gained=2,
    )
    analytics.record_forge(group, "2026-07-27", points_spent=90, refund=20)
    analytics.record_forge(group, "2026-07-27", points_spent=30, world_boss=True)
    analytics.record_rebuy(group, "2026-07-27", points_spent=100)
    analytics.record_supply_open(
        group,
        "2026-07-27",
        points_spent=140,
        exp_gained=30,
        user_id="u1",
        items={"旅人的行囊": 1, "地图": 2},
    )

    boss = {"date": "2026-07-27", "name": "赤鳞灾龙", "max_hp": 1200}
    boss["metric_id"] = analytics.record_world_boss_spawn(group, "2026-07-27", boss=boss)
    boss["contributors"] = {"u1": 420, "u2": 180}
    analytics.record_world_boss_attack(
        group,
        "2026-07-27",
        user_ids=["u1", "u2"],
        damage=600,
        boss=boss,
    )
    analytics.record_world_boss_settlement(
        group,
        boss,
        [{"exp": 40, "points": 5}, {"exp": 30, "points": 4}],
        killed=True,
    )

    metric = group["rpg"]["metrics"]["days"]["2026-07-27"]
    assert group["users"]["u1"]["rpg_first_seen"] == "2026-07-27"
    assert metric["signins"] == 1
    assert metric["signin_exp_gained"] == 65
    assert metric["signin_streak_bonus"] == 15
    assert metric["forge_points_spent"] == 100
    assert metric["world_boss_forge_points_spent"] == 30
    assert metric["rebuy_points_spent"] == 100
    assert metric["supply_players"] == ["u1"]
    assert metric["supply_items"] == {"旅人的行囊": 1, "地图": 2}
    assert metric["drop_attempts"] == 3
    assert metric["drop_hits"] == 2
    assert metric["events"]["battle:insight"] == 1
    assert metric["events"]["team_negative:friction"] == 1
    assert metric["events"]["daily_buff:festival"] == 1
    assert metric["events"]["exp_buff"] == 1
    assert metric["world_boss_instances"][0]["participants"] == 2
    assert metric["world_boss_instances"][0]["attacks"] == 1
    assert metric["world_boss_instances"][0]["damage"] == 600
    assert metric["world_boss_instances"][0]["reward_exp"] == 70

    page_data = analytics.build_metrics_page_data(group, "2026-07-27")
    baseline = page_data["periods"][1]
    assert baseline["signin_metrics_available"] is True
    assert baseline["spending_metrics_available"] is True
    assert baseline["event_metrics_available"] is True
    assert baseline["drop_metrics_available"] is True
    assert baseline["supply_metrics_available"] is True
    assert baseline["world_boss_instance_metrics_available"] is True
    assert page_data["event_rows"]
    assert page_data["drop_rows"]
    assert page_data["supply_rows"]
    assert page_data["boss_instance_rows"][0]["status"] == "已击杀"


def test_metrics_old_entries_do_not_claim_new_instrumentation():
    group = {"rpg": {"metrics": {"days": {"2026-07-27": {"battles": 2, "wins": 1}}}}}

    page_data = analytics.build_metrics_page_data(group, "2026-07-27")
    baseline = page_data["periods"][1]
    assert baseline["signin_metrics_available"] is False
    assert baseline["spending_metrics_available"] is False
    assert baseline["event_metrics_available"] is False
    assert baseline["drop_metrics_available"] is False
    assert baseline["supply_metrics_available"] is False
    assert baseline["world_boss_instance_metrics_available"] is False
    assert page_data["event_rows"] == []
    assert page_data["drop_rows"] == []
    assert page_data["supply_rows"] == []
    assert page_data["boss_instance_rows"] == []
    html = bond_render._TEMPLATE_ENV.get_template("rpg_metrics.html").render(**page_data)
    assert "暂无历史埋点" in html


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


@pytest.mark.asyncio
async def test_style_test_command_is_superuser_only_and_uses_synthetic_data(monkeypatch):
    monkeypatch.setattr(analytics, "_today_str", lambda: "2026-07-27")

    def _unexpected_load():
        raise AssertionError("样式测试不应读取真实存储")

    monkeypatch.setattr(analytics, "_load_data", _unexpected_load)
    rendered_page = {}

    async def _fake_render(page_data):
        rendered_page.update(page_data)
        return b"fake-style-test-image"

    monkeypatch.setattr(analytics, "_render_metrics_image", _fake_render)

    assert await analytics.style_test_cmd.handlers[0](Event(group_id=1001, user_id="u1")) is None
    with pytest.raises(FinishedException) as exc:
        await analytics.style_test_cmd.handlers[0](
            Event(group_id=1001, user_id=analytics.SUPERUSER_QQ)
        )

    result = str(exc.value.result)
    assert "[image]" in result
    assert rendered_page["periods"][0]["active_players"] == 8
    assert len(rendered_page["activity_trend"]) == 7
    assert rendered_page["level_distribution"]
    assert rendered_page["event_rows"]
    assert rendered_page["drop_rows"]
    assert rendered_page["supply_rows"]
    assert rendered_page["boss_instance_rows"]


@pytest.mark.asyncio
async def test_style_test_command_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(analytics, "_today_str", lambda: "2026-07-27")

    async def _fake_render(_page_data):
        return None

    monkeypatch.setattr(analytics, "_render_metrics_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await analytics.style_test_cmd.handlers[0](
            Event(group_id=1001, user_id=analytics.SUPERUSER_QQ)
        )

    result = str(exc.value.result)
    assert "看板样式测试渲染失败" in result
    assert "RPG运营数据" in result

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features.gift import render as bond_render
import nonebot_plugin_akito.features.rpg.character as character
import nonebot_plugin_akito.features.rpg.player as player

from .helpers import _equipped_user, _patch_io, _world_boss_record


@pytest.mark.asyncio
async def test_status_panel_only_level_and_equip(monkeypatch):
    lv3 = player._cum_exp(3, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": lv3, "points": 250, "equip_date": "2026-06-22", "equip_used": False,
               "equip_forge": 1, "inventory": {"经验书": 2}}}}}})
    monkeypatch.setattr(character, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert "Lv3" in r and "今日装备" in r and "已强化" in r and "250" in r
    assert "战力" not in r  # 战力隐藏，不外显


@pytest.mark.asyncio
async def test_status_panel_still_available_while_sleeping(monkeypatch):
    lv2 = player._cum_exp(2, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": lv2, "equip_date": "2026-06-22", "equip_used": False}
    }}}})
    monkeypatch.setattr(character, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(character, "is_sleeping", lambda: True, raising=False)
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    result = str(exc.value.result)
    assert "角色档案" in result
    assert "睡" not in result


@pytest.mark.asyncio
async def test_help_cmd_renders_image(monkeypatch):
    async def _fake_render():
        return b"fake-rpg-help-image"

    monkeypatch.setattr(character, "_render_help_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await character.help_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "[image]" in result
    assert "今日打怪" not in result


@pytest.mark.asyncio
async def test_help_cmd_falls_back_to_text(monkeypatch):
    async def _fake_render():
        return None

    monkeypatch.setattr(character, "_render_help_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await character.help_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "冒险系统" in result
    assert "今日打怪" in result
    assert "开启冒险补给" in result
    assert "　· 战后小奇遇" in result
    assert "　· 小奇遇奖励" in result
    assert "　· 战备列表" in result
    assert "　——旅人的行囊（35%）" in result
    assert "　——神官的护符（12%）" in result
    assert "　——补给袋：提供少量经验与积分。" in result
    assert "　——破旧积分卡：使用后获得 5 积分。" in result
    assert "　——破旧经验券：使用后获得 10 经验。" in result
    assert "　——彰冬无料券：使用后可向一名群友赠送彰冬无料。" in result
    assert "常规战备共享一个槽位" in result
    assert "触发时共同结算" in result
    assert "双倍经验卡暂缓且不消耗" in result
    assert "重置RPG功能" in result
    assert "——进行一次普通个人挑战" in result
    assert "[image]" not in result


def test_help_items_cover_public_and_admin_commands():
    commands = {item["command"] for item in character._HELP_ITEMS}

    assert {
        "签到",
        "今日打怪",
        "组队@某人",
        "开启冒险补给",
        "攻击世界BOSS",
        "组队世界BOSS@某人",
        "强化世界BOSS装备",
        "我的角色",
        "群排行榜",
        "强制开启世界BOSS",
        "RPG数据",
        "重置RPG功能",
    } <= commands

    sections = {
        item["command"]: {section["title"] for section in item.get("sections", [])}
        for item in character._HELP_ITEMS
    }
    assert {"战后小奇遇", "小奇遇奖励"} <= sections["今日打怪"]
    assert {"战备使用规则", "战备列表"} <= sections["开启冒险补给"]


@pytest.mark.asyncio
async def test_rank_sorts_filters_and_formats(monkeypatch):
    lv5 = player._cum_exp(5, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": 10, "display_name": "小一", "hunt_wins": 2},
        "u2": {"exp": lv5, "display_name": "大二", "hunt_wins": 7},
        "u3": {"exp": 0, "display_name": "路人"},   # 没开始冒险（exp=0）→ 不上榜
    }}}})
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    async def _fake_render(ranked):
        assert [uid for uid, _rec in ranked] == ["u2", "u1"]
        return b"fake-rpg-rank-image"
    monkeypatch.setattr(character, "_render_rank_image", _fake_render)
    with pytest.raises(FinishedException) as exc:
        await character.rank_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert "[image]" in r
    assert "大二" not in r
    assert "路人" not in r


@pytest.mark.asyncio
async def test_rank_falls_back_to_text(monkeypatch):
    lv5 = player._cum_exp(5, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": 10, "display_name": "小一", "hunt_wins": 2},
        "u2": {"exp": lv5, "display_name": "大二", "hunt_wins": 7},
    }}}})
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    async def _fake_render(_ranked):
        return None
    monkeypatch.setattr(character, "_render_rank_image", _fake_render)

    with pytest.raises(FinishedException) as exc:
        await character.rank_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert result.index("大二") < result.index("小一")
    assert "胜7场" in result and "Lv5" in result


def test_rank_template_renders_player_progress_and_record():
    lv5 = player._cum_exp(5, player._level_base())
    ranked = [("10001", {"exp": lv5 + 50, "display_name": "测试猎手", "hunt_wins": 7, "hunt_total": 9})]
    page_data = character._rank_page_data(ranked)
    html = bond_render._TEMPLATE_ENV.get_template("rpg_rank.html").render(**page_data)
    progress = player._level_progress(lv5 + 50)

    assert "冒险者等级排行" in html
    assert "测试猎手" in html
    assert "Lv5" in html
    assert f"50/{progress['span']}" in html
    assert "7 胜 / 9 场" in html


@pytest.mark.asyncio
async def test_rank_empty(monkeypatch):
    state = game_store._normalize_data({"groups": {"1001": {"users": {}}}})
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.rank_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    assert "还没人" in str(exc.value.result)


@pytest.mark.asyncio
async def test_status_panel_shows_title_and_record(monkeypatch):
    lv3 = player._cum_exp(3, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": lv3, "equip_date": "2026-06-22", "equip_used": False,
               "hunt_total": 5, "hunt_wins": 4}}}}})
    monkeypatch.setattr(character, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert player._title_of(3) in r            # 称号
    assert "4 胜" in r and "5 场" in r          # 战绩
    assert "当前无世界BOSS" in r


@pytest.mark.asyncio
async def test_status_panel_shows_weekly_investment_and_active_supplies(monkeypatch):
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(
        weekly_investment={
            "week": "2026-W26",
            "supply_count": 3,
            "supply_spent": 420,
            "gift_spent": 200,
        },
        active_battle_supply={"name": "旅人的行囊", "uses": 2},
        active_battle_guard={"name": "神官的护符", "uses": 1},
    )}}}}
    _patch_io(monkeypatch, character, store=store)

    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "冒险补给 3/7（已花费 420 积分） / 送礼 200 积分" in result
    assert "本周倾向：偏向冒险" in result
    assert "旅人的行囊（剩余 2 场） / 神官的护符（待触发）" in result
    assert "战备范围：普通个人挑战 / 普通组队挑战（世界BOSS不生效）" in result


@pytest.mark.asyncio
async def test_status_panel_shows_world_boss_equip_status(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user()},
        "rpg": {"world_boss": _world_boss_record()},
    }}}
    state = _patch_io(monkeypatch, character, store=store)

    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "深渊巨像" in result
    assert "装备：已就绪" in result
    assert "u1" in state["groups"]["1001"]["rpg"]["world_boss"]["participants"]


@pytest.mark.asyncio
async def test_status_panel_shows_world_boss_trophies(monkeypatch):
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(world_boss_trophies=["赤鳞龙鳞", "焦香披萨块"])},
    }}}
    _patch_io(monkeypatch, character, store=store)

    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))

    result = str(exc.value.result)
    assert "世界BOSS收藏" in result
    assert "赤鳞龙鳞" in result
    assert "焦香披萨块" in result

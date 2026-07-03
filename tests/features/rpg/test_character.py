from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store
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
    assert "[image]" not in result


@pytest.mark.asyncio
async def test_rank_sorts_filters_and_formats(monkeypatch):
    lv5 = player._cum_exp(5, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": 10, "display_name": "小一", "hunt_wins": 2},
        "u2": {"exp": lv5, "display_name": "大二", "hunt_wins": 7},
        "u3": {"exp": 0, "display_name": "路人"},   # 没开始冒险（exp=0）→ 不上榜
    }}}})
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.rank_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert r.index("大二") < r.index("小一")   # 经验高的排前
    assert "路人" not in r                       # exp=0 不上榜
    assert "胜7场" in r and "Lv5" in r


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

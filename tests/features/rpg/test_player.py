from __future__ import annotations

import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.player as player

from .helpers import _FixedRand


def test_level_curve_round_trips():
    base = player._level_base()
    for lvl in range(1, 9):
        floor = player._cum_exp(lvl, base)
        assert player._level_of(floor) == lvl
        if lvl > 1:
            assert player._level_of(floor - 1) == lvl - 1


def test_level_progress_fields():
    base = player._level_base()
    exp = player._cum_exp(3, base) + 10
    prog = player._level_progress(exp)
    assert prog["level"] == 3 and prog["into"] == 10 and prog["span"] == base * 3


def test_grant_equip_sets_fields_and_power():
    ecfg = rpg_config._cfg("equip", {})
    user = {"exp": player._cum_exp(3, player._level_base())}  # 等级 3
    player._grant_equip(user, "2026-06-22", _FixedRand(0))
    assert user["equip_date"] == "2026-06-22"
    assert user["equip_level"] == 3
    assert user["equip_used"] is False and user["equip_forge"] == 0
    expected = int(ecfg["base"]) + 3 * int(ecfg["per_level"]) + 0
    assert player._equip_power(user) == expected == player._combat_power(user)


def test_equip_power_includes_roll_and_forge():
    ecfg = rpg_config._cfg("equip", {})
    fcfg = rpg_config._cfg("forge", {})
    user = {"equip_level": 2, "equip_roll": 3, "equip_forge": 2}
    expected = int(ecfg["base"]) + 2 * int(ecfg["per_level"]) + 3 + 2 * int(fcfg["step"])
    assert player._equip_power(user) == expected


def test_equip_intact_consume_status():
    assert player._equip_intact({"equip_date": "D", "equip_used": False}, "D") is True
    assert player._equip_intact({"equip_date": "D", "equip_used": True}, "D") is False
    assert player._equip_intact({"equip_date": "X"}, "D") is False
    u = {"equip_date": "D", "equip_used": False}
    player._consume_equip(u)
    assert u["equip_used"] is True
    assert "未签到" in player._equip_status({"equip_date": ""}, "D")
    assert "已损坏" in player._equip_status({"equip_date": "D", "equip_used": True}, "D")
    s = player._equip_status({"equip_date": "D", "equip_used": False, "equip_forge": 2}, "D")
    assert "已强化" in s and "2" in s


def test_title_of_brackets():
    titles = rpg_config._cfg("titles", [])
    assert player._title_of(titles[0]["min_level"]) == titles[0]["name"]   # 最低档
    assert player._title_of(int(titles[1]["min_level"])) == titles[1]["name"]
    assert player._title_of(int(titles[1]["min_level"]) - 1) == titles[0]["name"]  # 未达则取低档
    assert player._title_of(10 ** 6) == titles[-1]["name"]                  # 顶档

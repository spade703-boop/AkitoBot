from __future__ import annotations

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.fortune as fortune

from .helpers import _FixedRand


def test_roll_fortune_pity_and_daxiong(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(fortune, "_weighted_choice", lambda w, r: (captured.update(w) or "ping"))
    fcfg = rpg_config._cfg("fortune", {})
    base_w = {lv["key"]: int(lv["weight"]) for lv in fcfg["levels"]}
    # 连签保底：吉以上 +boost
    fortune._roll_fortune({"no_lucky_streak": int(fcfg["lucky_pity_days"]), "last_fortune": "ping"})
    for k in fcfg["lucky_keys"]:
        assert captured[k] == base_w[k] + int(fcfg["lucky_pity_boost"])
    # 昨日大凶 → 今日大吉 +boost
    captured.clear()
    fortune._roll_fortune({"no_lucky_streak": 0, "last_fortune": fcfg["daxiong_key"]})
    dk = fcfg["daji_key"]
    assert captured[dk] == base_w[dk] + int(fcfg["daji_after_daxiong_boost"])


def test_on_signin_grants_exp_equip_fortune(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda u, rng: "daji")
    group = game_store._new_group()
    line = fortune.on_signin(group, "u1", _FixedRand(0))
    user = group["users"]["u1"]
    scfg = rpg_config._cfg("signin_streak", {})
    day1_bonus = min(0 * int(scfg["per_day"]), int(scfg["cap"]))  # 首签只吃基础签到经验
    assert user["exp"] == int(rpg_config._cfg("signin", {})["exp"]) + day1_bonus
    assert user["signin_streak"] == 1 and user["signin_last_date"] == "2026-06-22"
    assert user["fortune"] == "daji" and user["fortune_date"] == "2026-06-22"  # 运势暗掷
    assert user["equip_date"] == "2026-06-22" and user["equip_used"] is False  # 发今日装备
    if day1_bonus > 0:
        assert "经验" in line and "Lv" in line and "连签" in line
    else:
        assert "经验" in line and "Lv" in line and "连签" not in line
    assert "大吉" not in line  # 运势不外显


def test_on_signin_idempotent_same_day(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda u, rng: "ji")
    group = game_store._new_group()
    fortune.on_signin(group, "u1")
    exp1 = group["users"]["u1"]["exp"]
    assert fortune.on_signin(group, "u1") == ""
    assert group["users"]["u1"]["exp"] == exp1


def test_on_signin_registered_in_store():
    assert fortune.on_signin in game_store.SIGNIN_HOOKS


def test_signin_streak_increment_and_reset(monkeypatch):
    monkeypatch.setattr(fortune, "_yesterday_str", lambda: "2026-06-21")
    u = {"signin_streak": 3, "signin_last_date": "2026-06-21"}      # 昨天签过 → +1
    assert fortune._bump_streak(u, "2026-06-22") == 4 and u["signin_last_date"] == "2026-06-22"
    assert fortune._bump_streak({"signin_streak": 9, "signin_last_date": "2026-06-19"}, "2026-06-22") == 1  # 断签重置
    assert fortune._bump_streak({}, "2026-06-22") == 1             # 全新用户


def test_signin_streak_bonus_scales(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_yesterday_str", lambda: "2026-06-21")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda u, rng: "ping")
    scfg = rpg_config._cfg("signin_streak", {})
    group = game_store._new_group()
    group["users"]["u1"] = {"signin_streak": 4, "signin_last_date": "2026-06-21"}  # 今天应到第 5 天
    fortune.on_signin(group, "u1", _FixedRand(0))
    u = group["users"]["u1"]
    assert u["signin_streak"] == 5
    bonus = min(4 * int(scfg["per_day"]), int(scfg["cap"]))
    assert u["exp"] == int(rpg_config._cfg("signin", {})["exp"]) + bonus


def test_default_signin_background_exp_has_visible_floor():
    assert int(rpg_config._cfg("signin", {})["exp"]) == 10
    scfg = rpg_config._cfg("signin_streak", {})
    assert int(scfg["per_day"]) == 2
    assert int(scfg["cap"]) == 10

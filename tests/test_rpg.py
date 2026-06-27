"""测试 rpg 子包：运势 / 打野 / 角色面板。

数值断言一律从配置读（rpg_config._cfg(...) / player._level_base() 等），调数值不会让测试变脆。
存储与 gift 共享 core.game_store，因此打野/签到写入的就是同一份玩家库。
"""

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store

# 导入子包即触发命令注册与签到钩子注册（rpg/__init__ 内 from . import character, fortune, hunt）
import nonebot_plugin_akito.features.rpg.character as character
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.fortune as fortune
import nonebot_plugin_akito.features.rpg.hunt as hunt
import nonebot_plugin_akito.features.rpg.player as player


def _bot():
    return Bot(self_id="114514")


# ==================== 纯逻辑：等级 / 战力 ====================

def test_level_curve_round_trips_thresholds():
    base = player._level_base()
    for lvl in range(1, 9):
        floor = player._cum_exp(lvl, base)
        assert player._level_of(floor) == lvl              # 恰好踩门槛进该级
        if lvl > 1:
            assert player._level_of(floor - 1) == lvl - 1  # 差 1 分还在上一级


def test_level_progress_fields():
    base = player._level_base()
    exp = player._cum_exp(3, base) + 10
    prog = player._level_progress(exp)
    assert prog["level"] == 3
    assert prog["into"] == 10
    assert prog["span"] == base * 3                # cum(4)-cum(3) = base*level
    assert prog["to_next"] == prog["span"] - 10


def test_combat_power_derived_from_exp():
    p = rpg_config._cfg("power", {})
    base_p, per = int(p["base_power"]), int(p["power_per_level"])
    assert player._combat_power({"exp": 0}) == base_p + 1 * per          # Lv1
    lv5_exp = player._cum_exp(5, player._level_base())
    assert player._combat_power({"exp": lv5_exp}) == base_p + 5 * per    # Lv5
    assert player._power_for_level(3) == base_p + 3 * per


# ==================== 纯逻辑：精力 ====================

def test_refill_stamina_daily_reset():
    mx = player._stamina_max()
    user = {"stamina": 5, "stamina_date": "2026-06-20"}
    player._refill_stamina(user, "2026-06-21")
    assert user["stamina"] == mx and user["stamina_date"] == "2026-06-21"


def test_refill_stamina_same_day_noop():
    user = {"stamina": 5, "stamina_date": "2026-06-21"}
    player._refill_stamina(user, "2026-06-21")
    assert user["stamina"] == 5


# ==================== 纯逻辑：运势抽取与修正 ====================

def test_roll_fortune_pity_boosts_lucky_keys(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(fortune, "_weighted_choice", lambda w, r: (captured.update(w) or "ping"))
    fcfg = rpg_config._cfg("fortune", {})
    base_w = {lv["key"]: int(lv["weight"]) for lv in fcfg["levels"]}
    user = {"no_lucky_streak": int(fcfg["lucky_pity_days"]), "last_fortune": "ping"}
    fortune._roll_fortune(user)
    boost = int(fcfg["lucky_pity_boost"])
    for k in fcfg["lucky_keys"]:
        assert captured[k] == base_w[k] + boost


def test_roll_fortune_daxiong_boosts_daji(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(fortune, "_weighted_choice", lambda w, r: (captured.update(w) or "ping"))
    fcfg = rpg_config._cfg("fortune", {})
    base_w = {lv["key"]: int(lv["weight"]) for lv in fcfg["levels"]}
    user = {"no_lucky_streak": 0, "last_fortune": fcfg["daxiong_key"]}
    fortune._roll_fortune(user)
    dk = fcfg["daji_key"]
    assert captured[dk] == base_w[dk] + int(fcfg["daji_after_daxiong_boost"])


# ==================== 签到钩子：on_signin ====================

def test_on_signin_registered_in_store():
    assert fortune.on_signin in game_store.SIGNIN_HOOKS


def test_on_signin_grants_fortune_and_exp(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda user, rng: "daji")
    group = game_store._new_group()
    line = fortune.on_signin(group, "u1")
    user = group["users"]["u1"]
    base = int(rpg_config._cfg("fortune", {})["signin_exp_base"])
    assert user["exp"] == base * 3                       # 大吉 exp_mult=3
    assert user["fortune"] == "daji" and user["fortune_date"] == "2026-06-22"
    assert user["no_lucky_streak"] == 0                  # 大吉属「吉以上」→ 清零
    assert "大吉" in line


def test_on_signin_idempotent_same_day(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda user, rng: "ji")
    group = game_store._new_group()
    fortune.on_signin(group, "u1")
    exp1 = group["users"]["u1"]["exp"]
    assert fortune.on_signin(group, "u1") == ""          # 当天再签：空串
    assert group["users"]["u1"]["exp"] == exp1           # 不重复发放


def test_on_signin_daxiong_zero_exp_and_streak(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda user, rng: "daxiong")
    group = game_store._new_group()
    line = fortune.on_signin(group, "u1")
    assert group["users"]["u1"]["exp"] == 0              # 大凶 exp_mult=0
    assert group["users"]["u1"]["no_lucky_streak"] == 1  # 非「吉以上」→ 累加
    assert "大凶" in line


# ==================== 纯逻辑：打野结算 ====================

def _slime():
    return {"name": "史莱姆", "level": 1, "power_req": 10, "exp": 50, "points": 20, "weight": 1}


def test_resolve_hunt_win_grants_exp_and_points():
    out = hunt.resolve_hunt(20, _slime(), power_factor=1.0)  # 20 ≥ 10 → 胜
    assert out["win"] and out["exp_gain"] == 50 and out["points_gain"] == 20


def test_resolve_hunt_lose_gives_consolation_exp_no_points():
    goblin = {"name": "哥布林", "level": 3, "power_req": 30, "exp": 120, "points": 50}
    out = hunt.resolve_hunt(15, goblin, power_factor=1.0)    # 15 < 30 → 败
    ratio = float(rpg_config._cfg("combat", {})["lose_exp_ratio"])
    assert not out["win"] and out["exp_gain"] == int(120 * ratio) and out["points_gain"] == 0


def test_resolve_hunt_insight_boosts_win_exp():
    out = hunt.resolve_hunt(20, _slime(), power_factor=1.0, event="insight")
    mult = float(rpg_config._cfg("combat", {})["events"]["insight"]["exp_mult"])
    assert out["win"] and out["exp_gain"] == int(50 * mult)


def test_resolve_hunt_slip_can_flip_win_to_lose():
    m = {"name": "哥布林", "level": 3, "power_req": 18, "exp": 1, "points": 1}
    assert hunt.resolve_hunt(20, m, power_factor=1.0)["win"]                       # 20 ≥ 18 胜
    assert not hunt.resolve_hunt(20, m, power_factor=1.0, event="slip")["win"]     # ×0.75 → 15 < 18 败


def test_resolve_hunt_desperate_can_flip_lose_to_win():
    m = {"name": "食人魔", "level": 8, "power_req": 28, "exp": 1, "points": 1}
    assert not hunt.resolve_hunt(20, m, power_factor=1.0)["win"]                   # 20 < 28 败
    assert hunt.resolve_hunt(20, m, power_factor=1.0, event="desperate")["win"]    # ×1.6 → 32 ≥ 28 胜


def test_roll_hunt_event_brackets(monkeypatch):
    # 桩：从候选里挑第一个非空 key（即该档专属事件），绕开权重随机
    monkeypatch.setattr(hunt, "_weighted_choice", lambda cands, rng: next((k for k in cands if k), ""))
    ccfg = rpg_config._cfg("combat", {})
    crush, weak = float(ccfg["crush_margin"]), float(ccfg["weak_margin"])
    assert hunt._roll_hunt_event(crush) == "insight"          # 碾压
    assert hunt._roll_hunt_event(weak - 0.01) == "desperate"  # 劣势
    assert hunt._roll_hunt_event((crush + weak) / 2) == "slip"  # 均势


# ==================== 指令：打野 ====================

def _patch_hunt(monkeypatch, *, today="2026-06-22", store=None):
    state = game_store._normalize_data(store or {})
    monkeypatch.setattr(hunt, "_today_str", lambda: today)
    monkeypatch.setattr(hunt, "is_sleeping", lambda: False)

    def _load():
        return deepcopy(state)

    def _save(data):
        state.clear()
        state.update(deepcopy(game_store._normalize_data(data)))

    monkeypatch.setattr(hunt, "_load_data", _load)
    monkeypatch.setattr(hunt, "_save_data", _save)
    return state


@pytest.mark.asyncio
async def test_hunt_cmd_happy_path_consumes_stamina_and_rewards(monkeypatch):
    state = _patch_hunt(monkeypatch, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 100, "stamina_date": "2026-06-22"},
    }}}})
    monkeypatch.setattr(hunt, "_pick_monster", lambda rng=hunt.random: _slime())
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "")
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)

    event = Event(group_id=1001, user_id="u1")
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), event)

    result = str(exc.value.result)
    assert "史莱姆" in result and "[at:u1]" in result
    cost = player._stamina_cost()
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["stamina"] == 100 - cost
    assert user["exp"] == 50           # Lv1 战力 15 ≥ 史莱姆 10 → 胜，+50 exp
    assert user["points"] == 20        # +20 积分写入同一存储


@pytest.mark.asyncio
async def test_hunt_cmd_blocked_when_no_stamina(monkeypatch):
    _patch_hunt(monkeypatch, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 5, "stamina_date": "2026-06-22"},
    }}}})
    event = Event(group_id=1001, user_id="u1")
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), event)
    assert "精力不够" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_cmd_refills_stamina_next_day(monkeypatch):
    # 跨天：昨天精力耗尽，今天应自动回满后可打
    state = _patch_hunt(monkeypatch, today="2026-06-23", store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 0, "stamina_date": "2026-06-22"},
    }}}})
    monkeypatch.setattr(hunt, "_pick_monster", lambda rng=hunt.random: _slime())
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "")
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)

    event = Event(group_id=1001, user_id="u1")
    with pytest.raises(FinishedException):
        await hunt.hunt_cmd.handlers[0](_bot(), event)
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["stamina"] == player._stamina_max() - player._stamina_cost()
    assert user["stamina_date"] == "2026-06-23"


@pytest.mark.asyncio
async def test_hunt_cmd_rejects_private_chat(monkeypatch):
    _patch_hunt(monkeypatch)
    event = Event(user_id="u1")  # 无 group_id → 私聊
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), event)
    assert "群里" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_cmd_blocked_during_sleep(monkeypatch):
    _patch_hunt(monkeypatch, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 100, "stamina_date": "2026-06-22"},
    }}}})
    monkeypatch.setattr(hunt, "is_sleeping", lambda: True)
    event = Event(group_id=1001, user_id="u1")
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), event)
    assert "睡" in str(exc.value.result)


# ==================== 指令：我的角色 ====================

@pytest.mark.asyncio
async def test_status_cmd_renders_panel(monkeypatch):
    monkeypatch.setattr(character, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(character, "is_sleeping", lambda: False)
    lv3_exp = player._cum_exp(3, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": lv3_exp, "points": 250, "stamina": 80, "stamina_date": "2026-06-22",
               "fortune": "daji", "fortune_date": "2026-06-22"},
    }}}})
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))

    event = Event(group_id=1001, user_id="u1")
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](event)
    result = str(exc.value.result)
    assert "角色面板" in result
    assert "Lv3" in result
    assert "战力" in result
    assert "大吉" in result          # 今日运势
    assert "250" in result           # 积分（与送礼共享）

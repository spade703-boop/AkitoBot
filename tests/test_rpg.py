"""测试 rpg 子包（精简版）：签到领装备 → 选择打怪。

角色对外只有等级；战力是今日装备隐藏值；运势隐藏（影响打怪）；积分出口只有「强化」。
数值断言一律从配置读，调数值不会让测试变脆。存储与 gift 共享 core.game_store。
"""

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store

# 导入子包即触发命令注册与签到钩子注册
import nonebot_plugin_akito.features.rpg.character as character
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.fortune as fortune
import nonebot_plugin_akito.features.rpg.hunt as hunt
import nonebot_plugin_akito.features.rpg.inventory as inventory
import nonebot_plugin_akito.features.rpg.player as player
import nonebot_plugin_akito.features.rpg.smith as smith


def _bot():
    return Bot(self_id="114514")


class _FixedRand:
    def __init__(self, val=0):
        self.val = val

    def randint(self, _a, _b):
        return self.val


# ==================== 纯逻辑：等级 ====================

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


# ==================== 纯逻辑：今日装备 ====================

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


# ==================== 纯逻辑：隐藏运势 ====================

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


# ==================== 签到钩子 ====================

def test_on_signin_grants_exp_equip_fortune(monkeypatch):
    monkeypatch.setattr(fortune, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(fortune, "_roll_fortune", lambda u, rng: "daji")
    group = game_store._new_group()
    line = fortune.on_signin(group, "u1", _FixedRand(0))
    user = group["users"]["u1"]
    assert user["exp"] == int(rpg_config._cfg("signin", {})["exp"])
    assert user["fortune"] == "daji" and user["fortune_date"] == "2026-06-22"  # 运势暗掷
    assert user["equip_date"] == "2026-06-22" and user["equip_used"] is False  # 发今日装备
    assert "经验" in line and "Lv" in line
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


# ==================== 纯逻辑：打怪胜负 / 经验 / 事件 ====================

def test_resolve_hunt_win_lose():
    m = {"name": "史", "power_req": 20}
    assert hunt.resolve_hunt(25, m, power_factor=1.0)["win"] is True
    assert hunt.resolve_hunt(15, m, power_factor=1.0)["win"] is False


def test_resolve_hunt_slip_and_desperate_flip():
    m = {"name": "怪", "power_req": 18}
    assert hunt.resolve_hunt(20, m, power_factor=1.0)["win"] is True
    assert hunt.resolve_hunt(20, m, power_factor=1.0, event="slip")["win"] is False  # ×0.75=15<18
    big = {"name": "强怪", "power_req": 28}
    assert hunt.resolve_hunt(20, big, power_factor=1.0)["win"] is False
    assert hunt.resolve_hunt(20, big, power_factor=1.0, event="desperate")["win"] is True  # ×1.6=32≥28


def test_challenge_exp_scales_with_level():
    c = rpg_config._cfg("challenge", {})
    assert hunt._challenge_exp(True, 3) == int(c["win_exp_base"]) + 3 * int(c["win_exp_per_level"])
    assert hunt._challenge_exp(False, 3) == int(c["lose_exp_base"]) + 3 * int(c["lose_exp_per_level"])


def test_roll_hunt_event_brackets(monkeypatch):
    monkeypatch.setattr(hunt, "_weighted_choice", lambda cands, rng: next((k for k in cands if k), ""))
    ccfg = rpg_config._cfg("combat", {})
    crush, weak = float(ccfg["crush_margin"]), float(ccfg["weak_margin"])
    assert hunt._roll_hunt_event(crush) == "insight"
    assert hunt._roll_hunt_event(weak - 0.01) == "desperate"
    assert hunt._roll_hunt_event((crush + weak) / 2) == "slip"


def test_fortune_combat_and_drop_factors():
    lv = fortune._fortune_by_key("daji")
    signed = {"fortune": "daji", "fortune_date": "D"}
    assert hunt._fortune_combat_factor(signed, "D") == float(lv["combat_factor"])
    assert hunt._fortune_drop_factor(signed, "D") == float(lv["drop_factor"])
    assert hunt._fortune_combat_factor({"fortune": "daji", "fortune_date": "X"}, "D") == 1.0
    assert hunt._fortune_drop_factor({"fortune_date": "X"}, "D") == 1.0


# ==================== 纯逻辑：背包 / 掉落 / 道具 ====================

class _SeqRandom:
    def __init__(self, vals):
        self.vals = list(vals)

    def random(self):
        return self.vals.pop(0)


def test_inventory_add_remove_count():
    user: dict = {}
    assert inventory._add_item(user, "经验书", 2) == 2
    assert inventory._remove_item(user, "经验书", 1) is True
    assert inventory._item_count(user, "经验书") == 1
    assert inventory._remove_item(user, "经验书", 5) is False
    inventory._remove_item(user, "经验书", 1)
    assert "经验书" not in user["inventory"]


def test_roll_drops_respects_mult():
    m = {"drops": [{"item": "经验书", "chance": 0.5}]}
    assert inventory._roll_drops(m, _SeqRandom([0.4])) == ["经验书"]       # 0.4 < 0.5
    assert inventory._roll_drops(m, _SeqRandom([0.4]), mult=0.5) == []     # 0.4 < 0.25? 否
    assert inventory._roll_drops(m, _SeqRandom([0.7]), mult=2.0) == ["经验书"]  # 0.7 < 1.0


def test_apply_item_effect_exp_buff_and_grant():
    card = inventory._item_by_name("双倍经验卡")
    book = inventory._item_by_name("经验书")
    u: dict = {}
    ok, _ = inventory._apply_item_effect(u, card)
    assert ok and u["exp_buff_uses"] == int(card["effect"]["uses"]) and u["exp_buff_mult"] == int(card["effect"]["mult"])
    u2 = {"exp": 10}
    ok2, _ = inventory._apply_item_effect(u2, book)
    assert ok2 and u2["exp"] == 10 + int(book["effect"]["amount"])


# ==================== 纯逻辑：强化 ====================

def test_forge_guards_and_success():
    today = "D"
    fcfg = rpg_config._cfg("forge", {})
    base, mx = int(fcfg["cost_base"]), int(fcfg["max_per_day"])
    ok, msg = smith._forge({"equip_date": ""}, today)
    assert ok is False and "还没领装备" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": True}, today)
    assert ok is False and "损坏" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 0}, today)
    assert ok is False and "积分不够" in msg
    u = {"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 1000}
    ok, msg = smith._forge(u, today)
    assert ok and u["equip_forge"] == 1 and u["points"] == 1000 - base
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": mx, "points": 10 ** 9}, today)
    assert ok is False and "上限" in msg


# ==================== 指令 ====================

def _patch_io(monkeypatch, mod, *, today="2026-06-22", store=None):
    state = game_store._normalize_data(store or {})
    if hasattr(mod, "_today_str"):
        monkeypatch.setattr(mod, "_today_str", lambda: today)
    if hasattr(mod, "is_sleeping"):
        monkeypatch.setattr(mod, "is_sleeping", lambda: False)
    monkeypatch.setattr(mod, "_load_data", lambda: deepcopy(state))

    def _save(data):
        state.clear()
        state.update(deepcopy(game_store._normalize_data(data)))

    monkeypatch.setattr(mod, "_save_data", _save)
    return state


def _equipped_user(**extra):
    base = {"exp": 0, "equip_date": "2026-06-22", "equip_level": 1, "equip_roll": 0,
            "equip_used": False, "equip_forge": 0}
    base.update(extra)
    return base


def _stub_hunt_rng(monkeypatch, monster, *, event="", drops=None):
    monkeypatch.setattr(hunt, "_pick_monster", lambda rng=hunt.random: monster)
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: event)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_drops", lambda m, rng=hunt.random, mult=1.0: list(drops or []))


@pytest.mark.asyncio
async def test_hunt_happy_consumes_equip_and_grants_exp(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["equip_used"] is True                       # 今日装备损坏
    assert user["exp"] == hunt._challenge_exp(True, 1)      # 胜（战力 15 ≥ 1）
    assert "[at:u1]" in str(exc.value.result) and "史莱姆" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_blocked_without_equip(monkeypatch):
    _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": {"exp": 0}}}}})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    assert "还没签到" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_blocked_when_equip_broken(monkeypatch):
    _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(equip_used=True)}}}})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    assert "损坏" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_exp_buff_doubles(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(exp_buff_uses=1, exp_buff_mult=2)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == hunt._challenge_exp(True, 1) * 2 and user["exp_buff_uses"] == 0
    assert "翻倍" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_loot_into_bag(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史", "power_req": 1, "drops": [{"item": "经验书", "chance": 1.0}]},
                   drops=["经验书"])
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["inventory"]["经验书"] == 1
    assert "掉落" in str(exc.value.result)


@pytest.mark.asyncio
async def test_forge_cmd_deducts_points(monkeypatch):
    base = int(rpg_config._cfg("forge", {})["cost_base"])
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(points=1000)}}}})
    with pytest.raises(FinishedException) as exc:
        await smith.forge_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["equip_forge"] == 1 and user["points"] == 1000 - base
    assert "强化" in str(exc.value.result)


@pytest.mark.asyncio
async def test_status_panel_only_level_and_equip(monkeypatch):
    lv3 = player._cum_exp(3, player._level_base())
    state = game_store._normalize_data({"groups": {"1001": {"users": {
        "u1": {"exp": lv3, "points": 250, "equip_date": "2026-06-22", "equip_used": False,
               "equip_forge": 1, "inventory": {"经验书": 2}}}}}})
    monkeypatch.setattr(character, "_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(character, "is_sleeping", lambda: False)
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert "Lv3" in r and "今日装备" in r and "已强化" in r and "250" in r
    assert "战力" not in r  # 战力隐藏，不外显


@pytest.mark.asyncio
async def test_bag_and_use_book(monkeypatch):
    state = _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "inventory": {"经验书": 1}}}}}})
    with pytest.raises(FinishedException) as exc:
        await inventory.bag_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    assert "经验书" in str(exc.value.result)
    book = inventory._item_by_name("经验书")
    with pytest.raises(FinishedException):
        await inventory.use_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("经验书"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == int(book["effect"]["amount"]) and "经验书" not in user.get("inventory", {})

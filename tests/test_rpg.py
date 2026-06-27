"""测试 rpg 子包：运势 / 打野 / 角色面板。

数值断言一律从配置读（rpg_config._cfg(...) / player._level_base() 等），调数值不会让测试变脆。
存储与 gift 共享 core.game_store，因此打野/签到写入的就是同一份玩家库。
"""

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import game_store

# 导入子包即触发命令注册与签到钩子注册（rpg/__init__ 内 from . import character, fortune, hunt, inventory, shop）
import nonebot_plugin_akito.features.rpg.character as character
import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.equipment as equipment
import nonebot_plugin_akito.features.rpg.fortune as fortune
import nonebot_plugin_akito.features.rpg.hunt as hunt
import nonebot_plugin_akito.features.rpg.inventory as inventory
import nonebot_plugin_akito.features.rpg.player as player
import nonebot_plugin_akito.features.rpg.shop as shop


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
    assert str(base * 3) in line                         # 签到行只报经验数
    assert "大吉" not in line                             # 不外显运势


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
    assert "大凶" not in line                             # 不外显运势


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


# ==================== 背包 / 道具：纯逻辑 ====================

def test_inventory_add_remove_count():
    user: dict = {}
    assert inventory._item_count(user, "精力药水") == 0
    assert inventory._add_item(user, "精力药水", 2) == 2
    inventory._add_item(user, "精力药水")
    assert inventory._item_count(user, "精力药水") == 3
    assert inventory._remove_item(user, "精力药水", 2) is True
    assert inventory._item_count(user, "精力药水") == 1
    assert inventory._remove_item(user, "精力药水", 5) is False  # 不足不改动
    assert inventory._item_count(user, "精力药水") == 1
    inventory._remove_item(user, "精力药水", 1)
    assert "精力药水" not in user["inventory"]                   # 扣空移除


class _SeqRandom:
    """random() 依次返回给定值，用于掉落概率的确定性测试。"""

    def __init__(self, vals):
        self.vals = list(vals)

    def random(self):
        return self.vals.pop(0)


def test_roll_drops_respects_chance():
    monster = {"drops": [{"item": "精力药水", "chance": 0.5}, {"item": "转运石", "chance": 0.5}]}
    assert inventory._roll_drops(monster, _SeqRandom([0.1, 0.9])) == ["精力药水"]  # 仅第一个命中
    assert inventory._roll_drops(monster, _SeqRandom([0.9, 0.1])) == ["转运石"]
    assert inventory._roll_drops({"drops": []}, _SeqRandom([])) == []


def test_apply_item_effect_stamina_and_full_guard():
    potion = inventory._item_by_name("精力药水")
    amount = int(potion["effect"]["amount"])
    user = {"stamina": 10, "stamina_date": "2026-06-22"}
    ok, _msg = inventory._apply_item_effect(user, potion, "2026-06-22")
    assert ok and user["stamina"] == min(player._stamina_max(), 10 + amount)
    full = {"stamina": player._stamina_max(), "stamina_date": "2026-06-22"}
    ok2, msg2 = inventory._apply_item_effect(full, potion, "2026-06-22")
    assert ok2 is False and "满" in msg2  # 满精力拒绝、不消耗


def test_apply_item_effect_exp_buff():
    card = inventory._item_by_name("双倍经验卡")
    user: dict = {}
    ok, _msg = inventory._apply_item_effect(user, card, "2026-06-22")
    assert ok
    assert user["exp_buff_uses"] == int(card["effect"]["uses"])
    assert user["exp_buff_mult"] == int(card["effect"]["mult"])


def test_apply_item_effect_reroll_needs_signin(monkeypatch):
    stone = inventory._item_by_name("转运石")
    ok, msg = inventory._apply_item_effect({"fortune_date": ""}, stone, "2026-06-22")
    assert ok is False and "签到" in msg                       # 未签到拒绝
    monkeypatch.setattr(inventory, "_roll_fortune", lambda u, rng: "ji")
    user = {"fortune_date": "2026-06-22", "fortune": "daxiong"}
    ok2, _ = inventory._apply_item_effect(user, stone, "2026-06-22")
    assert ok2 and user["fortune"] == "ji"                     # 重掷今日运势


# ==================== 背包 / 商店 / 使用 / 购买：指令 ====================

def _patch_io(monkeypatch, mod, *, today="2026-06-22", store=None):
    """给 inventory/shop/equipment 模块打桩 IO/时间/睡眠（同 _patch_hunt 思路）。

    _today_str / is_sleeping 仅当模块导入了才打桩（equipment 不依赖这俩）。
    """
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


@pytest.mark.asyncio
async def test_bag_cmd_empty_and_filled(monkeypatch):
    _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {"u1": {}}}}})
    with pytest.raises(FinishedException) as exc:
        await inventory.bag_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    assert "空空" in str(exc.value.result)

    _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {
        "u1": {"inventory": {"精力药水": 2}}}}}})
    with pytest.raises(FinishedException) as exc2:
        await inventory.bag_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc2.value.result)
    assert "精力药水" in r and "×2" in r


@pytest.mark.asyncio
async def test_use_cmd_consumes_and_applies(monkeypatch):
    state = _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {
        "u1": {"inventory": {"双倍经验卡": 1}}}}}})
    with pytest.raises(FinishedException) as exc:
        await inventory.use_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("双倍经验卡"))
    assert "双倍" in str(exc.value.result)
    user = state["groups"]["1001"]["users"]["u1"]
    assert user.get("exp_buff_uses", 0) == 1
    assert "双倍经验卡" not in user.get("inventory", {})         # 已消耗


@pytest.mark.asyncio
async def test_use_cmd_not_owned(monkeypatch):
    _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {"u1": {}}}}})
    with pytest.raises(FinishedException) as exc:
        await inventory.use_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("精力药水"))
    assert "没有" in str(exc.value.result)


@pytest.mark.asyncio
async def test_shop_cmd_lists_items(monkeypatch):
    _patch_io(monkeypatch, shop, store={"groups": {"1001": {"users": {"u1": {}}}}})
    with pytest.raises(FinishedException) as exc:
        await shop.shop_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert "商店" in r and "精力药水" in r and "积分" in r


@pytest.mark.asyncio
async def test_buy_cmd_deducts_points_and_adds_item(monkeypatch):
    potion = inventory._item_by_name("精力药水")
    state = _patch_io(monkeypatch, shop, store={"groups": {"1001": {"users": {"u1": {"points": 1000}}}}})
    with pytest.raises(FinishedException):
        await shop.buy_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("精力药水 2"))
    user = state["groups"]["1001"]["users"]["u1"]
    cost = int(potion["price"]) * 2
    assert user["points"] == 1000 - cost
    assert user["inventory"]["精力药水"] == 2
    assert user["buy_counts"]["精力药水"] == 2


@pytest.mark.asyncio
async def test_buy_cmd_insufficient_points(monkeypatch):
    _patch_io(monkeypatch, shop, store={"groups": {"1001": {"users": {"u1": {"points": 10}}}}})
    with pytest.raises(FinishedException) as exc:
        await shop.buy_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("精力药水"))
    assert "积分不够" in str(exc.value.result)


@pytest.mark.asyncio
async def test_buy_cmd_daily_limit(monkeypatch):
    card = inventory._item_by_name("双倍经验卡")
    limit = int(card["daily_buy_limit"])
    _patch_io(monkeypatch, shop, store={"groups": {"1001": {"users": {
        "u1": {"points": 100000, "buy_date": "2026-06-22", "buy_counts": {"双倍经验卡": limit}}}}}})
    with pytest.raises(FinishedException) as exc:
        await shop.buy_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("双倍经验卡"))
    assert "买够" in str(exc.value.result)


@pytest.mark.asyncio
async def test_buy_cmd_unknown_item(monkeypatch):
    _patch_io(monkeypatch, shop, store={"groups": {"1001": {"users": {"u1": {"points": 100000}}}}})
    with pytest.raises(FinishedException) as exc:
        await shop.buy_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("不存在的道具"))
    assert "没在卖" in str(exc.value.result)


# ==================== 打野：双倍经验卡 / 掉落 联动 ====================

@pytest.mark.asyncio
async def test_hunt_consumes_exp_buff_and_doubles(monkeypatch):
    state = _patch_hunt(monkeypatch, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 100, "stamina_date": "2026-06-22",
               "exp_buff_uses": 1, "exp_buff_mult": 2}}}}})
    monkeypatch.setattr(hunt, "_pick_monster", lambda rng=hunt.random: _slime())
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "")
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_drops", lambda monster, rng=hunt.random: [])
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == 50 * 2          # 史莱姆 50 经验 ×2
    assert user["exp_buff_uses"] == 0     # buff 已消耗
    assert "翻倍" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_loot_drops_into_inventory(monkeypatch):
    state = _patch_hunt(monkeypatch, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "stamina": 100, "stamina_date": "2026-06-22"}}}}})
    monkeypatch.setattr(hunt, "_pick_monster", lambda rng=hunt.random: _slime())
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "")
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_drops", lambda monster, rng=hunt.random: ["精力药水"])
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["inventory"]["精力药水"] == 1
    r = str(exc.value.result)
    assert "掉落" in r and "精力药水" in r


# ==================== 装备：纯逻辑 ====================

def test_is_equipment():
    assert equipment._is_equipment(inventory._item_by_name("铁剑")) is True
    assert equipment._is_equipment(inventory._item_by_name("精力药水")) is False
    assert equipment._is_equipment(None) is False


def test_equip_moves_from_bag_and_boosts_power():
    sword = inventory._item_by_name("铁剑")
    user = {"exp": 0, "inventory": {"铁剑": 1}}
    base_cp = player._combat_power(user)               # 未装备
    ok, _msg = equipment._equip(user, "铁剑")
    assert ok
    assert user["equipped"]["武器"] == "铁剑"
    assert "铁剑" not in user.get("inventory", {})       # 移出背包
    assert player._combat_power(user) == base_cp + int(sword["power"])


def test_equip_swaps_same_slot_and_returns_old():
    user = {"exp": 0, "inventory": {"铁剑": 1, "精钢剑": 1}}
    equipment._equip(user, "铁剑")
    ok, _ = equipment._equip(user, "精钢剑")            # 同槽换装
    assert ok
    assert user["equipped"]["武器"] == "精钢剑"
    assert user["inventory"].get("铁剑") == 1            # 旧装备退回背包
    assert "精钢剑" not in user["inventory"]


def test_equip_rejects_non_equipment_and_unowned():
    user = {"inventory": {}}
    ok, msg = equipment._equip(user, "精力药水")
    assert ok is False and "不是装备" in msg            # 消耗品不能装备
    ok2, msg2 = equipment._equip(user, "铁剑")
    assert ok2 is False and "没有" in msg2              # 未拥有


def test_unequip_by_slot_and_by_name():
    user = {"exp": 0, "inventory": {"铁剑": 1}}
    equipment._equip(user, "铁剑")
    ok, _ = equipment._unequip(user, "武器")            # 按部位
    assert ok and user["inventory"]["铁剑"] == 1 and "武器" not in user["equipped"]
    equipment._equip(user, "铁剑")
    ok2, _ = equipment._unequip(user, "铁剑")           # 按装备名
    assert ok2 and user["inventory"]["铁剑"] == 1


def test_unequip_nothing_equipped():
    ok, msg = equipment._unequip({"equipped": {}}, "武器")
    assert ok is False and "没有穿戴" in msg


def test_equipped_power_sums_slots():
    sword = inventory._item_by_name("铁剑")
    armor = inventory._item_by_name("铁甲")
    user = {"equipped": {"武器": "铁剑", "防具": "铁甲"}}
    assert player._equipped_power(user) == int(sword["power"]) + int(armor["power"])
    assert player._equipped_power({"equipped": {}}) == 0


# ==================== 装备：指令 ====================

@pytest.mark.asyncio
async def test_equip_cmd_happy(monkeypatch):
    state = _patch_io(monkeypatch, equipment, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "inventory": {"铁剑": 1}}}}}})
    with pytest.raises(FinishedException) as exc:
        await equipment.equip_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("铁剑"))
    assert "装备" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["u1"]["equipped"]["武器"] == "铁剑"


@pytest.mark.asyncio
async def test_unequip_cmd_happy(monkeypatch):
    state = _patch_io(monkeypatch, equipment, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "inventory": {}, "equipped": {"武器": "铁剑"}}}}}})
    with pytest.raises(FinishedException) as exc:
        await equipment.unequip_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("武器"))
    assert "卸下" in str(exc.value.result)
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["inventory"]["铁剑"] == 1 and "武器" not in user.get("equipped", {})


@pytest.mark.asyncio
async def test_use_cmd_rejects_equipment(monkeypatch):
    _patch_io(monkeypatch, inventory, store={"groups": {"1001": {"users": {
        "u1": {"inventory": {"铁剑": 1}}}}}})
    with pytest.raises(FinishedException) as exc:
        await inventory.use_cmd.handlers[0](Event(group_id=1001, user_id="u1"), Message("铁剑"))
    assert "装备" in str(exc.value.result)              # 提示用「装备」指令

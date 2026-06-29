"""测试 rpg 子包（精简版）：签到领装备 → 选择打怪。

角色对外只有等级；战力是今日装备隐藏值；运势隐藏（影响打怪）；积分出口只有「强化」。
数值断言一律从配置读，调数值不会让测试变脆。存储与 gift 共享 core.game_store。
"""

from __future__ import annotations

from copy import deepcopy
import types

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
import nonebot_plugin_akito.features.rpg.team as team


def _bot():
    return Bot(self_id="114514")


def _at(qq):
    return types.SimpleNamespace(type="at", data={"qq": str(qq)})


def _team_event(initiator, target):
    return Event(group_id=1001, user_id=initiator, original_message=[_at(target)])


class _FixedRand:
    def __init__(self, val=0):
        self.val = val

    def randint(self, _a, _b):
        return self.val


class _Rng:
    """组队成功掷骰 + 文案随机选用 的桩：random() 返回固定值、choice 取首项。"""

    def __init__(self, r):
        self._r = r

    def random(self):
        return self._r

    def choice(self, seq):
        return seq[0]


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
    scfg = rpg_config._cfg("signin_streak", {})
    day1_bonus = min(1 * int(scfg["per_day"]), int(scfg["cap"]))  # 首签连签 1 天的额外经验
    assert user["exp"] == int(rpg_config._cfg("signin", {})["exp"]) + day1_bonus
    assert user["signin_streak"] == 1 and user["signin_last_date"] == "2026-06-22"
    assert user["fortune"] == "daji" and user["fortune_date"] == "2026-06-22"  # 运势暗掷
    assert user["equip_date"] == "2026-06-22" and user["equip_used"] is False  # 发今日装备
    assert "经验" in line and "Lv" in line and "连签" in line
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


def test_pick_encounter_uses_stage_weights_and_elite_gate():
    class _CaptureRng:
        def __init__(self, roll):
            self.roll = roll
            self.weights = []

        def choices(self, seq, weights, k):
            self.weights = list(weights)
            return [seq[0]]

        def random(self):
            return self.roll

    low = _CaptureRng(0.0)
    _monster, elite = hunt._pick_encounter(2, low)
    assert low.weights == [58, 32, 10, 0]
    assert elite is False

    high = _CaptureRng(0.0)
    _monster, elite = hunt._pick_encounter(8, high)
    assert high.weights == [40, 30, 20, 10]
    assert elite is True


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
    first_cost, mx = smith._forge_cost(fcfg, 0), int(fcfg["max_per_day"])
    ok, msg = smith._forge({"equip_date": ""}, today)
    assert ok is False and "还没领装备" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": True}, today)
    assert ok is False and "损坏" in msg
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 0}, today)
    assert ok is False and "积分不够" in msg
    u = {"equip_date": today, "equip_used": False, "equip_forge": 0, "points": 1000}
    ok, msg = smith._forge(u, today)
    assert ok and u["equip_forge"] == 1 and u["points"] == 1000 - first_cost
    ok, msg = smith._forge({"equip_date": today, "equip_used": False, "equip_forge": mx, "points": 10 ** 9}, today)
    assert ok is False and "上限" in msg


def test_forge_costs_list_and_linear_fallback(monkeypatch):
    orig_cfg = smith._cfg
    monkeypatch.setattr(
        smith,
        "_cfg",
        lambda key, default=None: {"cost_base": 100, "costs": [60, 150, 300], "max_per_day": 3, "step": 6}
        if key == "forge" else orig_cfg(key, default),
    )
    u = {"equip_date": "D", "equip_used": False, "equip_forge": 0, "points": 1000}
    assert smith._forge(u, "D")[0] is True and u["points"] == 940
    assert smith._forge(u, "D")[0] is True and u["points"] == 790
    assert smith._forge(u, "D")[0] is True and u["points"] == 490

    monkeypatch.setattr(
        smith,
        "_cfg",
        lambda key, default=None: {"cost_base": 80, "max_per_day": 5, "step": 6}
        if key == "forge" else orig_cfg(key, default),
    )
    u2 = {"equip_date": "D", "equip_used": False, "equip_forge": 0, "points": 1000}
    assert smith._forge(u2, "D")[0] is True and u2["points"] == 920


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


_PLAIN_BUFF = {"key": "plain", "name": "平日", "exp_mult": 1.0, "drop_mult": 1.0}


def _stub_hunt_rng(monkeypatch, monster, *, event="", drops=None, elite=False, buff=None):
    # 遭遇桩：精英默认关、今日增益默认平日 → 既有用例保持确定（数值不被随机精英/增益扰动）
    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, elite))
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: event)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_drops", lambda m, rng=hunt.random, mult=1.0: list(drops or []))
    monkeypatch.setattr(hunt, "_today_buff", lambda: buff or _PLAIN_BUFF)


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
    base = smith._forge_cost(rpg_config._cfg("forge", {}), 0)
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


# ==================== 打怪给积分 ====================

@pytest.mark.asyncio
async def test_hunt_grants_points(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["points"] == hunt._challenge_points(True)  # 胜得 win_points
    assert "积分" in str(exc.value.result)


def test_settle_solo_rookie_bonus_only_applies_to_solo(monkeypatch):
    factors: list[float] = []
    monster = {"name": "史莱姆", "power_req": 1, "drops": []}

    def _pick(level, rng=hunt.random):
        return monster, False

    def _resolve(combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None):
        factors.append(power_factor)
        return {"win": True, "effective": int(combat_power * power_factor * fortune_factor),
                "event": event or "", "monster": eff_monster}

    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}
    monkeypatch.setattr(hunt, "_pick_encounter", _pick)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(hunt, "resolve_hunt", _resolve)
    monkeypatch.setattr(hunt, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    hunt._settle_solo(_equipped_user(exp=0, equip_level=1), "D")
    hunt._settle_coop(_equipped_user(exp=0, equip_level=1), _equipped_user(exp=0, equip_level=1), "D")

    assert factors[0] == pytest.approx(1.08)
    assert factors[1] == pytest.approx(1.0)


# ==================== 纯逻辑：组队成功率 / 经验加成 ====================

def test_team_success_rate_scales_and_clamps():
    t = rpg_config._cfg("team", {})
    base, step = float(t["base_success"]), float(t["per_level"])
    assert team._team_success_rate(1) == pytest.approx(base)              # Lv1 = base
    assert team._team_success_rate(3) == pytest.approx(base + 2 * step)   # 随羁绊等级爬升
    assert team._team_success_rate(99) == pytest.approx(float(t["max_success"]))   # 封顶
    assert team._team_success_rate(-5) == pytest.approx(float(t["min_success"]))   # 封底（负档硬拉）


def test_team_exp_bonus_scales_and_caps():
    t = rpg_config._cfg("team", {})
    per, cap = float(t["exp_bonus_per_level"]), float(t["exp_bonus_max"])
    assert team._team_exp_bonus(1) == 0.0                       # Lv1 无加成
    assert team._team_exp_bonus(3) == pytest.approx(2 * per)
    assert team._team_exp_bonus(9999) == pytest.approx(cap)     # 封顶


# ==================== 指令：组队 ====================

@pytest.mark.asyncio
async def test_team_guards(monkeypatch):
    _patch_io(monkeypatch, team, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    with pytest.raises(FinishedException) as e1:  # 无 @ 目标
        await team.team_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1", original_message=[]))
    assert "@" in str(e1.value.result)
    with pytest.raises(FinishedException) as e2:  # @ 自己
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u1"))
    assert "自己" in str(e2.value.result)
    with pytest.raises(FinishedException) as e3:  # @ bot
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "114514"))
    assert "小彰" in str(e3.value.result)


@pytest.mark.asyncio
async def test_team_success_both_rewarded(monkeypatch):
    # 顶级羁绊 + random=0 → 必成功；合力打弱怪，双方各得经验+积分、各自装备都消耗、@ 双方
    store = {"groups": {"1001": {
        "users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)},
        "intimacy": {game_store._pair_key("u1", "u2"): 20000},
    }}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is True   # 双方装备都消耗
    win_pts = int(rpg_config._cfg("challenge", {})["win_points"])
    assert g["u1"]["points"] == win_pts and g["u2"]["points"] == win_pts      # 双方各得积分
    assert g["u1"]["exp"] > 0 and g["u2"]["exp"] > 0
    r = str(exc.value.result)
    assert "[at:u1]" in r and "[at:u2]" in r                                   # @ 双方


def test_settle_coop_uses_higher_level_for_encounter(monkeypatch):
    captured: dict = {}
    monster = {"name": "史莱姆", "power_req": 1, "drops": []}

    def _pick(level, rng=hunt.random):
        captured["level"] = level
        return monster, False

    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}
    monkeypatch.setattr(hunt, "_pick_encounter", _pick)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        hunt,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        {"win": True, "effective": int(combat_power * power_factor * fortune_factor),
         "event": event or "", "monster": eff_monster},
    )
    monkeypatch.setattr(hunt, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    hunt._settle_coop(
        _equipped_user(exp=player._cum_exp(2, player._level_base()), equip_level=2),
        _equipped_user(exp=player._cum_exp(7, player._level_base()), equip_level=7),
        "D",
    )

    assert captured["level"] == 7


@pytest.mark.asyncio
async def test_team_fail_when_partner_no_equip(monkeypatch):
    # 队友今天没装备 → 必定失败（拉不动），发起人单刷，只消耗发起人装备，队友毫发无损
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": {"exp": 0}}}}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.0))  # 即便骰子必成，也因对方无装备而失败
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True              # 发起人单刷消耗
    assert g["u2"].get("equip_used") is False          # 队友未被卷入、装备没动
    assert g["u2"]["exp"] == 0 and g["u2"]["points"] == 0  # 队友零收益零损耗
    assert "自己上" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_fail_by_rng_degrades_to_solo(monkeypatch):
    # 无羁绊（低成功率）+ random=0.999 → 拉不动，退化单刷；只消耗发起人装备
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)}}}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.999))
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is False
    assert g["u2"]["exp"] == 0 and g["u2"]["points"] == 0
    assert "自己上" in str(exc.value.result)


# ==================== 称号 / 连签 / 战绩 ====================

def test_title_of_brackets():
    titles = rpg_config._cfg("titles", [])
    assert player._title_of(titles[0]["min_level"]) == titles[0]["name"]   # 最低档
    assert player._title_of(int(titles[1]["min_level"])) == titles[1]["name"]
    assert player._title_of(int(titles[1]["min_level"]) - 1) == titles[0]["name"]  # 未达则取低档
    assert player._title_of(10 ** 6) == titles[-1]["name"]                  # 顶档


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
    bonus = min(5 * int(scfg["per_day"]), int(scfg["cap"]))
    assert u["exp"] == int(rpg_config._cfg("signin", {})["exp"]) + bonus


@pytest.mark.asyncio
async def test_hunt_records_battle_stats(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})  # 必胜
    with pytest.raises(FinishedException):
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["hunt_total"] == 1 and user["hunt_wins"] == 1


# ==================== 打怪变数：精英 / 今日增益 ====================

@pytest.mark.asyncio
async def test_hunt_elite_boosts_rewards_and_reveals(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "哥布林", "power_req": 1, "drops": []}, elite=True)
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    elite_mult = float(rpg_config._cfg("combat", {})["elite"]["exp_mult"])
    assert user["exp"] == int(hunt._challenge_exp(True, 1) * elite_mult)  # 精英胜则经验 ×elite.exp_mult
    assert "精英" in str(exc.value.result)


def test_today_buff_deterministic_by_date(monkeypatch):
    monkeypatch.setattr(hunt, "_today_str", lambda: "2026-07-01")
    assert hunt._today_buff()["key"] == hunt._today_buff()["key"]            # 同一天一致
    assert hunt._today_buff()["key"] in set(rpg_config._cfg("daily_buffs", {}))


@pytest.mark.asyncio
async def test_hunt_daily_buff_exp_applies(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    surge = {"key": "exp", "name": "经验涌动日", "exp_mult": 1.5, "drop_mult": 1.0}
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []}, buff=surge)
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == int(hunt._challenge_exp(True, 1) * 1.5)            # 经验涌动日 ×1.5
    assert "经验涌动日" in str(exc.value.result)


# ==================== 排行榜 / 面板（称号·战绩） ====================

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
    monkeypatch.setattr(character, "is_sleeping", lambda: False)
    monkeypatch.setattr(character, "_load_data", lambda: deepcopy(state))
    with pytest.raises(FinishedException) as exc:
        await character.status_cmd.handlers[0](Event(group_id=1001, user_id="u1"))
    r = str(exc.value.result)
    assert player._title_of(3) in r            # 称号
    assert "4 胜" in r and "5 场" in r          # 战绩

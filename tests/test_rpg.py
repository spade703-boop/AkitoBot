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
    assert hunt.resolve_hunt(20, m, power_factor=1.0, event="slip")["win"] is False  # ×0.74=14.8<18
    big = {"name": "强怪", "power_req": 28}
    assert hunt.resolve_hunt(20, big, power_factor=1.0)["win"] is False
    assert hunt.resolve_hunt(20, big, power_factor=1.0, event="desperate")["win"] is True  # ×1.6=32≥28


def test_hunt_result_lines_prefers_result_specific_event_copy_and_falls_back(monkeypatch):
    copy_table = {
        "event_slip_win": ["slip-win {monster}"],
        "event_insight": ["insight-generic {monster}"],
    }
    orig_cfg = hunt._cfg
    monkeypatch.setattr(hunt, "_cfg", lambda key, default=None: copy_table if key == "copy" else orig_cfg(key, default))
    monkeypatch.setattr(hunt, "_copy", lambda key: copy_table.get(key, [key]))
    monkeypatch.setattr(hunt.random, "choice", lambda seq: seq[0])

    slip_lines = hunt._hunt_result_lines({
        "monster": {"name": "slime"},
        "event": "slip",
        "win": True,
        "exp_gain": 1,
        "points_gain": 1,
        "exp_buffed": False,
        "drops": [],
        "old_level": 1,
        "new_level": 1,
        "buff": _PLAIN_BUFF,
    })
    insight_lines = hunt._hunt_result_lines({
        "monster": {"name": "goblin"},
        "event": "insight",
        "win": True,
        "exp_gain": 1,
        "points_gain": 1,
        "exp_buffed": False,
        "drops": [],
        "old_level": 1,
        "new_level": 1,
        "buff": _PLAIN_BUFF,
    })

    assert slip_lines[0] == "slip-win slime"
    assert insight_lines[0] == "insight-generic goblin"


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

    def _weights_for(level: int) -> list[int]:
        brackets = rpg_config._cfg("combat", {}).get("encounter_brackets", [])
        for bracket in brackets:
            max_level = bracket.get("max_level")
            if max_level is None or level <= int(max_level):
                return list(bracket["weights"])
        return []

    low = _CaptureRng(0.0)
    _monster, elite = hunt._pick_encounter(2, low)
    assert low.weights == _weights_for(2)
    assert elite is False

    high = _CaptureRng(0.0)
    _monster, elite = hunt._pick_encounter(8, high)
    assert high.weights == _weights_for(8)
    assert elite is True


def test_pick_monster_uses_brackets_for_non_six_monster_pool(monkeypatch):
    class _CaptureRng:
        def __init__(self):
            self.weights = []

        def choices(self, seq, weights, k):
            self.weights = list(weights)
            return [seq[0]]

    monsters = [
        {"name": "A", "power_req": 1, "weight": 7},
        {"name": "B", "power_req": 2, "weight": 5},
        {"name": "C", "power_req": 3, "weight": 99},
    ]
    combat = {
        "encounter_brackets": [
            {"max_level": 2, "weights": [9, 1, 0]},
            {"max_level": None, "weights": [1, 2, 3]},
        ]
    }
    orig_cfg = hunt._cfg
    monkeypatch.setattr(
        hunt,
        "_cfg",
        lambda key, default=None: monsters if key == "monsters"
        else combat if key == "combat"
        else orig_cfg(key, default),
    )
    rng = _CaptureRng()

    picked = hunt._pick_monster(2, rng)

    assert picked["name"] == "A"
    assert rng.weights == [9, 1, 0]


@pytest.mark.parametrize("combat", [
    {},
    {"encounter_brackets": "oops"},
    {"encounter_brackets": [{"max_level": None, "weights": [9, 1]}]},
])
def test_pick_monster_falls_back_to_monster_weights_when_brackets_unusable(monkeypatch, combat):
    class _CaptureRng:
        def __init__(self):
            self.weights = []

        def choices(self, seq, weights, k):
            self.weights = list(weights)
            return [seq[0]]

    monsters = [
        {"name": "A", "power_req": 1, "weight": 7},
        {"name": "B", "power_req": 2, "weight": 5},
        {"name": "C", "power_req": 3, "weight": 3},
    ]
    orig_cfg = hunt._cfg
    monkeypatch.setattr(
        hunt,
        "_cfg",
        lambda key, default=None: monsters if key == "monsters"
        else combat if key == "combat"
        else orig_cfg(key, default),
    )
    rng = _CaptureRng()

    picked = hunt._pick_monster(2, rng)

    assert picked["name"] == "A"
    assert rng.weights == [7, 5, 3]


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
        lambda key, default=None: {"cost_base": 100, "costs": [30, 60, 90], "max_per_day": 3, "step": 6}
        if key == "forge" else orig_cfg(key, default),
    )
    u = {"equip_date": "D", "equip_used": False, "equip_forge": 0, "points": 1000}
    assert smith._forge(u, "D")[0] is True and u["points"] == 970
    assert smith._forge(u, "D")[0] is True and u["points"] == 910
    assert smith._forge(u, "D")[0] is True and u["points"] == 820

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
        await inventory.use_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"), Message("经验书"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == int(book["effect"]["amount"]) and "经验书" not in user.get("inventory", {})


def test_is_gift_item():
    assert inventory._is_gift_item({"name": "彰冬无料券", "effect": {"type": "gift", "gift_name": "彰冬无料"}})
    assert not inventory._is_gift_item({"name": "经验书", "effect": {"type": "exp_grant", "amount": 80}})
    assert not inventory._is_gift_item({})


def test_pick_gift_by_name():
    from nonebot_plugin_akito.features.gift import _pick_gift_by_name
    g = _pick_gift_by_name("彰冬无料")
    assert g is not None and g["name"] == "彰冬无料" and g["cost"] == 50
    assert _pick_gift_by_name("不存在的礼物") is None


async def test_use_gift_voucher_flow(monkeypatch):
    import nonebot_plugin_akito.features.gift as gift_mod
    from nonebot_plugin_akito.features.rpg import inventory as inv
    state = _patch_io(monkeypatch, inv, store={"groups": {"1001": {"users": {
        "u1": {"exp": 0, "points": 0, "inventory": {"彰冬无料券": 1}},
        "u2": {"exp": 0, "points": 0},
    }, "intimacy": {}}}})
    orig_cfg = inv._cfg
    monkeypatch.setattr(inv, "_cfg", lambda key, default=None: {"gifts": [
        {"name": "彰冬无料", "cost": 50, "intimacy": 12},
    ]} if key == "gifts" else orig_cfg(key, default))
    monkeypatch.setattr(gift_mod, "_cfg", lambda key, default=None: {
        "sign_delay_sec": {}, "gifts": [{"name": "彰冬无料", "cost": 50, "intimacy": 12}],
        "crit_multiplier": 2, "fail_refund_ratio": 0.3,
        "event_weights": {"normal": 100}, "mishaps": {}, "return_gifts": {},
        "bond_levels": [{"min": 0, "name": "Hot Dogs"}],
        "copy": {"normal": ["{a} 送了 {b} {gift}，羁绊 +{amount}。"]},
    }.get(key, orig_cfg(key, default) if key != "gifts" else [{"name": "彰冬无料", "cost": 50, "intimacy": 12}]))
    monkeypatch.setattr(gift_mod, "_roll_main_event", lambda rng=gift_mod.random: "normal")
    event = Event(group_id=1001, user_id="u1", original_message=[_at("u2")])
    with pytest.raises(FinishedException) as exc:
        await inv.use_cmd.handlers[0](_bot(), event, Message("彰冬无料券"))
    result = str(exc.value.result)
    assert "彰冬无料" in result
    assert "羁绊" in result
    u1 = state["groups"]["1001"]["users"]["u1"]
    assert "彰冬无料券" not in u1.get("inventory", {})

@pytest.mark.asyncio
async def test_hunt_grants_points(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {
        "u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["points"] == hunt._challenge_points(True, user)  # 胜得 win_points
    assert "积分" in str(exc.value.result)


def test_challenge_points_halved_for_rebought_equip():
    cfg = rpg_config._cfg("challenge", {})
    wp = int(cfg.get("win_points", 15))
    lp = int(cfg.get("lose_points", 5))
    mult = float(rpg_config._cfg("equip", {}).get("rebuy_points_mult", 0.5))
    assert hunt._challenge_points(True, {"equip_rebought": True}) == int(wp * mult)
    assert hunt._challenge_points(False, {"equip_rebought": True}) == int(lp * mult)
    assert hunt._challenge_points(True, {"equip_rebought": False}) == wp
    assert hunt._challenge_points(False, {}) == lp


def test_rebuy_equip_success(monkeypatch):
    from nonebot_plugin_akito.features.rpg import smith as _s
    cf = {"equip": {"base": 10, "per_level": 5, "var": 6, "rebuy_cost": 100, "rebuy_points_mult": 0.5}}
    orig = _s._cfg
    monkeypatch.setattr(_s, "_cfg", lambda key, default=None: cf.get(key, orig(key, default)))
    u = {"equip_date": "D", "equip_used": True, "points": 300, "equip_forge": 2, "equip_rebought": False}
    ok, msg = _s._rebuy_equip(u, "D")
    assert ok
    assert u["equip_used"] is False
    assert u["equip_rebought"] is True
    assert u["equip_forge"] == 0
    assert u["points"] == 200


def test_rebuy_equip_rejects():
    from nonebot_plugin_akito.features.rpg import smith as _s
    ok, msg = _s._rebuy_equip({}, "D")
    assert ok is False and "没签到" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": False}, "D")
    assert ok is False and "还好好的" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": True, "points": 10}, "D")
    assert ok is False and "积分不够" in msg
    ok, msg = _s._rebuy_equip({"equip_date": "D", "equip_used": True, "points": 500, "equip_rebuy_count": 1}, "D")
    assert ok is False and "买过" in msg


def test_reset_group_rpg_equip_only_refreshes_signed_users():
    group = game_store._new_group()
    lv3 = player._cum_exp(3, player._level_base())
    group["users"]["u1"] = {
        "exp": lv3,
        "fortune": "daji",
        "fortune_date": "2026-06-22",
        "signin_last_date": "2026-06-22",
        "equip_date": "2026-06-22",
        "equip_used": True,
        "equip_forge": 2,
        "equip_rebought": True,
        "equip_rebuy_count": 1,
        "equip_roll": 0,
    }
    group["users"]["u2"] = {"exp": 0, "fortune_date": "", "signin_last_date": ""}

    reset = smith._reset_group_rpg_equip(group, "2026-06-22", _FixedRand(4))

    u1 = group["users"]["u1"]
    u2 = group["users"]["u2"]
    assert reset == 1
    assert u1["equip_date"] == "2026-06-22"
    assert u1["equip_used"] is False
    assert u1["equip_forge"] == 0
    assert u1["equip_rebought"] is False
    assert u1["equip_rebuy_count"] == 0
    assert u1["equip_roll"] == 4
    assert u1["equip_level"] == 3
    assert u1["fortune"] == "daji" and u1["fortune_date"] == "2026-06-22"
    assert u2.get("equip_date", "") == ""


@pytest.mark.asyncio
async def test_reset_rpg_cmd_only_regrants_equips_for_signed_users(monkeypatch):
    state = _patch_io(monkeypatch, smith, store={"groups": {"1001": {"users": {
        "u1": {
            "exp": 0,
            "fortune": "ping",
            "fortune_date": "2026-06-22",
            "signin_last_date": "2026-06-22",
            "equip_date": "2026-06-22",
            "equip_used": True,
            "equip_forge": 2,
        },
        "u2": {"exp": 0},
    }}}})
    monkeypatch.setattr(smith, "random", _FixedRand(2))

    with pytest.raises(FinishedException) as exc:
        await smith.reset_rpg_cmd.handlers[0](Event(group_id=1001, user_id=smith.SUPERUSER_QQ))

    u1 = state["groups"]["1001"]["users"]["u1"]
    u2 = state["groups"]["1001"]["users"]["u2"]
    assert u1["equip_used"] is False and u1["equip_forge"] == 0 and u1["equip_roll"] == 2
    assert u2.get("equip_date", "") == ""
    assert "今天已签到的 1 人" in str(exc.value.result)


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
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
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

def test_team_drop_bonus_scales_and_caps():
    t = rpg_config._cfg("team", {})
    per, cap = float(t["drop_bonus_per_level"]), float(t["drop_bonus_max"])
    assert team._team_drop_bonus(1) == 0.0
    assert team._team_drop_bonus(3) == pytest.approx(2 * per)
    assert team._team_drop_bonus(9999) == pytest.approx(cap)


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
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "focus_fire")
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is True   # 双方装备都消耗
    win_pts = int(rpg_config._cfg("challenge", {})["win_points"])
    assert g["u1"]["points"] == win_pts and g["u2"]["points"] == win_pts      # 双方各得积分
    assert g["u1"]["exp"] > 0 and g["u2"]["exp"] > 0
    assert "协作加成" in str(exc.value.result)
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
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
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


def test_settle_coop_uses_average_fortune_factor(monkeypatch):
    captured: dict = {}
    monster = {"name": "slime", "power_req": 1, "drops": []}
    left = _equipped_user()
    right = _equipped_user()
    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, False))
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(hunt, "_fortune_combat_factor", lambda user, today: 1.4 if user is left else 0.8)
    monkeypatch.setattr(
        hunt,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"fortune_factor": fortune_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )
    monkeypatch.setattr(hunt, "_apply_rewards", lambda *args, **kwargs: dict(reward))

    hunt._settle_coop(left, right, "D")

    assert captured["fortune_factor"] == pytest.approx(1.1)


@pytest.mark.parametrize(
    ("event_key", "expected_power", "expected_exp_mult", "expected_drop_mult"),
    [
        ("focus_fire", 1.10, 1.10, 1.25),
        ("cover_route", 1.00, 1.00, 1.35 * 1.25),
        ("follow_up", 1.00, 1.20, 1.25),
        ("missed_beat", 0.90, 1.00, 1.25),
    ],
)
def test_settle_coop_applies_team_event_and_drop_bonus(
    monkeypatch,
    event_key,
    expected_power,
    expected_exp_mult,
    expected_drop_mult,
):
    captured: dict = {"reward_kwargs": []}
    monster = {"name": "slime", "power_req": 1, "drops": []}
    reward = {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, False))
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: event_key)
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(
        hunt,
        "resolve_hunt",
        lambda combat_power, eff_monster, *, power_factor, fortune_factor=1.0, event=None:
        (captured.update({"power_factor": power_factor}) or {
            "win": True,
            "effective": int(combat_power * power_factor * fortune_factor),
            "event": event or "",
            "monster": eff_monster,
        }),
    )

    def _apply_rewards(*args, **kwargs):
        captured["reward_kwargs"].append(kwargs)
        return dict(reward)

    monkeypatch.setattr(hunt, "_apply_rewards", _apply_rewards)

    hunt._settle_coop(_equipped_user(), _equipped_user(), "D", exp_bonus=0.15, drop_bonus=0.25)

    assert captured["power_factor"] == pytest.approx(expected_power)
    assert len(captured["reward_kwargs"]) == 2
    for kwargs in captured["reward_kwargs"]:
        assert kwargs["exp_bonus"] == pytest.approx(0.15)
        assert kwargs["exp_mult"] == pytest.approx(expected_exp_mult)
        assert kwargs["drop_mult"] == pytest.approx(expected_drop_mult)


@pytest.mark.asyncio
async def test_team_rejects_target_no_signin(monkeypatch):
    # 对方今天未签到 → 硬性拒绝，不退化单刷
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": {"exp": 0}}}}}
    state = _patch_io(monkeypatch, team, store=store)
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is False              # 发起人装备未消耗
    assert "未签到" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_rejects_target_broken_equip(monkeypatch):
    # 对方装备已损坏 → 硬性拒绝，不退化单刷
    u2 = _equipped_user(points=0)
    u2["equip_used"] = True
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": u2}}}}
    state = _patch_io(monkeypatch, team, store=store)
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is False              # 发起人装备未消耗
    assert "损坏" in str(exc.value.result)
    assert "购买装备" in str(exc.value.result)


@pytest.mark.asyncio
async def test_team_fail_by_rng_degrades_to_solo(monkeypatch):
    # 无羁绊（低成功率）+ random=0.999 → 拉不动，退化单刷；只消耗发起人装备
    store = {"groups": {"1001": {"users": {"u1": _equipped_user(points=0), "u2": _equipped_user(points=0)}}}}
    state = _patch_io(monkeypatch, team, store=store)
    monkeypatch.setattr(team, "random", _Rng(0.999))
    monkeypatch.setattr(team, "_roll_fail_flavor", lambda rng=team.random: "hesitate")
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await team.team_cmd.handlers[0](_bot(), _team_event("u1", "u2"))
    g = state["groups"]["1001"]["users"]
    assert g["u1"]["equip_used"] is True and g["u2"]["equip_used"] is False
    assert g["u2"]["exp"] == 0 and g["u2"]["points"] == 0
    assert "独自前往" in str(exc.value.result)
    assert "迟疑" in str(exc.value.result)


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

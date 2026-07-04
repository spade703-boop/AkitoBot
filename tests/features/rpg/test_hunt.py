from __future__ import annotations

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.rpg.config as rpg_config
import nonebot_plugin_akito.features.rpg.fortune as fortune
import nonebot_plugin_akito.features.rpg.hunt as hunt

from .helpers import _PLAIN_BUFF, _bot, _equipped_user, _patch_io, _stub_hunt_rng


def _direct_solo_exp(win: bool, level: int = 1) -> int:
    bonus = hunt._solo_exp_bonus(win)
    return int(hunt._challenge_exp(win, level) * (1.0 + bonus))


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
        "event_slip": ["slip-generic {monster}"],
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


def test_hunt_result_lines_rescue_flip_uses_generic_event_copy(monkeypatch):
    copy_table = {
        "event_desperate": ["desperate-generic"],
        "event_desperate_win": ["desperate-win"],
    }
    orig_cfg = hunt._cfg
    monkeypatch.setattr(hunt, "_cfg", lambda key, default=None: copy_table if key == "copy" else orig_cfg(key, default))
    monkeypatch.setattr(hunt, "_copy", lambda key: copy_table.get(key, [key]))
    monkeypatch.setattr(hunt.random, "choice", lambda seq: seq[0])

    lines = hunt._hunt_result_lines({
        "monster": {"name": "ogre"},
        "event": "desperate",
        "win": True,
        "base_win": False,
        "support_scene": "toya_rescue",
        "exp_gain": 1,
        "points_gain": 1,
        "exp_buffed": False,
        "drops": [],
        "old_level": 1,
        "new_level": 1,
        "buff": _PLAIN_BUFF,
        "support_exp": 0,
        "support_points": 0,
    })

    assert lines[0] == "desperate-generic"


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


def test_default_encounter_brackets_match_monster_pool_length():
    monsters = rpg_config._cfg("monsters", [])
    brackets = rpg_config._cfg("combat", {}).get("encounter_brackets", [])
    assert monsters and brackets
    assert all(len(bracket["weights"]) == len(monsters) for bracket in brackets)


def test_default_encounter_brackets_hold_dragon_until_level_seventeen():
    monsters = rpg_config._cfg("monsters", [])
    dragon_index = next(i for i, monster in enumerate(monsters) if monster.get("name") == "龙")
    assert hunt._encounter_weights(16, len(monsters))[dragon_index] == 0
    assert hunt._encounter_weights(17, len(monsters))[dragon_index] > 0


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


@pytest.mark.asyncio
async def test_hunt_happy_consumes_equip_and_grants_exp(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["equip_used"] is True                       # 今日装备损坏
    assert user["exp"] == _direct_solo_exp(True, 1)         # 主动单刷吃单人经验补偿
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
    assert user["exp"] == _direct_solo_exp(True, 1) * 2 and user["exp_buff_uses"] == 0
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
async def test_hunt_minor_encounter_grants_extra_rewards(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(
        monkeypatch,
        {"name": "史莱姆", "power_req": 1, "drops": []},
        minor_event="supply_cache",
    )

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    spec = hunt._minor_event_spec("supply_cache")
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == _direct_solo_exp(True, 1) + int(spec.get("exp", 0))
    assert user["points"] == hunt._challenge_points(True, user) + int(spec.get("points", 0))
    result = str(exc.value.result)
    assert "补给" in result
    assert f"经验 +{int(spec.get('exp', 0))}" in result
    assert f"积分 +{int(spec.get('points', 0))}" in result


@pytest.mark.asyncio
async def test_hunt_minor_encounter_worn_chest_item_adds_inventory(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(
        monkeypatch,
        {"name": "史莱姆", "power_req": 1, "drops": []},
        minor_event="worn_chest",
        minor_reward={"type": "item", "name": "彰冬无料券", "amount": 1},
    )

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    assert user["inventory"]["彰冬无料券"] == 1
    assert "彰冬无料券 ×1" in str(exc.value.result)


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


def test_apply_rewards_halves_exp_for_rebought_equip():
    equip_cfg = rpg_config._cfg("equip", {})
    mult = float(equip_cfg.get("rebuy_exp_mult", equip_cfg.get("rebuy_points_mult", 0.5)))
    user = _equipped_user(exp=0, points=0, equip_rebought=True)
    out = hunt._apply_rewards(user, "2026-06-22", win=True, monster={"name": "史莱姆", "drops": []})
    assert out["exp_gain"] == int(hunt._challenge_exp(True, 1) * mult)
    assert user["exp"] == out["exp_gain"]


def test_settle_solo_rookie_bonus_only_applies_to_solo(monkeypatch):
    factors: list[float] = []
    reward_kwargs: list[dict] = []
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
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_roll_coop_event", lambda rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(hunt, "resolve_hunt", _resolve)
    monkeypatch.setattr(
        hunt,
        "_apply_rewards",
        lambda *args, **kwargs: (reward_kwargs.append(dict(kwargs)) or dict(reward)),
    )

    hunt._settle_solo(_equipped_user(exp=0, equip_level=1), "D", direct=True)
    hunt._settle_solo(_equipped_user(exp=0, equip_level=1), "D")
    hunt._settle_coop(_equipped_user(exp=0, equip_level=1), _equipped_user(exp=0, equip_level=1), "D")

    assert factors[0] == pytest.approx(1.08 * (1.0 + float(rpg_config._cfg("solo", {}).get("power_bonus", 0.0))))
    assert factors[1] == pytest.approx(1.08)
    assert factors[2] == pytest.approx(1.0 + float(rpg_config._cfg("team", {}).get("power_bonus", 0.0)))
    assert reward_kwargs[0]["exp_bonus"] == pytest.approx(float(rpg_config._cfg("solo", {}).get("win_exp_bonus", 0.0)))
    assert reward_kwargs[1]["exp_bonus"] == pytest.approx(0.0)
    assert reward_kwargs[2]["exp_bonus"] == pytest.approx(0.0)
    assert reward_kwargs[3]["exp_bonus"] == pytest.approx(0.0)


def test_roll_solo_support_scene_uses_fixed_three_percent_bands():
    class _SeqRng:
        def __init__(self, val):
            self.val = val

        def random(self):
            return self.val

    assert hunt._roll_solo_support_scene(True, _SeqRng(0.0)) == "akito_success"
    assert hunt._roll_solo_support_scene(True, _SeqRng(0.04)) == ""
    assert hunt._roll_solo_support_scene(False, _SeqRng(0.00)) == "akito_fail"
    assert hunt._roll_solo_support_scene(False, _SeqRng(0.04)) == "toya_rescue"
    assert hunt._roll_solo_support_scene(False, _SeqRng(0.08)) == "duo_combo"
    assert hunt._roll_solo_support_scene(False, _SeqRng(0.12)) == ""


@pytest.mark.asyncio
async def test_hunt_support_akito_success_adds_bonus_rewards(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "akito_success")

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    bonus_exp = hunt._support_bonus_exp("akito_success", user, 1)
    bonus_points = hunt._support_bonus_points("akito_success", user)
    assert user["exp"] == _direct_solo_exp(True, 1) + bonus_exp
    assert user["points"] == hunt._challenge_points(True, user) + bonus_points
    result = str(exc.value.result)
    assert "真·龙王烈火斩" in result
    assert f"额外获得经验 +{bonus_exp}" in result


@pytest.mark.asyncio
async def test_hunt_support_akito_fail_keeps_failure_with_bonus(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "座狼", "power_req": 999, "drops": []})
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "akito_fail")

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    bonus_exp = hunt._support_bonus_exp("akito_fail", user, 1)
    bonus_points = hunt._support_bonus_points("akito_fail", user)
    assert user["exp"] == _direct_solo_exp(False, 1) + bonus_exp
    assert user["points"] == hunt._challenge_points(False, user) + bonus_points
    result = str(exc.value.result)
    assert "未能击败【座狼】" in result
    assert "反手补上一剑" in result


@pytest.mark.asyncio
async def test_hunt_support_toya_rescue_turns_loss_into_win(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "座狼", "power_req": 999, "drops": []})
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "desperate")
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "toya_rescue")

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    assert user["exp"] == _direct_solo_exp(True, 1)
    assert user["points"] == hunt._challenge_points(True, user)
    result = str(exc.value.result)
    assert any(
        text in result
        for text in (
            "转机却在最后一刻出现了",
            "局势却忽然有了变化",
            "等来了转机",
            "一线生机",
        )
    )
    assert "本次挑战转为成功" in result
    assert "已击败【座狼】" in result
    assert "成功反败为胜" not in result


@pytest.mark.asyncio
async def test_hunt_support_duo_combo_turns_loss_into_win_with_bonus(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user(points=0)}}}})
    _stub_hunt_rng(monkeypatch, {"name": "食人魔", "power_req": 999, "drops": []})
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: "desperate")
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "duo_combo")

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    user = state["groups"]["1001"]["users"]["u1"]
    bonus_exp = hunt._support_bonus_exp("duo_combo", user, 1)
    bonus_points = hunt._support_bonus_points("duo_combo", user)
    assert user["exp"] == _direct_solo_exp(True, 1) + bonus_exp
    assert user["points"] == hunt._challenge_points(True, user) + bonus_points
    result = str(exc.value.result)
    assert any(
        text in result
        for text in (
            "转机却在最后一刻出现了",
            "局势却忽然有了变化",
            "等来了转机",
            "一线生机",
        )
    )
    assert "双色发神官在远处施放了支援魔法稳住阵型" in result
    assert "本次挑战转为成功" in result
    assert "成功反败为胜" not in result


@pytest.mark.asyncio
async def test_hunt_records_battle_stats(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})  # 必胜
    with pytest.raises(FinishedException):
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    assert user["hunt_total"] == 1 and user["hunt_wins"] == 1


@pytest.mark.asyncio
async def test_hunt_elite_boosts_rewards_and_reveals(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "哥布林", "power_req": 1, "drops": []}, elite=True)
    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))
    user = state["groups"]["1001"]["users"]["u1"]
    elite_mult = float(rpg_config._cfg("combat", {})["elite"]["exp_mult"])
    assert user["exp"] == int(_direct_solo_exp(True, 1) * elite_mult)  # 主动单刷补偿后再吃精英倍率
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
    assert user["exp"] == int(_direct_solo_exp(True, 1) * 1.5)               # 主动单刷补偿后再吃今日增益
    assert "经验涌动日" in str(exc.value.result)


@pytest.mark.asyncio
async def test_hunt_appends_world_boss_spawn_lines(monkeypatch):
    state = _patch_io(monkeypatch, hunt, store={"groups": {"1001": {"users": {"u1": _equipped_user()}}}})
    _stub_hunt_rng(monkeypatch, {"name": "史莱姆", "power_req": 1, "drops": []})
    monkeypatch.setattr(hunt, "_maybe_spawn_world_boss_lines", lambda *args, **kwargs: ["世界BOSS出现"])

    with pytest.raises(FinishedException) as exc:
        await hunt.hunt_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="u1"))

    assert state["groups"]["1001"]["users"]["u1"]["equip_used"] is True
    assert "世界BOSS出现" in str(exc.value.result)

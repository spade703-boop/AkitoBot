from __future__ import annotations

from copy import deepcopy
import types

from nonebot.adapters import Bot, Event

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.hunt as hunt


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
    """组队成功掷骰 + 文案随机选用的桩：random() 返回固定值、choice 取首项。"""

    def __init__(self, r):
        self._r = r

    def random(self):
        return self._r

    def choice(self, seq):
        return seq[0]


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
    base = {
        "exp": 0,
        "equip_date": "2026-06-22",
        "equip_level": 1,
        "equip_roll": 0,
        "equip_used": False,
        "equip_forge": 0,
    }
    base.update(extra)
    return base


def _world_boss_record(**extra):
    base = {
        "date": "2026-06-22",
        "name": "深渊巨像",
        "max_hp": 120,
        "hp": 120,
        "recent_active_count": 6,
        "scale_count": 6,
        "reward_scale_count": 6,
        "avg_level": 2,
        "avg_power": 18,
        "contributors": {},
        "participants": {},
        "spawned_by": "u0",
    }
    base.update(extra)
    return base


_PLAIN_BUFF = {"key": "plain", "name": "平日", "exp_mult": 1.0, "drop_mult": 1.0}


_MISSING = object()


def _stub_hunt_rng(monkeypatch, monster, *, event="", drops=None, elite=False, buff=None,
                   minor_event="", minor_reward=_MISSING):
    # 遭遇桩：精英默认关、今日增益默认平日 → 既有用例保持确定（数值不被随机精英/增益扰动）
    monkeypatch.setattr(hunt, "_pick_encounter", lambda level, rng=hunt.random: (monster, elite))
    monkeypatch.setattr(hunt, "_roll_hunt_event", lambda margin, rng=hunt.random: event)
    monkeypatch.setattr(hunt, "_roll_solo_support_scene", lambda win, rng=hunt.random: "")
    monkeypatch.setattr(hunt, "_roll_minor_encounter", lambda win, team=False, rng=hunt.random: minor_event)
    monkeypatch.setattr(hunt.random, "uniform", lambda _a, _b: 1.0)
    monkeypatch.setattr(hunt, "_roll_drops", lambda m, rng=hunt.random, mult=1.0: list(drops or []))
    monkeypatch.setattr(hunt, "_today_buff", lambda: buff or _PLAIN_BUFF)
    if minor_reward is not _MISSING:
        monkeypatch.setattr(hunt, "_roll_minor_reward", lambda spec, rng=hunt.random: dict(minor_reward))

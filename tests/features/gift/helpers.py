from __future__ import annotations

from copy import deepcopy
import types

from nonebot.adapters import Bot

import nonebot_plugin_akito.features.gift as gift


def _at(qq):
    return types.SimpleNamespace(type="at", data={"qq": str(qq)})


def _bot():
    return Bot(self_id="114514")


async def _no_delay():
    return None


class _FixedRNG:
    """randint 恒返回定值，用于负羁绊随机扣除的确定性测试。"""

    def __init__(self, val):
        self.val = val

    def randint(self, _a, _b):
        return self.val


def _g0() -> dict:
    return gift._gift_list()[0]


def _top() -> dict:
    return gift._gift_list()[-1]


def _patch_runtime(monkeypatch, *, today: str = "2026-06-22", store: dict | None = None):
    state = gift._normalize_data(store or {})
    monkeypatch.setattr(gift, "_today_str", lambda: today)
    monkeypatch.setattr(gift, "is_sleeping", lambda: False)
    monkeypatch.setattr(gift, "_sign_in_delay", _no_delay)

    def _load():
        return deepcopy(state)

    def _save(data):
        state.clear()
        state.update(deepcopy(gift._normalize_data(data)))

    monkeypatch.setattr(gift, "_load_data", _load)
    monkeypatch.setattr(gift, "_save_data", _save)
    return state


def _steal_group(thief_pts=0, victim_pts=200, bond=0):
    group = gift._new_group()
    gift._get_user(group, "T")["points"] = thief_pts
    gift._get_user(group, "V")["points"] = victim_pts
    if bond:
        gift._add_intimacy(group, "T", "V", bond)
    return group

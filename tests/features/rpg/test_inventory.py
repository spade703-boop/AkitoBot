from __future__ import annotations

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.rpg.inventory as inventory

from .helpers import _at, _bot, _patch_io


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

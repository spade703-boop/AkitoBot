"""测试 gift.py 的送礼系统：纯逻辑函数 + 指令行为。"""

from __future__ import annotations

from copy import deepcopy
import types

from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift


def _at(qq):
    return types.SimpleNamespace(type="at", data={"qq": str(qq)})


def _bot():
    return Bot(self_id="114514")


def _patch_runtime(monkeypatch, *, today: str = "2026-06-22", store: dict | None = None):
    state = gift._normalize_data(store or {})
    monkeypatch.setattr(gift, "_today_str", lambda: today)

    def _load():
        return deepcopy(state)

    def _save(data):
        state.clear()
        state.update(deepcopy(gift._normalize_data(data)))

    monkeypatch.setattr(gift, "_load_data", _load)
    monkeypatch.setattr(gift, "_save_data", _save)
    return state


# ==================== 纯逻辑：礼物挑选 ====================

def test_affordable_gifts_filters_by_points():
    assert gift._affordable_gifts(0) == []
    assert [g["name"] for g in gift._affordable_gifts(30)] == ["彰冬谷子"]
    assert len(gift._affordable_gifts(1000)) == len(gift._gift_list())


def test_pick_gift_returns_none_when_broke():
    assert gift._pick_gift(0) is None
    assert gift._pick_gift(30)["name"] == "彰冬谷子"  # 只有谷子买得起


def test_cheapest_gift():
    assert gift._cheapest_gift()["name"] == "彰冬谷子"


def test_is_special_gift():
    top = gift._gift_list()[-1]
    assert gift._is_special_gift(top) is True
    assert gift._is_special_gift(gift._gift_list()[0]) is False


# ==================== 纯逻辑：亲密度 ====================

def test_pair_key_is_order_independent():
    assert gift._pair_key("9", "10") == gift._pair_key("10", "9")
    assert gift._pair_key("9", "10") == "10|||9"  # 字符串排序


def test_add_and_get_intimacy_accumulates():
    group = gift._new_group()
    assert gift._add_intimacy(group, "A", "B", 10) == 10
    assert gift._add_intimacy(group, "B", "A", 5) == 15  # 无方向累加
    assert gift._get_intimacy(group, "A", "B") == 15
    assert gift._get_intimacy(group, "A", "C") == 0


def test_top_partners_sorted_desc():
    group = {"intimacy": {"A|||B": 5, "A|||C": 10, "B|||C": 3}, "users": {}}
    assert gift._top_partners(group, "A") == [("C", 10), ("B", 5)]


# ==================== 纯逻辑：随机抽取 ====================

def test_weighted_choice_respects_zero_weights():
    assert gift._weighted_choice({"x": 0, "y": 5, "z": 0}, __import__("random")) == "y"


def test_roll_main_event_uses_config_weights(monkeypatch):
    monkeypatch.setitem(gift.GIFT_CONFIG, "event_weights", {"crit": 1})
    assert gift._roll_main_event() == "crit"


def test_roll_mishap_only_damaged_or_allergy(monkeypatch):
    # 配置里意外子事件只剩 damaged / allergy
    assert set(gift._cfg("mishap_weights")) == {"damaged", "allergy"}


# ==================== 纯逻辑：结算 _settle ====================

def _g0():
    return gift._gift_list()[0]  # 彰冬谷子 cost30 base10


def test_settle_normal_adds_base_intimacy():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "normal", None)
    assert out["amount"] == 10
    assert gift._get_intimacy(group, "A", "B") == 10


def test_settle_crit_doubles():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "crit", None)
    assert out["amount"] == 20
    assert gift._get_intimacy(group, "A", "B") == 20


def test_settle_return_sets_return_gift():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "return", None)
    assert out["amount"] == 10
    assert out["return_gift"] == "彰冬谷子"
    assert gift._get_intimacy(group, "A", "B") == 10


def test_settle_fail_no_intimacy():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "fail", None)
    assert out["amount"] == 0
    assert gift._get_intimacy(group, "A", "B") == 0


def test_settle_special_meal_uses_special_intimacy():
    group = gift._new_group()
    top = gift._gift_list()[-1]  # 自己产的彰冬饭
    out = gift._settle(group, "A", "B", top, "special", None)
    assert out["amount"] == gift._cfg("special_intimacy")
    assert gift._get_intimacy(group, "A", "B") == gift._cfg("special_intimacy")


def test_settle_mishap_damaged_refunds_half():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "mishap", "damaged")
    assert out["amount"] == 5
    assert out["refund"] == 15  # 30 * 0.5
    assert gift._get_intimacy(group, "A", "B") == 5
    assert gift._get_user(group, "A")["points"] == 15  # 返还入账


def test_settle_mishap_allergy_refunds_half():
    group = gift._new_group()
    out = gift._settle(group, "A", "B", _g0(), "mishap", "allergy")
    assert out["amount"] == 5
    assert out["refund"] == 15
    assert gift._get_user(group, "A")["points"] == 15


# ==================== 纯逻辑：文案组装 ====================

def test_outcome_copy_key_mapping():
    assert gift._outcome_copy_key({"event": "normal", "mishap": None}) == "normal"
    assert gift._outcome_copy_key({"event": "special", "mishap": None}) == "special"
    assert gift._outcome_copy_key({"event": "mishap", "mishap": "damaged"}) == "mishap_damaged"
    assert gift._outcome_copy_key({"event": "mishap", "mishap": "allergy"}) == "mishap_allergy"


def test_render_with_ats_builds_at_and_text():
    result = str(gift._render_with_ats("{a} 给 {b} 送了【{gift}】+{amount}",
                                       {"a": "1", "b": "2", "gift": "谷子", "amount": 10}))
    assert "[at:1]" in result
    assert "[at:2]" in result
    assert "谷子" in result
    assert "10" in result


def test_normalize_data_tolerates_garbage():
    assert gift._normalize_data("nonsense") == {"schema_version": gift.SCHEMA_VERSION, "groups": {}}
    norm = gift._normalize_data({"groups": {"1001": {"users": {"u": {"points": 5}}, "intimacy": {"a|||b": 7}}}})
    assert norm["groups"]["1001"]["users"]["u"]["points"] == 5
    assert norm["groups"]["1001"]["intimacy"]["a|||b"] == 7


# ==================== 指令：签到 ====================

@pytest.mark.asyncio
async def test_sign_cmd_rejects_private_chat(monkeypatch):
    _patch_runtime(monkeypatch)
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event())
    assert "群里" in str(exc.value.result)


@pytest.mark.asyncio
async def test_sign_cmd_grants_points_then_blocks_same_day(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 50)

    with pytest.raises(FinishedException) as first:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "50" in str(first.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 50

    with pytest.raises(FinishedException) as second:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "今天已经签到过" in str(second.value.result)


# ==================== 指令：送礼 ====================

@pytest.mark.asyncio
async def test_gift_cmd_requires_at_target(monkeypatch):
    _patch_runtime(monkeypatch)
    event = Event(group_id=1001, user_id="10001", original_message=[])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "要 @一位群友" in str(exc.value.result)


@pytest.mark.asyncio
async def test_gift_cmd_rejects_self_and_bot(monkeypatch):
    _patch_runtime(monkeypatch)

    self_event = Event(group_id=1001, user_id="10001", original_message=[_at("10001")])
    with pytest.raises(FinishedException) as self_exc:
        await gift.gift_cmd.handlers[0](_bot(), self_event, Message(""))
    assert "给自己送礼" in str(self_exc.value.result)

    bot_event = Event(group_id=1001, user_id="10001", original_message=[_at("114514")])
    with pytest.raises(FinishedException) as bot_exc:
        await gift.gift_cmd.handlers[0](_bot(), bot_event, Message(""))
    assert "送给我" in str(bot_exc.value.result)


@pytest.mark.asyncio
async def test_gift_cmd_rejects_insufficient_points(monkeypatch):
    _patch_runtime(monkeypatch)  # 新用户 0 积分，买不起最低档
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "不太够" in str(exc.value.result)


@pytest.mark.asyncio
async def test_gift_cmd_happy_path_deducts_and_adds_intimacy(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100}}, "intimacy": {}}}},
    )
    g0 = gift._gift_list()[0]  # 彰冬谷子 30/10
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "normal")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert "彰冬谷子" in result
    assert "[at:10001]" in result and "[at:10002]" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 70  # 100 - 30
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == 10


@pytest.mark.asyncio
async def test_gift_cmd_special_meal_always_special(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 1000}}, "intimacy": {}}}},
    )
    top = gift._gift_list()[-1]  # 自己产的彰冬饭 800/300
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: top)
    # 即便 roll 出别的事件，special 礼物也应强制走 special 分支
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "fail")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert "彰冬饭" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 200  # 1000 - 800
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == gift._cfg("special_intimacy")


@pytest.mark.asyncio
async def test_gift_cmd_blocks_second_gift_same_day(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100, "last_gift": "2026-06-22"}}, "intimacy": {}}}},
    )
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "今天的礼已经送过" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100  # 未扣分

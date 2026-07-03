from __future__ import annotations

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift

from .helpers import _at, _bot, _g0, _patch_runtime, _top


@pytest.mark.asyncio
async def test_gift_cmd_requires_at_target(monkeypatch):
    _patch_runtime(monkeypatch)
    event = Event(group_id=1001, user_id="10001", original_message=[])
    result = await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert result is None  # 无 @ 目标时静默忽略


@pytest.mark.asyncio
async def test_gift_cmd_rejects_self_and_bot(monkeypatch):
    _patch_runtime(monkeypatch)

    self_event = Event(group_id=1001, user_id="10001", original_message=[_at("10001")])
    self_result = await gift.gift_cmd.handlers[0](_bot(), self_event, Message(""))
    assert self_result is None  # @自己时静默忽略

    bot_event = Event(group_id=1001, user_id="10001", original_message=[_at("114514")])
    bot_result = await gift.gift_cmd.handlers[0](_bot(), bot_event, Message(""))
    assert bot_result is None  # @bot 时静默忽略


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
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "normal")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert g0["name"] in result
    assert "[at:10001]" in result and "[at:10002]" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - g0["cost"]
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == g0["intimacy"]
    assert state["groups"]["1001"]["counts"]["10001>10002"] == 1  # 有向送礼次数 +1


@pytest.mark.asyncio
async def test_gift_cmd_return_path_credits_refund(monkeypatch):
    """回礼路径：先扣 cost 再退 refund、羁绊=base+bonus、文案含回赠物名。"""
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "return")
    monkeypatch.setattr(gift, "_roll_return_gift", lambda rng=gift.random: "doujin")
    spec = gift._return_spec("doujin")
    refund = int(g0["cost"] * spec["refund_ratio"])

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert spec["name"] in result  # 文案含回赠物名
    assert "[at:10001]" in result and "[at:10002]" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - g0["cost"] + refund
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == g0["intimacy"] + spec["bonus"]
    assert state["groups"]["1001"]["counts"]["10001>10002"] == 1


@pytest.mark.asyncio
async def test_gift_cmd_special_gift_always_special(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    top = _top()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: top)
    # 即便 roll 出别的事件，special 礼物也应强制走 special 分支
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "fail")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert "婚礼" in result  # 顶档现为婚礼邀请函，走 special_wedding 文案
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - top["cost"]
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == top["intimacy"]


@pytest.mark.asyncio
async def test_gift_cmd_blocks_second_gift_same_day(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000, "last_gift": "2026-06-22"}}, "intimacy": {}}}},
    )
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "今天的礼已经送过" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000  # 未扣分


@pytest.mark.asyncio
async def test_gift_cmd_superuser_ignores_daily_limit(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {su: {"points": 100000, "last_gift": "2026-06-22"}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _p, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "normal")
    # last_gift==today，普通用户会被「今天已送过」拦下；超管照送
    event = Event(group_id=1001, user_id=su, original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert g0["name"] in str(exc.value.result)
    assert state["groups"]["1001"]["users"][su]["points"] == 100000 - g0["cost"]


@pytest.mark.asyncio
async def test_gift_cmd_blocked_during_sleep(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "睡" in str(exc.value.result)
    # 不扣分、不计数
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000
    assert state["groups"]["1001"].get("counts", {}) == {}

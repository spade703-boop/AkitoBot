from __future__ import annotations

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift

from .helpers import _patch_runtime


@pytest.mark.asyncio
async def test_reset_gift_cmd_clears_global_state(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {
            "1001": {"users": {"10001": {"points": 88}}, "intimacy": {"10001|||10002": 20}},
            "1002": {"users": {"10002": {"points": 99}}},
        }},
    )

    with pytest.raises(FinishedException) as exc:
        await gift.reset_cmd.handlers[0](Event(group_id=1001, user_id=su))

    assert "全局" in str(exc.value.result)
    assert state["users"] == {}
    assert state["intimacy"] == {}
    assert state["groups"] == {}


@pytest.mark.asyncio
async def test_reset_signin_cmd_clears_only_today_gate(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {
                "points": 88,
                "last_sign_in": "2026-06-22",
                "fortune_date": "2026-06-22",
                "equip_date": "2026-06-22",
                "equip_used": False,
                "signin_streak": 7,
                "signin_last_date": "2026-06-22",
                "protect_until": 9999.0,
            },
            "10002": {"points": 10, "last_sign_in": "2026-06-21"},
            "10003": {"points": 20, "last_sign_in": "2026-06-22"},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id=su))

    assert "当前群 2 名成员的全局签到闸门" in str(exc.value.result)
    users = state["groups"]["1001"]["users"]
    assert users["10001"]["last_sign_in"] == ""
    assert users["10003"]["last_sign_in"] == ""
    assert users["10002"]["last_sign_in"] == "2026-06-21"
    assert users["10001"]["fortune_date"] == "2026-06-22"
    assert users["10001"]["equip_date"] == "2026-06-22"
    assert users["10001"]["equip_used"] is False
    assert users["10001"]["signin_streak"] == 7
    assert users["10001"]["signin_last_date"] == "2026-06-22"
    assert users["10001"]["protect_until"] == 9999.0


@pytest.mark.asyncio
async def test_reset_signin_cmd_silent_for_non_superuser(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"last_sign_in": "2026-06-22"},
        }, "intimacy": {}}}},
    )
    result = await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert result is None
    assert state["groups"]["1001"]["users"]["10001"]["last_sign_in"] == "2026-06-22"


@pytest.mark.asyncio
async def test_reset_signin_cmd_reports_when_nobody_blocked(monkeypatch):
    su = gift.SUPERUSER_QQ
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"last_sign_in": "2026-06-21"},
            "10002": {"points": 5},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert "还没人被全局签到闸门卡住" in str(exc.value.result)


@pytest.mark.asyncio
async def test_reset_steal_cmd_clears_only_today_gate(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {
                "points": 88,
                "steal_date": "2026-06-22",
                "steal_used": 2,
                "robbed_date": "2026-06-22",
                "robbed_count": 1,
                "protect_until": 9999.0,
                "last_sign_in": "2026-06-22",
            },
            "10002": {
                "points": 30,
                "steal_date": "2026-06-21",
                "steal_used": 1,
                "robbed_date": "2026-06-22",
                "robbed_count": 3,
            },
            "10003": {"points": 20},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_steal_cmd.handlers[0](Event(group_id=1001, user_id=su))

    assert "当前群 2 名成员的全局偷取/被偷闸门" in str(exc.value.result)
    users = state["groups"]["1001"]["users"]
    assert users["10001"]["steal_date"] == ""
    assert users["10001"]["steal_used"] == 0
    assert users["10001"]["robbed_date"] == ""
    assert users["10001"]["robbed_count"] == 0
    assert users["10002"]["steal_date"] == "2026-06-21"
    assert users["10002"]["steal_used"] == 1
    assert users["10002"]["robbed_date"] == ""
    assert users["10002"]["robbed_count"] == 0
    assert users["10001"]["protect_until"] == 9999.0
    assert users["10001"]["last_sign_in"] == "2026-06-22"


@pytest.mark.asyncio
async def test_reset_steal_cmd_silent_for_non_superuser(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"steal_date": "2026-06-22", "steal_used": 2},
        }, "intimacy": {}}}},
    )
    result = await gift.reset_steal_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert result is None
    assert state["groups"]["1001"]["users"]["10001"]["steal_date"] == "2026-06-22"
    assert state["groups"]["1001"]["users"]["10001"]["steal_used"] == 2


@pytest.mark.asyncio
async def test_reset_steal_cmd_reports_when_nobody_blocked(monkeypatch):
    su = gift.SUPERUSER_QQ
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"steal_date": "2026-06-21", "steal_used": 2},
            "10002": {"points": 5},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_steal_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert "还没人被全局偷取次数卡住" in str(exc.value.result)

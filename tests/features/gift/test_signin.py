from __future__ import annotations

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift

from .helpers import _patch_runtime


@pytest.mark.asyncio
async def test_sign_cmd_rejects_private_chat(monkeypatch):
    _patch_runtime(monkeypatch)
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event())
    assert "群里" in str(exc.value.result)


@pytest.mark.asyncio
async def test_sign_cmd_grants_then_silent_on_repeat(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 70)

    # 首次签到：正常应答 + 入账
    with pytest.raises(FinishedException) as first:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "70" in str(first.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 70


@pytest.mark.asyncio
async def test_sign_cmd_is_silent_across_groups_after_global_signin(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 70)

    with pytest.raises(FinishedException):
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    result = await gift.sign_cmd.handlers[0](Event(group_id=1002, user_id="10001"))

    assert result is None
    assert state["users"]["10001"]["points"] == 70
    assert "10001" in state["groups"]["1002"]["user_ids"]

    # 当天重复签到：静默（不抛 finish、不改积分）
    result = await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert result is None
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 70


@pytest.mark.asyncio
async def test_sign_cmd_superuser_unlimited(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    # 超管同一天连签两次都正常入账（不被限制、不静默）
    for _ in range(2):
        with pytest.raises(FinishedException):
            await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert state["groups"]["1001"]["users"][su]["points"] == 120


@pytest.mark.asyncio
async def test_sign_cmd_blocked_during_sleep(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "睡" in str(exc.value.result)
    # 睡眠时段不入账、不写库
    assert state["groups"].get("1001", {}).get("users", {}).get("10001", {}).get("points", 0) == 0


@pytest.mark.asyncio
async def test_superuser_bypasses_sleep(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    # 超管深夜仍可签到（不被睡眠拦截）
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert "60" in str(exc.value.result)
    assert state["groups"]["1001"]["users"][su]["points"] == 60


@pytest.mark.asyncio
async def test_sign_cmd_sets_protect_until(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    monkeypatch.setattr(gift.time, "time", lambda: 1000.0)
    with pytest.raises(FinishedException):
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    pm = gift._steal_cfg()["protect_minutes"]
    assert state["groups"]["1001"]["users"]["10001"]["protect_until"] == 1000.0 + pm * 60

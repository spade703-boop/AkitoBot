from __future__ import annotations

import asyncio

import pytest

from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features.rpg import equipment, hunt, inventory, profile, reporting, supply, team, world_boss


def test_rpg_write_paths_share_the_game_store_lock():
    locks = [
        hunt.command.LOCK,
        team.team.LOCK,
        supply.supply.LOCK,
        equipment.smith.LOCK,
        inventory.inventory.LOCK,
        profile.character.LOCK,
        reporting.analytics.LOCK,
        world_boss.command.LOCK,
    ]
    assert all(lock is game_store.LOCK for lock in locks)


@pytest.mark.asyncio
async def test_shared_lock_serializes_concurrent_writers():
    active = 0
    max_active = 0

    async def writer():
        nonlocal active, max_active
        async with game_store.LOCK:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(*(writer() for _ in range(8)))

    assert max_active == 1

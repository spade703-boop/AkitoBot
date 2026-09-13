from __future__ import annotations

from copy import deepcopy

from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features.rpg.world_boss import settlement

from .helpers import _world_boss_record


def test_legacy_gift_data_round_trips_with_group_rpg_state():
    legacy = {
        "users": {"u1": {"points": 12, "display_name": "旧用户"}},
        "intimacy": {"u1|u2": 7},
        "groups": {
            "1001": {
                "user_ids": ["u1", "u2"],
                "users": {"u1": {"exp": 15}},
                "rpg": {"world_boss": {"date": "2026-06-22", "hp": 9}},
            }
        },
    }

    normalized = game_store._normalize_data(legacy)
    serialized = game_store._serializable_data(normalized)
    restored = game_store._normalize_data(serialized)

    assert restored["users"]["u1"]["points"] == 12
    assert restored["groups"]["1001"]["rpg"]["world_boss"]["hp"] == 9
    assert restored["groups"]["1001"]["user_ids"] == ["u1", "u2"]


def test_stale_world_boss_cleanup_is_idempotent_after_first_settlement():
    group = game_store._new_group()
    group["users"]["u1"] = {"exp": 0, "points": 0, "display_name": "用户一"}
    group["rpg"] = {
        "world_boss": _world_boss_record(
            date="2026-06-21",
            max_hp=100,
            hp=70,
            contributors={"u1": 30},
        )
    }

    first_lines, first_changed = settlement._cleanup_stale_world_boss(group, "2026-06-22")
    reward_snapshot = deepcopy(group["users"]["u1"])
    second_lines, second_changed = settlement._cleanup_stale_world_boss(group, "2026-06-22")

    assert first_changed is True
    assert first_lines
    assert second_changed is False
    assert second_lines == []
    assert group["users"]["u1"] == reward_snapshot

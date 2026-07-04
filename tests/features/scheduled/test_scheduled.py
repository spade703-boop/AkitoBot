"""测试 scheduled.py 中抽出的定时任务辅助函数。"""

from __future__ import annotations

from copy import deepcopy
from unittest import mock

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.scheduled as scheduled
from tests.features.rpg.helpers import _world_boss_record


def test_clean_memory_db_prunes_expired_implants_in_place():
    memory_db = {
        "group_1": {
            "temp_implants": [
                {"content": "保留", "expire_at": 200.0},
                {"content": "过期", "expire_at": 50.0},
            ]
        },
        "group_2": {"history": []},
    }

    cleaned = scheduled._clean_memory_db(memory_db, now_ts=100.0)

    assert cleaned == 1
    assert memory_db["group_1"]["temp_implants"] == [{"content": "保留", "expire_at": 200.0}]


def test_get_scheduled_greeting_uses_defaults_and_custom_pool():
    with mock.patch.object(scheduled.random, "choice", side_effect=lambda seq: seq[0]):
        custom = scheduled._get_scheduled_greeting("morning", {"greetings": {"morning": ["起床。"]}})
        fallback = scheduled._get_scheduled_greeting("night", {"greetings": {}})

    assert custom == "起床。"
    assert fallback == "晚安。"


def test_collect_world_boss_settlements_only_broadcasts_stale_unfinished_bosses():
    data = {"groups": {}}
    group_1001 = game_store._new_group()
    group_1001["users"]["u1"] = {"exp": 0, "points": 0, "display_name": "阿一"}
    group_1001["rpg"] = {
        "world_boss": deepcopy(
            _world_boss_record(
                date="2026-07-04",
                max_hp=200,
                hp=120,
                reward_scale_count=3,
                contributors={"u1": 80},
            )
        )
    }
    data["groups"]["1001"] = group_1001

    group_1002 = game_store._new_group()
    data["groups"]["1002"] = group_1002

    with mock.patch.object(scheduled, "_save_data") as save_data:
        broadcasts = scheduled._collect_world_boss_settlements(data, "2026-07-05", [1001, 1002])

    assert len(broadcasts) == 1
    assert broadcasts[0][0] == 1001
    assert "已经离场" in broadcasts[0][1]
    assert "world_boss" not in data["groups"]["1001"]["rpg"]
    assert save_data.called is True


def test_collect_world_boss_settlements_skips_groups_without_stale_boss():
    data = {"groups": {}}
    group_1001 = game_store._new_group()
    data["groups"]["1001"] = group_1001

    with mock.patch.object(scheduled, "_save_data") as save_data:
        broadcasts = scheduled._collect_world_boss_settlements(data, "2026-07-05", [1001])

    assert broadcasts == []
    save_data.assert_not_called()

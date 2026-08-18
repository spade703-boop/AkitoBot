from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features.rpg.state import _rpg_state
from nonebot_plugin_akito.features.rpg.team import _team_bond_daily_pairs


def test_rpg_state_preserves_existing_mapping_and_nested_records():
    group = game_store._new_group()
    world_boss = {"hp": 7}
    rpg = {"world_boss": world_boss}
    group["rpg"] = rpg

    state = _rpg_state(group)

    assert state is rpg
    assert state["world_boss"] is world_boss


def test_rpg_state_repairs_invalid_state_in_place():
    group = game_store._new_group()
    group["rpg"] = None

    state = _rpg_state(group)

    assert state == {}
    assert group["rpg"] is state


def test_team_bond_daily_pairs_repairs_invalid_pairs_without_copying_state():
    group = game_store._new_group()
    group["rpg"] = {"team_bond_daily": {"date": "2026-06-22", "pairs": "invalid"}}

    pairs = _team_bond_daily_pairs(group, "2026-06-22")

    assert pairs == {}
    assert group["rpg"]["team_bond_daily"]["pairs"] is pairs

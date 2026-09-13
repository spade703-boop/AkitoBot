from __future__ import annotations

from nonebot_plugin_akito.core import game_store
from nonebot_plugin_akito.features import rpg


def test_rpg_bootstrap_registers_unique_commands_and_signin_hook_once():
    modules = (
        rpg.equipment.smith,
        rpg.hunt.command,
        rpg.hunt.drop_test,
        rpg.inventory.inventory,
        rpg.team.team,
        rpg.reporting.analytics,
        rpg.reporting.command,
        rpg.world_boss.command,
        rpg.profile.character,
    )
    matchers = []
    for module in modules:
        for value in vars(module).values():
            if hasattr(value, "handlers") and hasattr(value, "args") and value not in matchers:
                matchers.append(value)

    assert len(matchers) == 20
    command_names = [matcher.args[0] for matcher in matchers]
    assert len(set(command_names)) == len(command_names)
    assert game_store.SIGNIN_HOOKS.count(rpg.on_signin) == 1

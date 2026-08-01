from copy import deepcopy

import pytest

import nonebot_plugin_akito.features.rpg.config as rpg_config


def test_default_rpg_config_passes_strict_validation():
    rpg_config.validate_rpg_config(deepcopy(rpg_config.DEFAULT_RPG_CONFIG))


def test_config_rejects_legacy_encounter_weights_that_do_not_match_monster_pool():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    config["combat"]["encounter_brackets"][0]["weights"] = [1] * (len(config["monsters"]) - 1)

    with pytest.raises(rpg_config.RpgConfigError, match="怪物池有"):
        rpg_config.validate_rpg_config(config)


def test_config_rejects_named_encounter_weight_for_unknown_monster():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    config["combat"]["encounter_brackets"][0]["weights"]["不存在的怪物"] = 1

    with pytest.raises(rpg_config.RpgConfigError, match="不存在的怪物"):
        rpg_config.validate_rpg_config(config)


def test_config_rejects_unbounded_monster_exp_multiplier():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    config["monsters"][-1]["reward_exp_mult"] = 3.0

    with pytest.raises(rpg_config.RpgConfigError, match="reward_exp_mult"):
        rpg_config.validate_rpg_config(config)


def test_config_rejects_invalid_supply_cost_count():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    config["adventure_supply"]["weekly_costs"] = [140] * 5

    with pytest.raises(rpg_config.RpgConfigError, match="7 次成本"):
        rpg_config.validate_rpg_config(config)


def test_config_rejects_supply_pool_weights_not_equal_to_one_hundred():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    config["adventure_supply"]["pool"][0]["weight"] = 33

    with pytest.raises(rpg_config.RpgConfigError, match="权重总和"):
        rpg_config.validate_rpg_config(config)


def test_config_rejects_scallion_cake_multiplier_above_one():
    config = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    cake = next(item for item in config["items"] if item["name"] == "大葱味蛋糕")
    cake["effect"]["exp_mult"] = 1.15

    with pytest.raises(rpg_config.RpgConfigError, match="不高于 1"):
        rpg_config.validate_rpg_config(config)


def test_failed_hot_reload_keeps_current_config(monkeypatch):
    previous = deepcopy(rpg_config.RPG_CONFIG)
    invalid = deepcopy(rpg_config.DEFAULT_RPG_CONFIG)
    invalid["world_boss"]["spawn_chance"] = 1.5
    monkeypatch.setattr(rpg_config, "load_json_file", lambda _filename, _default: invalid)

    with pytest.raises(rpg_config.RpgConfigError, match="spawn_chance"):
        rpg_config.reload_rpg_config()

    assert previous == rpg_config.RPG_CONFIG

from nonebot_plugin_akito.features.rpg.simulation import (
    growth_baseline_violations,
    simulate_solo_growth,
)


def test_solo_growth_simulation_is_reproducible_and_reports_baseline_status():
    first = simulate_solo_growth(days=180, runs=160, seed=20260727)
    second = simulate_solo_growth(days=180, runs=160, seed=20260727)

    assert first == second
    assert set(first["checkpoints"]) == {"30", "90", "180"}
    assert all("目标为" in violation for violation in growth_baseline_violations(first))
    assert 0.0 < first["win_rate"] < 1.0


def test_long_growth_simulation_reports_level_30_arrival_window():
    result = simulate_solo_growth(days=360, runs=40, seed=20260727)
    level_30 = result["level_30"]

    assert set(result["checkpoints"]) == {"30", "90", "180", "270", "360"}
    assert level_30["reached_runs"] > 0
    assert 180 < level_30["day_p10"] <= level_30["day_median"] <= level_30["day_p90"] <= 360

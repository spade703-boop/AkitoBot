from __future__ import annotations

import pytest

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.rpg.combat as combat
import nonebot_plugin_akito.features.rpg.events as rpg_events
import nonebot_plugin_akito.features.rpg.friend_support as friend_support
import nonebot_plugin_akito.features.rpg.hunt as hunt
import nonebot_plugin_akito.features.rpg.inventory as inventory
import nonebot_plugin_akito.features.rpg.rewards as rewards

from .helpers import _PLAIN_BUFF, _equipped_user


class _Rng:
    def __init__(self, values=()):
        self.values = list(values)

    def random(self):
        return self.values.pop(0) if self.values else 0.0

    def uniform(self, low, _high):
        return low

    def choices(self, seq, *, weights, k):
        return [seq[0]]

    def choice(self, seq):
        return seq[0]


def _group(*, intimacy=None):
    users = {
        "actor": _equipped_user(display_name="发起人"),
        "helper": _equipped_user(exp=20000, display_name="助力者"),
        "other": _equipped_user(exp=100, display_name="另一位"),
    }
    return {
        "user_ids": list(users),
        "users": users,
        "intimacy": intimacy or {},
    }


def test_roll_friend_support_uses_bond_and_helper_level(monkeypatch):
    group = _group(intimacy={game_store._pair_key("actor", "helper"): 1000})
    monkeypatch.setattr(friend_support, "_friend_support_chance", lambda: 1.0)
    monkeypatch.setattr(friend_support, "_weighted_choice", lambda weights, rng: "helper")

    result = friend_support.roll_friend_support(group, ["actor"], excluded_user_ids=["actor"], rng=_Rng([0.0]))

    assert result is not None
    assert result["helper_id"] == "helper"
    assert result["polarity"] == "positive"
    assert result["helper_level"] > 1
    assert result["power_mult"] == pytest.approx(1.08)
    assert result["exp_mult"] == pytest.approx(1.10)


def test_roll_friend_support_negative_bond_is_negative_for_low_roll(monkeypatch):
    group = _group(intimacy={game_store._pair_key("actor", "helper"): -650})
    monkeypatch.setattr(friend_support, "_friend_support_chance", lambda: 1.0)
    monkeypatch.setattr(friend_support, "_weighted_choice", lambda weights, rng: "helper")

    result = friend_support.roll_friend_support(group, ["actor"], excluded_user_ids=["actor"], rng=_Rng([0.0, 0.0]))

    assert result is not None
    assert result["polarity"] == "negative"
    assert result["power_mult"] == pytest.approx(0.95)
    assert result["exp_mult"] == pytest.approx(0.94)


def test_roll_friend_support_returns_none_without_candidate(monkeypatch):
    group = _group()
    group["user_ids"] = ["actor"]
    group["users"] = {"actor": group["users"]["actor"]}
    monkeypatch.setattr(friend_support, "_friend_support_chance", lambda: 1.0)

    assert friend_support.roll_friend_support(group, ["actor"], excluded_user_ids=["actor"], rng=_Rng([0.0])) is None


def test_roll_friend_support_does_not_roll_without_participants(monkeypatch):
    group = _group()

    class _UnexpectedRng:
        def random(self):
            raise AssertionError("an empty battle cannot trigger friend support")

    monkeypatch.setattr(friend_support, "_friend_support_chance", lambda: 1.0)

    assert friend_support.roll_friend_support(group, [], rng=_UnexpectedRng()) is None


def test_friend_support_render_uses_real_at():
    line = friend_support.render_friend_support_line(
        {
            "helper_id": "helper",
            "helper_name": "助力者",
            "polarity": "positive",
            "power_mult": 1.08,
            "exp_mult": 1.10,
            "points_mult": 1.10,
            "drop_mult": 1.07,
            "rescue_triggered": False,
        },
        target_name="发起人",
        rng=_Rng(),
    )

    assert "[at:helper]" in str(line)
    assert "助力者" in str(line)
    assert "战力 +8%" in str(line)


def test_friend_support_render_uses_failure_copy_for_positive_support():
    line = friend_support.render_friend_support_line(
        {
            "helper_id": "helper",
            "helper_name": "助力者",
            "polarity": "positive",
            "power_mult": 1.08,
            "exp_mult": 1.10,
            "points_mult": 1.10,
            "drop_mult": 1.07,
            "rescue_triggered": False,
        },
        target_name="发起人",
        battle_won=False,
        rng=_Rng(),
    )

    assert "没能扭转败局" in str(line)
    assert "虽已生效" in str(line)


def test_apply_rewards_accepts_points_multiplier(monkeypatch):
    user = _equipped_user(points=0)
    monkeypatch.setattr(inventory, "_roll_drops", lambda *args, **kwargs: [])

    out = rewards._apply_rewards(
        user,
        "2026-06-22",
        win=True,
        monster={"name": "史莱姆", "drops": []},
        points_mult=0.5,
    )

    assert out["points_gain"] == rewards._challenge_points(True, user) // 2


def test_settle_solo_applies_friend_support_before_rewards(monkeypatch):
    group = _group(intimacy={game_store._pair_key("actor", "helper"): 1000})
    monster = {"name": "强敌", "power_req": 1, "drops": []}
    captured = {}
    support = {
        "helper_id": "helper",
        "helper_name": "助力者",
        "polarity": "positive",
        "power_mult": 1.08,
        "exp_mult": 1.10,
        "points_mult": 1.10,
        "drop_mult": 1.07,
        "rescue_chance": 0.0,
        "rescue_triggered": False,
    }
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng: (monster, False))
    monkeypatch.setattr(rpg_events, "_roll_hunt_event", lambda margin, rng: "")
    monkeypatch.setattr(rpg_events, "_roll_solo_support_scene", lambda win, rng: "")
    monkeypatch.setattr(rewards.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(rewards.friend_support, "roll_friend_support", lambda *args, **kwargs: dict(support))
    monkeypatch.setattr(rewards.inventory, "_roll_drops", lambda *args, **kwargs: [])

    def _apply(*args, **kwargs):
        captured.update(kwargs)
        return {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(rewards, "_apply_rewards", _apply)
    out = rewards._settle_solo(
        group["users"]["actor"],
        "2026-06-22",
        group=group,
        participant_ids=["actor"],
        excluded_user_ids=["actor"],
        rng=_Rng(),
    )

    assert out["friend_support"]["helper_id"] == "helper"
    assert captured["exp_mult"] == pytest.approx(1.10)
    assert captured["points_mult"] == pytest.approx(1.10)
    assert captured["drop_mult"] == pytest.approx(1.07)


def test_settle_solo_skips_legacy_support_when_friend_support_hits(monkeypatch):
    group = _group(intimacy={game_store._pair_key("actor", "helper"): 1000})
    support = {
        "helper_id": "helper",
        "helper_name": "助力者",
        "polarity": "positive",
        "power_mult": 1.02,
        "exp_mult": 1.03,
        "points_mult": 1.03,
        "drop_mult": 1.02,
        "rescue_chance": 0.0,
        "rescue_triggered": False,
    }
    monkeypatch.setattr(rewards.friend_support, "roll_friend_support", lambda *args, **kwargs: dict(support))
    monkeypatch.setattr(rpg_events, "_roll_solo_support_scene", lambda *args, **kwargs: pytest.fail("legacy support should be skipped"))
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng: ({"name": "史莱姆", "power_req": 1, "drops": []}, False))
    monkeypatch.setattr(rpg_events, "_roll_hunt_event", lambda margin, rng: "")
    monkeypatch.setattr(rewards.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(rewards.inventory, "_roll_drops", lambda *args, **kwargs: [])

    out = rewards._settle_solo(
        group["users"]["actor"],
        "2026-06-22",
        group=group,
        participant_ids=["actor"],
        excluded_user_ids=["actor"],
        rng=_Rng(),
    )

    assert out["friend_support"]["helper_id"] == "helper"
    assert out["support_scene"] == ""


def test_negative_friend_support_never_uses_rescue_chance(monkeypatch):
    group = _group(intimacy={game_store._pair_key("actor", "helper"): -650})
    support = {
        "helper_id": "helper",
        "helper_name": "助力者",
        "polarity": "negative",
        "power_mult": 1.0,
        "exp_mult": 1.0,
        "points_mult": 1.0,
        "drop_mult": 1.0,
        "rescue_chance": 1.0,
        "rescue_triggered": False,
    }
    monkeypatch.setattr(rewards.friend_support, "roll_friend_support", lambda *args, **kwargs: dict(support))
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng: ({"name": "强敌", "power_req": 999, "drops": []}, False))
    monkeypatch.setattr(rpg_events, "_roll_hunt_event", lambda margin, rng: "")
    monkeypatch.setattr(rewards.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(rewards.inventory, "_roll_drops", lambda *args, **kwargs: [])

    out = rewards._settle_solo(
        group["users"]["actor"],
        "2026-06-22",
        group=group,
        participant_ids=["actor"],
        excluded_user_ids=["actor"],
        rng=_Rng(),
    )

    assert out["win"] is False
    assert out["friend_support"]["rescue_triggered"] is False


def test_settle_coop_applies_one_friend_support_to_both_rewards(monkeypatch):
    group = _group(intimacy={
        game_store._pair_key("actor", "helper"): 1000,
        game_store._pair_key("other", "helper"): 1000,
    })
    support = {
        "helper_id": "helper",
        "helper_name": "助力者",
        "polarity": "positive",
        "power_mult": 1.08,
        "exp_mult": 1.10,
        "points_mult": 1.10,
        "drop_mult": 1.07,
        "rescue_chance": 0.0,
        "rescue_triggered": False,
    }
    captured = []
    monkeypatch.setattr(rewards.friend_support, "roll_friend_support", lambda *args, **kwargs: dict(support))
    monkeypatch.setattr(combat, "_pick_encounter", lambda level, rng: ({"name": "史莱姆", "power_req": 1, "drops": []}, False))
    monkeypatch.setattr(rpg_events, "_roll_coop_event", lambda rng: "")
    monkeypatch.setattr(rewards.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(combat, "_today_buff", lambda: _PLAIN_BUFF)
    monkeypatch.setattr(rewards.inventory, "_roll_drops", lambda *args, **kwargs: [])

    def _apply(*args, **kwargs):
        captured.append(kwargs)
        return {"exp_gain": 0, "exp_buffed": False, "drops": [], "points_gain": 0, "old_level": 1, "new_level": 1}

    monkeypatch.setattr(rewards, "_apply_rewards", _apply)
    out = rewards._settle_coop(
        group["users"]["actor"],
        group["users"]["other"],
        "2026-06-22",
        group=group,
        participant_ids=["actor", "other"],
        excluded_user_ids=["actor", "other"],
        rng=_Rng(),
    )

    assert out["friend_support"]["helper_id"] == "helper"
    assert len(captured) == 2
    assert all(item["exp_mult"] == pytest.approx(1.10) for item in captured)
    assert all(item["points_mult"] == pytest.approx(1.10) for item in captured)
    assert all(item["drop_mult"] == pytest.approx(1.07) for item in captured)


def test_hunt_result_lines_include_friend_support():
    lines = hunt._hunt_result_lines(
        {
            "monster": {"name": "史莱姆"},
            "event": "",
            "win": True,
            "base_win": True,
            "support_scene": "",
            "friend_support": {
                "helper_id": "helper",
                "helper_name": "助力者",
                "polarity": "positive",
                "power_mult": 1.02,
                "exp_mult": 1.03,
                "points_mult": 1.03,
                "drop_mult": 1.02,
            },
            "exp_gain": 1,
            "points_gain": 1,
            "drops": [],
            "old_level": 1,
            "new_level": 1,
            "buff": _PLAIN_BUFF,
        }
    )

    assert any("[at:helper]" in str(line) for line in lines)

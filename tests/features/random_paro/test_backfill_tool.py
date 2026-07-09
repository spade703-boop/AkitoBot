"""Tests for the standalone random_paro history backfill script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_backfill_tool():
    tool_path = Path(__file__).resolve().parents[3] / "tools" / "backfill_paro_stats.py"
    spec = importlib.util.spec_from_file_location("backfill_paro_stats_tool", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_回补脚本_会按个人累计重建历史角色榜与抽数(tmp_path):
    tool = _load_backfill_tool()
    stats_path = tmp_path / "paro_stats.json"
    original = {
        "schema_version": 2,
        "cooldowns": {},
        "groups": {
            "1001": {
                "profiles": {"u1": "测试甲", "u2": "测试乙"},
                "users": {
                    "u1": {
                        "draw_count": 5,
                        "egg_count": 1,
                        "foxbun_count": 2,
                        "akito_hits": {"蛇彰": 4, "厨子": 1},
                        "toya_hits": {"烈火": 3},
                        "pair_hits": {},
                        "akito_last_hit_seq": {"厨子": 4, "蛇彰": 5},
                        "toya_last_hit_seq": {"烈火": 6},
                        "pair_last_hit_seq": {},
                        "_seq": 6,
                    },
                    "u2": {
                        "draw_count": 3,
                        "egg_count": 0,
                        "foxbun_count": 1,
                        "akito_hits": {"蛇彰": 2},
                        "toya_hits": {"王子冬": 3},
                        "pair_hits": {},
                        "akito_last_hit_seq": {"蛇彰": 3},
                        "toya_last_hit_seq": {"王子冬": 5},
                        "pair_last_hit_seq": {},
                        "_seq": 5,
                    },
                },
                "daily": {"date": "2026-07-09", "total_draws": 0},
                "history": {
                    "total_draws": 1,
                    "user_draw_counts": {"u1": 1},
                    "akito_hits": {"厨子": 1},
                    "toya_hits": {"烈火": 1},
                    "akito_last_hit_seq": {"厨子": 9},
                    "toya_last_hit_seq": {"王子冬": 1, "烈火": 8},
                    "egg_user_counts": {"u1": 1},
                    "foxrabbit_total": 0,
                    "foxbun_total": 0,
                    "fox_total": 0,
                    "rabbit_total": 0,
                    "_seq": 9,
                },
            }
        },
    }
    stats_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

    result = tool.backfill_paro_stats_file(stats_path)

    updated = json.loads(stats_path.read_text(encoding="utf-8"))
    history = updated["groups"]["1001"]["history"]

    assert result["groups_changed"] == 1
    assert result["backup_path"] == stats_path.with_name("paro_stats.json.bak")
    assert result["backup_path"].exists()
    assert json.loads(result["backup_path"].read_text(encoding="utf-8")) == original

    assert history["akito_hits"] == {"厨子": 1, "蛇彰": 6}
    assert history["toya_hits"] == {"烈火": 3, "王子冬": 3}
    assert history["user_draw_counts"] == {"u1": 5, "u2": 3}
    assert history["egg_user_counts"] == {"u1": 3, "u2": 1}
    assert history["total_draws"] == 8
    assert history["foxbun_total"] == 3
    assert history["akito_last_hit_seq"] == {"厨子": 1, "蛇彰": 2}
    assert history["toya_last_hit_seq"] == {"王子冬": 1, "烈火": 2}

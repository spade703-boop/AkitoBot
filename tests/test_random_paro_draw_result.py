from __future__ import annotations

import nonebot_plugin_akito.features.random_paro as random_paro


def test_build_draw_result_page_data_single_cooking_summary():
    data = random_paro._build_draw_result_page_data(
        [("Callboy彰", "Callboy冬", True, None)],
        remaining=2,
        nickname="测试群友",
    )

    assert data["pill"] == "本次共 1 抽"
    assert data["quota_text"] == "30 分钟内剩余 2 次"
    assert data["results"][0]["type"] == "pair"
    assert data["results"][0]["cooking"] is True
    assert data["cooking_summary"] == {
        "mode": "single",
        "nickname": "测试群友",
        "akito": "Callboy彰",
        "toya": "Callboy冬",
    }


def test_build_draw_result_page_data_multi_cooking_and_foxbun_summary():
    data = random_paro._build_draw_result_page_data(
        [
            ("白骑", "王子冬", True, None),
            ("Callboy彰", "Callboy冬", False, "foxbun"),
            ("黑百合", "黑骑", True, None),
        ],
        remaining=1,
        nickname="测试群友",
    )

    assert [item["type"] for item in data["results"]] == ["pair", "fox", "pair"]
    assert data["results"][1]["fox_type"] == "foxbun"
    assert data["cooking_summary"] == {
        "mode": "multi",
        "dishes": [
            {"akito": "白骑", "toya": "王子冬"},
            {"akito": "黑百合", "toya": "黑骑"},
        ],
        "with_foxbun": True,
    }

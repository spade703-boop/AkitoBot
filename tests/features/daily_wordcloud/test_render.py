from __future__ import annotations

from unittest import mock

from nonebot_plugin_akito.features.daily_wordcloud import render


def test_build_page_data_adds_avatar_and_initial_for_contributors():
    report = {
        "group_id": "1001",
        "report_date": "2026-08-29",
        "frequencies": [["彰冬", 3]],
        "top_words": [
            {
                "word": "彰冬",
                "count": 3,
                "contributors": [{"user_id": "10001", "nickname": "橘子汽水", "count": 3}],
            }
        ],
    }
    with mock.patch.object(render, "_wordcloud_data_uri", return_value="data:image/png;base64,demo"):
        data = render.build_page_data(report)

    person = data["top_words"][0]["contributors"][0]
    assert person["initial"] == "橘"
    assert person["avatar"].endswith("nk=10001&s=100")


def test_build_page_data_builds_top_five_plus_other_message_volume_chart():
    report = {
        "frequencies": [],
        "top_words": [],
        "message_count": 11,
        "message_volume": [
            {"user_id": f"u{index}", "nickname": f"用户{index}", "count": count}
            for index, count in enumerate((4, 3, 2, 1, 1), start=1)
        ],
    }
    with mock.patch.object(render, "_wordcloud_data_uri", return_value=""):
        data = render.build_page_data(report)

    assert len(data["message_volume"]) == 5
    assert data["message_volume"][0]["percentage"] == "36.4"
    assert data["volume_chart_style"].startswith("background: conic-gradient(")
    assert "其他" not in [item["nickname"] for item in data["message_volume"]]

    report["message_volume"].append({"user_id": "u6", "nickname": "用户6", "count": 1})
    report["message_volume"].append({"user_id": "u7", "nickname": "用户7", "count": 1})
    report["message_volume"].append({"user_id": "u8", "nickname": "用户8", "count": 1})
    with mock.patch.object(render, "_wordcloud_data_uri", return_value=""):
        data = render.build_page_data(report)

    assert data["message_volume"][-1]["nickname"] == "其他"
    assert data["message_volume"][-1]["count"] == 3
    assert data["message_volume"][0]["avatar"].endswith("nk=u1&s=100")


async def test_render_report_delegates_to_html_renderer():
    report = {
        "frequencies": [],
        "top_words": [],
        "report_date": "2026-08-29",
        "message_count": 1,
        "message_volume": [{"user_id": "10001", "nickname": "橘子汽水", "count": 1}],
    }
    with (
        mock.patch.object(render, "_wordcloud_data_uri", return_value=""),
        mock.patch.object(render, "html_to_pic", new=mock.AsyncMock(return_value=b"jpeg")) as html_to_pic,
    ):
        image = await render.render_report(report)

    assert image == b"jpeg"
    html_to_pic.assert_awaited_once()
    html = html_to_pic.await_args.args[0]
    assert "volume-hole" not in html
    assert "TOP 5 + 其他" in html
    assert "nk=10001&amp;s=100" in html

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
    assert data["unique_word_count"] == 1


async def test_render_report_delegates_to_html_renderer():
    report = {"frequencies": [], "top_words": [], "report_date": "2026-08-29"}
    with (
        mock.patch.object(render, "_wordcloud_data_uri", return_value=""),
        mock.patch.object(render, "html_to_pic", new=mock.AsyncMock(return_value=b"jpeg")) as html_to_pic,
    ):
        image = await render.render_report(report)

    assert image == b"jpeg"
    html_to_pic.assert_awaited_once()

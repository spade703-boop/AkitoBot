"""卡面别称维护指令参数测试。"""

from nonebot_plugin_akito.features.card import commands


def test_parse_card_alias_binding_uses_last_field_as_target():
    assert commands._parse_card_alias_binding("烈火 彰14") == ("烈火", "彰14")
    assert commands._parse_card_alias_binding("火焰 彰 卡 759") == ("火焰 彰 卡", "759")
    assert commands._parse_card_alias_binding("只有别称") is None


def test_parse_card_alias_note_and_group_targets():
    assert commands._split_alias_note("老蛇 彰21 | 因花后眼神像蛇") == (
        "老蛇 彰21",
        "因花后眼神像蛇",
    )
    assert commands._parse_card_group_binding("烈火 杏17、心羽17、冬17") == (
        "烈火",
        ["杏17", "心羽17", "冬17"],
    )

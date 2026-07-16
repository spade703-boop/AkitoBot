from __future__ import annotations

from nonebot_plugin_akito.core import game_store
import nonebot_plugin_akito.features.gift as gift

from .helpers import _g0, _top


def test_affordable_gifts_filters_by_points():
    assert gift._affordable_gifts(0) == []
    first = _g0()
    assert [g["name"] for g in gift._affordable_gifts(first["cost"])] == [first["name"]]
    assert len(gift._affordable_gifts(10**9)) == len(gift._gift_list())


def test_pick_gift_returns_none_when_broke():
    assert gift._pick_gift(0) is None
    first = _g0()
    assert gift._pick_gift(first["cost"])["name"] == first["name"]  # 只买得起最低档


def test_pick_gift_weighted_prefers_pricier():
    """买得起的礼按档位序号加权（越贵权重越大），且候选只含买得起的。"""

    class _RNG:
        def __init__(self):
            self.seen = None

        def choices(self, population, weights=None, k=1):
            self.seen = (population, weights)
            return [population[-1]]

    rng = _RNG()
    # 用一个能买得起前几档（但买不起顶档）的积分
    budget = gift._gift_list()[2]["cost"]
    picked = gift._pick_gift(budget, rng=rng)

    affordable = [g["name"] for g in gift._affordable_gifts(budget)]
    assert [g["name"] for g in rng.seen[0]] == affordable
    assert rng.seen[1] == list(range(1, len(affordable) + 1))  # 权重按档位递增
    assert picked["name"] == affordable[-1]  # 末位（由桩 rng 决定）


def test_pick_gift_can_exclude_wedding_invitation_before_draw():
    class _RNG:
        def __init__(self):
            self.population = []

        def choices(self, population, weights=None, k=1):
            self.population = list(population)
            return [population[-1]]

    rng = _RNG()
    wedding_name = gift._wedding_cfg()["gift_name"]
    picked = gift._pick_gift(10**9, rng=rng, excluded_names={wedding_name})

    assert all(g["name"] != wedding_name for g in rng.population)
    assert picked["name"] == gift._gift_list()[-2]["name"]


def test_pick_gift_uses_independent_forty_percent_wedding_roll():
    class _RNG:
        def __init__(self, roll):
            self.roll = roll

        def random(self):
            return self.roll

        def choices(self, population, weights=None, k=1):
            return [population[-1]]

    wedding_name = gift._wedding_cfg()["gift_name"]
    assert gift._wedding_cfg()["chance"] == 0.40
    assert gift._pick_gift(1112, rng=_RNG(0.399))["name"] == wedding_name
    assert gift._pick_gift(1112, rng=_RNG(0.400))["name"] == gift._gift_list()[-2]["name"]


def test_cheapest_gift():
    assert gift._cheapest_gift()["name"] == _g0()["name"]


def test_is_special_gift():
    assert gift._is_special_gift(_top()) is True            # 彰冬婚礼邀请函
    assert gift._is_special_gift(gift._gift_list()[-2]) is True  # 自己产的彰冬饭
    assert gift._is_special_gift(_g0()) is False


def test_top_gift_keeps_monotonic_value_after_balance_change():
    previous, top = gift._gift_list()[-2:]
    default_top = gift.DEFAULT_GIFT_CONFIG["gifts"][-1]

    assert top["cost"] == default_top["cost"] == 1112
    assert top["intimacy"] == default_top["intimacy"] == 819
    assert top["intimacy"] > previous["intimacy"]
    assert top["intimacy"] / top["cost"] > previous["intimacy"] / previous["cost"]


def test_pair_key_is_order_independent():
    assert gift._pair_key("9", "10") == gift._pair_key("10", "9")
    assert gift._pair_key("9", "10") == "10|||9"  # 字符串排序


def test_add_and_get_intimacy_accumulates():
    group = gift._new_group()
    assert gift._add_intimacy(group, "A", "B", 10) == 10
    assert gift._add_intimacy(group, "B", "A", 5) == 15  # 无方向累加
    assert gift._get_intimacy(group, "A", "B") == 15
    assert gift._get_intimacy(group, "A", "C") == 0


def test_top_partners_sorted_desc():
    group = {"intimacy": {"A|||B": 5, "A|||C": 10, "B|||C": 3}, "users": {}}
    assert gift._top_partners(group, "A") == [("C", 10), ("B", 5)]


def test_bond_level_brackets():
    # 每级最低门槛恰好进入该级
    assert gift._bond_level(0)["name"] == "Hot Dogs"
    assert gift._bond_level(99)["name"] == "Hot Dogs"
    assert gift._bond_level(100)["name"] == "大麦克风"
    assert gift._bond_level(399)["name"] == "大麦克风"
    assert gift._bond_level(400)["name"] == "能信赖的搭档"
    assert gift._bond_level(1000)["name"] == "云与柳的大头贴"
    assert gift._bond_level(2500)["name"] == "想与你并肩而行"
    assert gift._bond_level(6000)["name"] == "从今往后直到永远"
    assert gift._bond_level(999999)["name"] == "从今往后直到永远"


def test_bond_level_progress_and_maxed():
    mid = gift._bond_level(620)
    assert mid["name"] == "能信赖的搭档"
    assert mid["next_name"] == "云与柳的大头贴"
    assert mid["to_next"] == 1000 - 620
    assert mid["level"] == 3  # Hot Dogs=Lv1 锚定（不受负档前置影响）
    top = gift._bond_level(7000)
    assert top["name"] == "从今往后直到永远"
    assert top["level"] == 6
    assert top["next_name"] is None
    assert top["to_next"] == 0


def test_bond_level_negative_tiers():
    assert gift._bond_level(0)["name"] == "Hot Dogs"
    assert gift._bond_level(0)["level"] == 1
    assert gift._bond_level(-1)["name"] == "闹别扭"
    assert gift._bond_level(-50)["name"] == "闹别扭"
    assert gift._bond_level(-50)["team_level"] == 0
    assert gift._bond_level(-51)["name"] == "看不顺眼"
    assert gift._bond_level(-51)["team_level"] == -1
    assert gift._bond_level(-100)["name"] == "看不顺眼"
    assert gift._bond_level(-101)["name"] == "有过节"
    assert gift._bond_level(-180)["name"] == "有过节"
    assert gift._bond_level(-181)["name"] == "结了梁子"
    assert gift._bond_level(-300)["name"] == "结了梁子"
    assert gift._bond_level(-301)["name"] == "势同水火"
    assert gift._bond_level(-301)["team_level"] == -2
    assert gift._bond_level(-650)["name"] == "势同水火"
    assert gift._bond_level(-651)["name"] == "宿敌"
    assert gift._bond_level(-99999)["name"] == "宿敌"  # 兜底到最低档
    assert gift._bond_level(-10)["level"] <= 0  # 负档不挂 Lv


def test_count_directed_bump_and_get():
    group = gift._new_group()
    assert gift._get_count(group, "A", "B") == 0
    assert gift._bump_count(group, "A", "B") == 1
    gift._bump_count(group, "A", "B")
    gift._bump_count(group, "B", "A")
    assert gift._get_count(group, "A", "B") == 2  # 有向：A→B
    assert gift._get_count(group, "B", "A") == 1  # 有向：B→A


def test_wedding_invitation_history_uses_directionless_pair_key():
    group = gift._new_group()
    gift._record_wedding_invitation(group, "A", "B", "2026-07-13", bonus=495)

    assert gift._pair_key("A", "B") in group["wedding_invitations"]
    assert gift._pair_key("B", "A") in group["wedding_invitations"]
    assert gift._wedding_pair_has_1314(group, "B", "A") is True


def test_wedding_first_sender_bonus_only_applies_once():
    data = gift._normalize_data({"groups": {"1001": {}, "1002": {}}})
    group = gift._get_group(data, "1001")
    other_group = gift._get_group(data, "1002")
    top = _top()

    first = gift._settle(group, "A", "B", top, "special", None)
    gift._settle_wedding_invitation(group, "A", "B", first, "2026-07-13")
    second = gift._settle(other_group, "A", "C", top, "special", None)
    gift._settle_wedding_invitation(other_group, "A", "C", second, "2026-07-14")

    assert first["amount"] == 1314
    assert first["wedding_bonus"] == 495
    assert gift._get_intimacy(group, "A", "B") == 1314
    assert second["amount"] == 819
    assert second["wedding_bonus"] == 0
    assert gift._get_intimacy(other_group, "A", "C") == 819
    assert gift._pair_key("A", "B") in other_group["wedding_invitations"]


def test_wedding_bonus_requires_sender_first_and_pair_without_1314():
    data = gift._normalize_data({"groups": {"1001": {}}})
    group = gift._get_group(data, "1001")
    top = _top()

    first = gift._settle(group, "B", "C", top, "special", None)
    gift._settle_wedding_invitation(group, "B", "C", first, "2026-07-13")
    pair_blocked = gift._settle(group, "C", "B", top, "special", None)
    gift._settle_wedding_invitation(group, "C", "B", pair_blocked, "2026-07-14")

    assert first["amount"] == 1314
    assert pair_blocked["amount"] == 819
    assert pair_blocked["wedding_bonus"] == 0
    assert data["users"]["C"]["wedding_first_bonus_claimed"] is True


def test_wedding_pair_with_only_819_does_not_block_eligible_sender_bonus():
    data = gift._normalize_data({"groups": {"1001": {}}})
    group = gift._get_group(data, "1001")
    top = _top()
    data["users"]["B"] = {"wedding_first_bonus_claimed": True}

    base = gift._settle(group, "B", "A", top, "special", None)
    gift._settle_wedding_invitation(group, "B", "A", base, "2026-07-13")
    assert gift._wedding_pair_has_1314(group, "A", "B") is False

    eligible = gift._settle(group, "A", "B", top, "special", None)
    gift._settle_wedding_invitation(group, "A", "B", eligible, "2026-07-14")

    assert base["amount"] == 819
    assert gift._wedding_pair_has_1314(group, "A", "B") is True
    assert eligible["amount"] == 1314
    assert eligible["wedding_bonus"] == 495


def test_historical_wedding_records_are_seeded_idempotently():
    data = gift._normalize_data({"groups": {"1001": {}, "1002": {}}})
    group = gift._get_group(data, "1001")
    other_group = gift._get_group(data, "1002")

    assert gift._apply_historical_wedding_records(group) == 2
    assert gift._apply_historical_wedding_records(other_group) == 0
    assert gift._pair_key("2833120053", "630778039") in group["wedding_invitations"]
    assert gift._pair_key("3534610836", "3541957542") in other_group["wedding_invitations"]
    assert data["users"]["2833120053"]["wedding_first_bonus_claimed"] is True
    assert data["users"]["3541957542"]["wedding_first_bonus_claimed"] is True

    top = _top()
    out = gift._settle(group, "2833120053", "NEW", top, "special", None)
    gift._settle_wedding_invitation(group, "2833120053", "NEW", out, "2026-07-13")
    assert out["amount"] == 819
    assert out["wedding_bonus"] == 0

    assert other_group["wedding_invitations"] is group["wedding_invitations"]


def test_global_profiles_and_social_state_share_across_groups_with_source_priority():
    pair = gift._pair_key("A", "B")
    data = gift._normalize_data({"groups": {
        "691188576": {
            "users": {"A": {"points": 88, "exp": 120}},
            "intimacy": {pair: 400},
            "counts": {"A>B": 3},
        },
        "1002": {
            "users": {"A": {"points": 999, "exp": 999}, "B": {"points": 20}},
            "intimacy": {pair: 900},
            "counts": {"A>B": 8},
        },
    }})
    source = gift._get_group(data, "691188576")
    other = gift._get_group(data, "1002")

    assert source["users"]["A"] is other["users"]["A"]
    assert other["users"]["A"]["points"] == 88
    assert other["users"]["A"]["exp"] == 120
    assert gift._get_intimacy(other, "A", "B") == 400
    assert gift._get_count(other, "A", "B") == 3

    gift._add_points(source, "A", 12)
    gift._add_intimacy(source, "A", "B", 5)
    gift._bump_count(source, "A", "B")
    assert other["users"]["A"]["points"] == 100
    assert gift._get_intimacy(other, "A", "B") == 405
    assert gift._get_count(other, "A", "B") == 4


def test_global_profiles_round_trip_without_group_copies():
    pair = gift._pair_key("A", "B")
    data = gift._normalize_data({"groups": {
        "691188576": {"users": {"A": {"points": 88}}, "intimacy": {pair: 400}},
        "1002": {"users": {"A": {"points": 999}, "B": {"points": 20}}},
    }})
    stored = game_store._serializable_data(data)

    assert stored["users"]["A"]["points"] == 88
    assert stored["intimacy"][pair] == 400
    assert set(stored["groups"]["1002"]) == {"user_ids", "rpg"}

    reloaded = gift._normalize_data(stored)
    source = gift._get_group(reloaded, "691188576")
    other = gift._get_group(reloaded, "1002")
    assert source["users"]["A"] is other["users"]["A"]
    assert gift._get_intimacy(other, "A", "B") == 400


def test_normalize_data_preserves_counts_and_wedding_invitations():
    raw = {"groups": {"1001": {
        "users": {},
        "intimacy": {"a|||b": 50},
        "counts": {"a>b": 3, "b>a": 1},
        "wedding_invitations": {"a|||b": {"sender_id": "a", "recipient_id": "b"}},
    }}}
    norm = gift._normalize_data(raw)
    assert norm["groups"]["1001"]["counts"] == {"a>b": 3, "b>a": 1}
    assert norm["groups"]["1001"]["wedding_invitations"]["a|||b"]["sender_id"] == "a"
    # 旧数据无 counts → 容错为空（不报错）
    old = gift._normalize_data({"groups": {"1001": {"intimacy": {"a|||b": 5}}}})
    assert old["groups"]["1001"]["counts"] == {}
    assert old["groups"]["1001"]["wedding_invitations"] == {}


def test_bond_card_shows_level_and_directed_counts():
    group = gift._new_group()
    gift._add_intimacy(group, "10001", "10002", 620)
    gift._bump_count(group, "10001", "10002")
    gift._bump_count(group, "10001", "10002")
    gift._bump_count(group, "10002", "10001")
    card = str(gift._bond_card(group, "10001", "10002"))
    assert "能信赖的搭档" in card
    assert "距「云与柳的大头贴」还差" in card
    assert "你送出 2 次" in card
    assert "ta 回送 1 次" in card
    assert "[at:10001]" in card and "[at:10002]" in card


def test_bond_card_no_gifts_yet():
    group = gift._new_group()
    card = str(gift._bond_card(group, "10001", "10002"))
    assert "Hot Dogs" in card
    assert "还没互送过礼" in card


def test_weighted_choice_respects_zero_weights():
    assert gift._weighted_choice({"x": 0, "y": 5, "z": 0}, __import__("random")) == "y"


def test_roll_main_event_uses_config_weights(monkeypatch):
    monkeypatch.setitem(gift.GIFT_CONFIG, "event_weights", {"crit": 1})
    assert gift._roll_main_event() == "crit"


def test_roll_mishap_returns_table_key():
    keys = set(gift._mishaps())
    assert keys == {
        "damaged", "freebie", "rare", "handwritten", "praised",
        "overboard", "delayed", "dupe", "lost",
    }
    assert gift._roll_mishap() in keys


def test_roll_return_gift_returns_table_key():
    keys = set(gift._return_gifts())
    assert keys == {"guzi", "card", "doujin", "rareguzi", "jouhan"}
    assert gift._roll_return_gift() in keys


def test_settle_normal_adds_base_intimacy():
    group = gift._new_group()
    base = _g0()["intimacy"]
    out = gift._settle(group, "A", "B", _g0(), "normal", None)
    assert out["amount"] == base
    assert gift._get_intimacy(group, "A", "B") == base


def test_settle_crit_doubles():
    group = gift._new_group()
    expected = _g0()["intimacy"] * gift._cfg("crit_multiplier")
    out = gift._settle(group, "A", "B", _g0(), "crit", None)
    assert out["amount"] == expected
    assert gift._get_intimacy(group, "A", "B") == expected


def test_settle_return_no_refund_tier_adds_bonus():
    """回礼无退分档：羁绊 = base + bonus，return_gift 取该档名，不退分。"""
    group = gift._new_group()
    g0 = _g0()
    spec = gift._return_spec("guzi")
    out = gift._settle(group, "A", "B", g0, "return", None, "guzi")
    assert out["return_gift"] == spec["name"]
    assert out["return_key"] == "guzi"
    assert out["amount"] == g0["intimacy"] + spec["bonus"]
    assert out["refund"] == 0
    assert gift._get_intimacy(group, "A", "B") == g0["intimacy"] + spec["bonus"]
    assert gift._get_user(group, "A")["points"] == 0


def test_settle_return_refund_tier_credits_points():
    """回礼退分档：退还 int(cost*ratio) 入送礼方账，羁绊 = base + bonus。"""
    group = gift._new_group()
    g = gift._gift_list()[-3]  # 彰冬手办（最贵的非特殊礼，确保退分>0）
    spec = gift._return_spec("doujin")
    refund = int(g["cost"] * spec["refund_ratio"])
    assert refund > 0
    out = gift._settle(group, "A", "B", g, "return", None, "doujin")
    assert out["return_gift"] == spec["name"]
    assert out["amount"] == g["intimacy"] + spec["bonus"]
    assert out["refund"] == refund
    assert gift._get_intimacy(group, "A", "B") == g["intimacy"] + spec["bonus"]
    assert gift._get_user(group, "A")["points"] == refund


def test_settle_fail_refunds_consolation():
    """失败：0 羁绊不变，但按 cost 比例退还安慰积分入送礼方账。"""
    group = gift._new_group()
    g = gift._gift_list()[-3]  # 彰冬手办（贵礼，退分明显 >0）
    refund = int(g["cost"] * float(gift._cfg("fail_refund_ratio")))
    assert refund > 0
    out = gift._settle(group, "A", "B", g, "fail", None)
    assert out["amount"] == 0
    assert gift._get_intimacy(group, "A", "B") == 0          # 仍不涨羁绊
    assert out["refund"] == refund
    assert gift._get_user(group, "A")["points"] == refund    # 退分入账


def test_settle_special_uses_gift_own_intimacy():
    specials = [g for g in gift._gift_list() if g.get("special")]
    assert len(specials) >= 2  # 彰冬饭 + 婚礼邀请函
    for g in specials:
        group = gift._new_group()
        out = gift._settle(group, "A", "B", g, "special", None)
        assert out["amount"] == g["intimacy"]                         # 取礼物自身好感度
        assert gift._get_intimacy(group, "A", "B") == g["intimacy"]
        assert out["copy"] == g["copy"]                               # 走礼物专属文案


def test_settle_mishap_damaged_refunds_half():
    group = gift._new_group()
    g0 = _g0()
    spec = gift._mishap_spec("damaged")
    refund = int(g0["cost"] * spec["refund_ratio"])
    out = gift._settle(group, "A", "B", g0, "mishap", "damaged")
    assert out["amount"] == spec["intimacy"]
    assert out["refund"] == refund
    assert gift._get_intimacy(group, "A", "B") == spec["intimacy"]
    assert gift._get_user(group, "A")["points"] == refund  # 返还入账


def test_settle_mishap_freebie_intimacy_no_refund():
    group = gift._new_group()
    g0 = _g0()
    spec = gift._mishap_spec("freebie")
    out = gift._settle(group, "A", "B", g0, "mishap", "freebie")
    assert out["amount"] == spec["intimacy"]
    assert out["refund"] == 0  # 不退款
    assert gift._get_intimacy(group, "A", "B") == spec["intimacy"]
    assert gift._get_user(group, "A")["points"] == 0


def test_settle_mishap_lost_full_refund_no_intimacy():
    group = gift._new_group()
    g0 = _g0()
    out = gift._settle(group, "A", "B", g0, "mishap", "lost")
    assert out["amount"] == 0  # 不涨羁绊
    assert out["refund"] == g0["cost"]  # 全额返还
    assert gift._get_intimacy(group, "A", "B") == 0
    assert gift._get_user(group, "A")["points"] == g0["cost"]


def test_settle_mishap_dupe_partial_refund():
    group = gift._new_group()
    g0 = _g0()
    spec = gift._mishap_spec("dupe")
    out = gift._settle(group, "A", "B", g0, "mishap", "dupe")
    assert out["amount"] == spec["intimacy"]
    assert out["refund"] == int(g0["cost"] * spec["refund_ratio"])


def test_settle_mishap_scales_with_base():
    """意外羁绊取 max(保底, ratio×base)：贵礼按档放大、便宜礼吃保底（手感不变）。"""
    g_big = gift._gift_list()[-3]  # 手办 base 255
    base = g_big["intimacy"]
    # freebie ratio=1.0 → 缩放 255 > 保底 28 → 取缩放
    spec_f = gift._mishap_spec("freebie")
    out = gift._settle(gift._new_group(), "A", "B", g_big, "mishap", "freebie")
    assert out["amount"] == max(int(spec_f["intimacy"]), int(float(spec_f["ratio"]) * base)) == base
    # overboard ratio=1.1 → 缩放 280 > 保底 → 取缩放
    spec_o = gift._mishap_spec("overboard")
    out2 = gift._settle(gift._new_group(), "A", "B", g_big, "mishap", "overboard")
    assert out2["amount"] == int(float(spec_o["ratio"]) * base)
    # 便宜礼（无料 base 12）：缩放 < 保底 → 仍取保底，旧手感不变
    out3 = gift._settle(gift._new_group(), "A", "B", _g0(), "mishap", "freebie")
    assert out3["amount"] == int(spec_f["intimacy"])


def test_every_mishap_has_copy_and_renders():
    """每个意外都有对应文案、且能正常渲染（含 @），防漏配 copy。"""
    g0 = _g0()
    for key in gift._mishaps():
        out = gift._settle(gift._new_group(), "1", "2", g0, "mishap", key)
        msg = str(gift._build_broadcast(out, "1", "2"))
        assert "[at:1]" in msg and msg.strip()


def test_every_return_gift_has_copy_and_renders():
    """每个回赠档都有对应文案、且能正常渲染（含 @、回赠物名），防漏配 copy。"""
    g0 = _g0()
    for key in gift._return_gifts():
        out = gift._settle(gift._new_group(), "1", "2", g0, "return", None, key)
        msg = str(gift._build_broadcast(out, "1", "2"))
        assert "[at:1]" in msg and msg.strip()
        assert out["return_gift"] in msg  # 回赠物名出现在文案里


def test_outcome_copy_key_mapping():
    assert gift._outcome_copy_key({"event": "normal", "mishap": None}) == "normal"
    assert gift._outcome_copy_key({"event": "special", "mishap": None}) == "special"  # 无 copy 兜底
    assert gift._outcome_copy_key({"event": "special", "copy": "special_wedding"}) == "special_wedding"
    assert gift._outcome_copy_key({"event": "mishap", "mishap": "damaged"}) == "mishap_damaged"
    assert gift._outcome_copy_key({"event": "mishap", "mishap": "lost"}) == "mishap_lost"
    assert gift._outcome_copy_key({"event": "return", "return_key": "doujin"}) == "return_doujin"
    assert gift._outcome_copy_key({"event": "return", "return_key": None}) == "return"  # 缺 key 兜底


def test_render_with_ats_builds_at_and_text():
    result = str(gift._render_with_ats("{a} 给 {b} 送了【{gift}】+{amount}",
                                       {"a": "1", "b": "2", "gift": "谷子", "amount": 10}))
    assert "[at:1]" in result
    assert "[at:2]" in result
    assert "谷子" in result
    assert "10" in result


def test_normalize_data_tolerates_garbage():
    assert gift._normalize_data("nonsense") == {
        "schema_version": gift.SCHEMA_VERSION,
        "users": {},
        "intimacy": {},
        "counts": {},
        "wedding_invitations": {},
        "groups": {},
    }
    norm = gift._normalize_data({"groups": {"1001": {"users": {"u": {"points": 5}}, "intimacy": {"a|||b": 7}}}})
    assert norm["groups"]["1001"]["users"]["u"]["points"] == 5
    assert norm["groups"]["1001"]["intimacy"]["a|||b"] == 7



def test_pick_gift_by_name():
    g = gift._pick_gift_by_name("彰冬无料")
    assert g is not None and g["name"] == "彰冬无料" and g["cost"] == 50
    assert gift._pick_gift_by_name("不存在的礼物") is None

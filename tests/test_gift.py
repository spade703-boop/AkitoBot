"""测试 gift.py 的送礼系统：纯逻辑函数 + 指令行为。

数值断言一律从配置读（`_gift_list()` / `_cfg(...)`），调数值不会让测试变脆。
"""

from __future__ import annotations

from copy import deepcopy
import types

from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gift as gift


def _at(qq):
    return types.SimpleNamespace(type="at", data={"qq": str(qq)})


def _bot():
    return Bot(self_id="114514")


async def _no_delay():
    return None


class _FixedRNG:
    """randint 恒返回定值，用于负羁绊随机扣除的确定性测试。"""

    def __init__(self, val):
        self.val = val

    def randint(self, _a, _b):
        return self.val


def _g0() -> dict:
    return gift._gift_list()[0]  # 最低档


def _top() -> dict:
    return gift._gift_list()[-1]  # 顶档（彰冬婚礼邀请函）


def _patch_runtime(monkeypatch, *, today: str = "2026-06-22", store: dict | None = None):
    state = gift._normalize_data(store or {})
    monkeypatch.setattr(gift, "_today_str", lambda: today)
    monkeypatch.setattr(gift, "is_sleeping", lambda: False)  # 默认非睡眠时段（防真实 0–6 点跑测试误拦）
    monkeypatch.setattr(gift, "_sign_in_delay", _no_delay)   # 签到延迟空操作（防测试真睡 3–5s）

    def _load():
        return deepcopy(state)

    def _save(data):
        state.clear()
        state.update(deepcopy(gift._normalize_data(data)))

    monkeypatch.setattr(gift, "_load_data", _load)
    monkeypatch.setattr(gift, "_save_data", _save)
    return state


# ==================== 纯逻辑：礼物挑选 ====================

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


def test_cheapest_gift():
    assert gift._cheapest_gift()["name"] == _g0()["name"]


def test_is_special_gift():
    assert gift._is_special_gift(_top()) is True            # 彰冬婚礼邀请函
    assert gift._is_special_gift(gift._gift_list()[-2]) is True  # 自己产的彰冬饭
    assert gift._is_special_gift(_g0()) is False


# ==================== 纯逻辑：亲密度 ====================

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


# ==================== 羁绊等级 / 送礼次数 ====================

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
    assert gift._bond_level(-1)["name"] == "有过节"
    assert gift._bond_level(-50)["name"] == "有过节"
    assert gift._bond_level(-51)["name"] == "结了梁子"
    assert gift._bond_level(-300)["name"] == "结了梁子"
    assert gift._bond_level(-301)["name"] == "宿敌"
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


def test_normalize_data_preserves_counts():
    raw = {"groups": {"1001": {"users": {}, "intimacy": {"a|||b": 50}, "counts": {"a>b": 3, "b>a": 1}}}}
    norm = gift._normalize_data(raw)
    assert norm["groups"]["1001"]["counts"] == {"a>b": 3, "b>a": 1}
    # 旧数据无 counts → 容错为空（不报错）
    old = gift._normalize_data({"groups": {"1001": {"intimacy": {"a|||b": 5}}}})
    assert old["groups"]["1001"]["counts"] == {}


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


# ==================== 纯逻辑：随机抽取 ====================

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


# ==================== 纯逻辑：结算 _settle ====================

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


# ==================== 纯逻辑：文案组装 ====================

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
    assert gift._normalize_data("nonsense") == {"schema_version": gift.SCHEMA_VERSION, "groups": {}}
    norm = gift._normalize_data({"groups": {"1001": {"users": {"u": {"points": 5}}, "intimacy": {"a|||b": 7}}}})
    assert norm["groups"]["1001"]["users"]["u"]["points"] == 5
    assert norm["groups"]["1001"]["intimacy"]["a|||b"] == 7


# ==================== 指令：签到 ====================

@pytest.mark.asyncio
async def test_sign_cmd_rejects_private_chat(monkeypatch):
    _patch_runtime(monkeypatch)
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event())
    assert "群里" in str(exc.value.result)


@pytest.mark.asyncio
async def test_sign_cmd_grants_then_silent_on_repeat(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 70)

    # 首次签到：正常应答 + 入账
    with pytest.raises(FinishedException) as first:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "70" in str(first.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 70

    # 当天重复签到：静默（不抛 finish、不改积分）
    result = await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert result is None
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 70


# ==================== 指令：送礼 ====================

@pytest.mark.asyncio
async def test_gift_cmd_requires_at_target(monkeypatch):
    _patch_runtime(monkeypatch)
    event = Event(group_id=1001, user_id="10001", original_message=[])
    result = await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert result is None  # 无 @ 目标时静默忽略


@pytest.mark.asyncio
async def test_gift_cmd_rejects_self_and_bot(monkeypatch):
    _patch_runtime(monkeypatch)

    self_event = Event(group_id=1001, user_id="10001", original_message=[_at("10001")])
    self_result = await gift.gift_cmd.handlers[0](_bot(), self_event, Message(""))
    assert self_result is None  # @自己时静默忽略

    bot_event = Event(group_id=1001, user_id="10001", original_message=[_at("114514")])
    bot_result = await gift.gift_cmd.handlers[0](_bot(), bot_event, Message(""))
    assert bot_result is None  # @bot 时静默忽略


@pytest.mark.asyncio
async def test_gift_cmd_rejects_insufficient_points(monkeypatch):
    _patch_runtime(monkeypatch)  # 新用户 0 积分，买不起最低档
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "不太够" in str(exc.value.result)


@pytest.mark.asyncio
async def test_gift_cmd_happy_path_deducts_and_adds_intimacy(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "normal")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert g0["name"] in result
    assert "[at:10001]" in result and "[at:10002]" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - g0["cost"]
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == g0["intimacy"]
    assert state["groups"]["1001"]["counts"]["10001>10002"] == 1  # 有向送礼次数 +1


@pytest.mark.asyncio
async def test_gift_cmd_return_path_credits_refund(monkeypatch):
    """回礼路径：先扣 cost 再退 refund、羁绊=base+bonus、文案含回赠物名。"""
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "return")
    monkeypatch.setattr(gift, "_roll_return_gift", lambda rng=gift.random: "doujin")
    spec = gift._return_spec("doujin")
    refund = int(g0["cost"] * spec["refund_ratio"])

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert spec["name"] in result  # 文案含回赠物名
    assert "[at:10001]" in result and "[at:10002]" in result
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - g0["cost"] + refund
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == g0["intimacy"] + spec["bonus"]
    assert state["groups"]["1001"]["counts"]["10001>10002"] == 1


@pytest.mark.asyncio
async def test_gift_cmd_special_gift_always_special(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    top = _top()
    monkeypatch.setattr(gift, "_pick_gift", lambda _points, rng=gift.random: top)
    # 即便 roll 出别的事件，special 礼物也应强制走 special 分支
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "fail")

    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))

    result = str(exc.value.result)
    assert "婚礼" in result  # 顶档现为婚礼邀请函，走 special_wedding 文案
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000 - top["cost"]
    assert state["groups"]["1001"]["intimacy"]["10001|||10002"] == top["intimacy"]


@pytest.mark.asyncio
async def test_gift_cmd_blocks_second_gift_same_day(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000, "last_gift": "2026-06-22"}}, "intimacy": {}}}},
    )
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "今天的礼已经送过" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000  # 未扣分


# ==================== 超管不限次（测试用） ====================

@pytest.mark.asyncio
async def test_sign_cmd_superuser_unlimited(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    # 超管同一天连签两次都正常入账（不被限制、不静默）
    for _ in range(2):
        with pytest.raises(FinishedException):
            await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert state["groups"]["1001"]["users"][su]["points"] == 120


@pytest.mark.asyncio
async def test_gift_cmd_superuser_ignores_daily_limit(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {su: {"points": 100000, "last_gift": "2026-06-22"}}, "intimacy": {}}}},
    )
    g0 = _g0()
    monkeypatch.setattr(gift, "_pick_gift", lambda _p, rng=gift.random: g0)
    monkeypatch.setattr(gift, "_roll_main_event", lambda: "normal")
    # last_gift==today，普通用户会被「今天已送过」拦下；超管照送
    event = Event(group_id=1001, user_id=su, original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert g0["name"] in str(exc.value.result)
    assert state["groups"]["1001"]["users"][su]["points"] == 100000 - g0["cost"]


# ==================== 睡眠拦截（0–6 点） ====================

@pytest.mark.asyncio
async def test_sign_cmd_blocked_during_sleep(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert "睡" in str(exc.value.result)
    # 睡眠时段不入账、不写库
    assert state["groups"].get("1001", {}).get("users", {}).get("10001", {}).get("points", 0) == 0


@pytest.mark.asyncio
async def test_gift_cmd_blocked_during_sleep(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100000}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.gift_cmd.handlers[0](_bot(), event, Message(""))
    assert "睡" in str(exc.value.result)
    # 不扣分、不计数
    assert state["groups"]["1001"]["users"]["10001"]["points"] == 100000
    assert state["groups"]["1001"].get("counts", {}) == {}


@pytest.mark.asyncio
async def test_superuser_bypasses_sleep(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    # 超管深夜仍可签到（不被睡眠拦截）
    with pytest.raises(FinishedException) as exc:
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert "60" in str(exc.value.result)
    assert state["groups"]["1001"]["users"][su]["points"] == 60


# ==================== 偷积分 ====================

def _steal_group(thief_pts=0, victim_pts=200, bond=0):
    group = gift._new_group()
    gift._get_user(group, "T")["points"] = thief_pts
    gift._get_user(group, "V")["points"] = victim_pts
    if bond:
        gift._add_intimacy(group, "T", "V", bond)
    return group


def test_steal_outcome_in_weights():
    keys = set(gift._steal_cfg()["weights"])
    assert keys == {"success", "caught", "whiff", "reversal"}
    assert gift._steal_outcome() in keys


def test_settle_steal_success_capped_and_moves_points():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=0, victim_pts=1000)
    out = gift._settle_steal(group, "T", "V", "success")
    amt = min(int(1000 * cfg["ratio"]), cfg["cap"], 1000)
    assert out["amount"] == amt == cfg["cap"]  # 封顶生效
    assert gift._get_user(group, "T")["points"] == amt
    assert gift._get_user(group, "V")["points"] == 1000 - amt


def test_settle_steal_caught_pays_victim():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "caught")
    assert out["amount"] == cfg["caught_penalty"]
    assert gift._get_user(group, "T")["points"] == 100 - cfg["caught_penalty"]
    assert gift._get_user(group, "V")["points"] == 200 + cfg["caught_penalty"]


def test_settle_steal_reversal_pays_victim():
    cfg = gift._steal_cfg()
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "reversal")
    assert out["amount"] == cfg["reversal_amount"]
    assert gift._get_user(group, "T")["points"] == 100 - cfg["reversal_amount"]
    assert gift._get_user(group, "V")["points"] == 200 + cfg["reversal_amount"]


def test_settle_steal_whiff_keeps_points():
    group = _steal_group(thief_pts=100, victim_pts=200)
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert out["amount"] == 0
    assert gift._get_user(group, "T")["points"] == 100
    assert gift._get_user(group, "V")["points"] == 200


def test_settle_steal_success_bond_scales_with_amount():
    group_small = _steal_group(victim_pts=80, bond=0)
    out_small = gift._settle_steal(group_small, "T", "V", "success")
    group_big = _steal_group(victim_pts=1000, bond=0)
    out_big = gift._settle_steal(group_big, "T", "V", "success")
    assert out_big["amount"] > out_small["amount"]
    assert out_big["bond"] > out_small["bond"]


def test_settle_steal_whiff_bond_loss_is_light():
    cfg = gift._steal_cfg()
    spec = cfg["bond_loss"]["whiff"]
    group = _steal_group(bond=1000)
    out = gift._settle_steal(group, "T", "V", "whiff")
    expected = int(spec["base"]) + min(int(1000 * float(spec["positive_bond_ratio"])), int(spec["positive_bond_cap"]))
    assert out["bond"] == expected
    assert gift._get_intimacy(group, "T", "V") == 1000 - expected


def test_settle_steal_reversal_hurts_bond_more_than_caught():
    cfg = gift._steal_cfg()
    group_caught = _steal_group(thief_pts=100, victim_pts=200, bond=200)
    out_caught = gift._settle_steal(group_caught, "T", "V", "caught")
    group_reversal = _steal_group(thief_pts=100, victim_pts=200, bond=200)
    out_reversal = gift._settle_steal(group_reversal, "T", "V", "reversal")
    assert out_caught["amount"] == cfg["caught_penalty"]
    assert out_reversal["amount"] == cfg["reversal_amount"]
    assert out_caught["amount"] > out_reversal["amount"]
    assert out_reversal["bond"] > out_caught["bond"]


def test_settle_steal_low_or_zero_amount_no_longer_drops_huge_bond():
    group = _steal_group(bond=-100)
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert out["bond"] == gift._steal_cfg()["bond_loss"]["whiff"]["base"]
    assert gift._get_intimacy(group, "T", "V") == -100 - out["bond"]


def test_settle_steal_bond_floor():
    floor = gift._steal_cfg()["bond_floor"]
    group = _steal_group(bond=floor + 3)  # 接近下限
    out = gift._settle_steal(group, "T", "V", "whiff")
    assert gift._get_intimacy(group, "T", "V") == floor  # 封底，不再下探
    assert out["bond"] == 3  # 实际只掉到下限的幅度


@pytest.mark.asyncio
async def test_steal_cmd_success_moves_points_and_counts(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 0}, "10002": {"points": 1000},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "_steal_outcome", lambda rng=gift.random: "success")
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    cap = gift._steal_cfg()["cap"]
    assert "[at:10001]" in str(exc.value.result)
    assert state["groups"]["1001"]["users"]["10001"]["points"] == cap
    assert state["groups"]["1001"]["users"]["10002"]["points"] == 1000 - cap
    assert state["groups"]["1001"]["users"]["10001"]["steal_used"] == 1
    assert state["groups"]["1001"]["users"]["10002"]["robbed_count"] == 1


@pytest.mark.asyncio
async def test_steal_cmd_rejects_self_bot_no_target(monkeypatch):
    _patch_runtime(monkeypatch, store={"groups": {"1001": {"users": {"10001": {"points": 100}}, "intimacy": {}}}})
    r1 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[]))
    assert r1 is None  # 无 @ 目标时静默忽略
    r2 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[_at("10001")]))
    assert r2 is None  # @自己时静默忽略
    r3 = await gift.steal_cmd.handlers[0](_bot(), Event(group_id=1001, user_id="10001", original_message=[_at("114514")]))
    assert r3 is None  # @bot 时静默忽略


@pytest.mark.asyncio
async def test_steal_cmd_blocks_too_poor(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100}, "10002": {"points": 10}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "没什么好偷" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_daily_limit_blocks(monkeypatch):
    limit = gift._steal_cfg()["daily_limit"]
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100, "steal_date": "2026-06-22", "steal_used": limit},
            "10002": {"points": 500},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "手气用完" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_blocked_by_signin_protection(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100}, "10002": {"points": 500, "protect_until": 9999.0},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 1000.0)  # < protect_until → 受保护
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "偷不了" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_victim_daily_limit_blocks(monkeypatch):
    vlimit = gift._steal_cfg()["victim_daily_limit"]
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"points": 100},
            "10002": {"points": 500, "robbed_date": "2026-06-22", "robbed_count": vlimit},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "偷不了" in str(exc.value.result)


@pytest.mark.asyncio
async def test_steal_cmd_superuser_bypasses_all(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            su: {"points": 0, "steal_date": "2026-06-22", "steal_used": 99},
            "10002": {"points": 1000, "protect_until": 9e9, "robbed_date": "2026-06-22", "robbed_count": 99},
        }, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "_steal_outcome", lambda rng=gift.random: "success")
    monkeypatch.setattr(gift.time, "time", lambda: 0.0)
    event = Event(group_id=1001, user_id=su, original_message=[_at("10002")])
    with pytest.raises(FinishedException):
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert state["groups"]["1001"]["users"][su]["points"] == gift._steal_cfg()["cap"]  # 照偷不误


@pytest.mark.asyncio
async def test_steal_cmd_blocked_during_sleep(monkeypatch):
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {"10001": {"points": 100}, "10002": {"points": 500}}, "intimacy": {}}}},
    )
    monkeypatch.setattr(gift, "is_sleeping", lambda: True)
    event = Event(group_id=1001, user_id="10001", original_message=[_at("10002")])
    with pytest.raises(FinishedException) as exc:
        await gift.steal_cmd.handlers[0](_bot(), event)
    assert "睡" in str(exc.value.result)


@pytest.mark.asyncio
async def test_sign_cmd_sets_protect_until(monkeypatch):
    state = _patch_runtime(monkeypatch)
    monkeypatch.setattr(gift.random, "randint", lambda _a, _b: 60)
    monkeypatch.setattr(gift.time, "time", lambda: 1000.0)
    with pytest.raises(FinishedException):
        await gift.sign_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    pm = gift._steal_cfg()["protect_minutes"]
    assert state["groups"]["1001"]["users"]["10001"]["protect_until"] == 1000.0 + pm * 60


@pytest.mark.asyncio
async def test_reset_signin_cmd_clears_only_today_gate(monkeypatch):
    su = gift.SUPERUSER_QQ
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {
                "points": 88,
                "last_sign_in": "2026-06-22",
                "fortune_date": "2026-06-22",
                "equip_date": "2026-06-22",
                "equip_used": False,
                "signin_streak": 7,
                "signin_last_date": "2026-06-22",
                "protect_until": 9999.0,
            },
            "10002": {"points": 10, "last_sign_in": "2026-06-21"},
            "10003": {"points": 20, "last_sign_in": "2026-06-22"},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id=su))

    assert "已清掉 2 人" in str(exc.value.result)
    users = state["groups"]["1001"]["users"]
    assert users["10001"]["last_sign_in"] == ""
    assert users["10003"]["last_sign_in"] == ""
    assert users["10002"]["last_sign_in"] == "2026-06-21"
    assert users["10001"]["fortune_date"] == "2026-06-22"
    assert users["10001"]["equip_date"] == "2026-06-22"
    assert users["10001"]["equip_used"] is False
    assert users["10001"]["signin_streak"] == 7
    assert users["10001"]["signin_last_date"] == "2026-06-22"
    assert users["10001"]["protect_until"] == 9999.0


@pytest.mark.asyncio
async def test_reset_signin_cmd_silent_for_non_superuser(monkeypatch):
    state = _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"last_sign_in": "2026-06-22"},
        }, "intimacy": {}}}},
    )
    result = await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id="10001"))
    assert result is None
    assert state["groups"]["1001"]["users"]["10001"]["last_sign_in"] == "2026-06-22"


@pytest.mark.asyncio
async def test_reset_signin_cmd_reports_when_nobody_blocked(monkeypatch):
    su = gift.SUPERUSER_QQ
    _patch_runtime(
        monkeypatch,
        store={"groups": {"1001": {"users": {
            "10001": {"last_sign_in": "2026-06-21"},
            "10002": {"points": 5},
        }, "intimacy": {}}}},
    )
    with pytest.raises(FinishedException) as exc:
        await gift.reset_signin_cmd.handlers[0](Event(group_id=1001, user_id=su))
    assert "还没人被签到闸门卡住" in str(exc.value.result)

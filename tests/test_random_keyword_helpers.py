"""测试 random_keyword.py 的关键词抽取辅助函数与指令行为。"""

from __future__ import annotations

from copy import deepcopy

from nonebot.adapters import Event
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.random_keyword as random_keyword


def _pick_first(options):
    return options[0]


def _sample_first(items, k):
    return list(items)[:k]


def _deterministic_select(categories, count, *, sample_fn=None, choice_fn=None):
    chosen = list(categories)[: min(count, len(categories))]
    return [items[0] for _cat_name, items in chosen]


def _patch_draw_runtime(
    monkeypatch,
    *,
    today: str = "2026-06-13",
    draw_count: int = 1,
    state: dict | None = None,
    categories: dict[str, list[str]] | None = None,
):
    store = deepcopy(random_keyword._normalize_draws_state(state or {}, today))
    pool = categories or {
        "科学隐喻": ["洛希极限", "潮汐锁定"],
        "自然意象": ["暴雨", "逆光"],
        "关系张力": ["错位"],
    }

    monkeypatch.setattr(random_keyword, "KEYWORD_DATA", {"categories": deepcopy(pool)})
    monkeypatch.setattr(random_keyword, "_today_str", lambda: today)
    monkeypatch.setattr(random_keyword.random, "randint", lambda _start, _end: draw_count)
    monkeypatch.setattr(random_keyword.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(random_keyword, "_select_daily_keywords", _deterministic_select)

    async def _fake_sleep(_delay: float):
        return None

    monkeypatch.setattr(random_keyword.asyncio, "sleep", _fake_sleep)

    def _fake_load_draws():
        return deepcopy(store)

    def _fake_save_draws(data: dict):
        store.clear()
        store.update(deepcopy(random_keyword._normalize_draws_state(data, today)))

    monkeypatch.setattr(random_keyword, "_load_draws", _fake_load_draws)
    monkeypatch.setattr(random_keyword, "_save_draws", _fake_save_draws)
    return store


def test_resolve_keyword_category_name_supports_exact_and_prefix():
    names = ["科学隐喻", "自然意象", "关系张力"]

    assert random_keyword._resolve_keyword_category_name("自然意象", names) == "自然意象"
    assert random_keyword._resolve_keyword_category_name("科学", names) == "科学隐喻"
    assert random_keyword._resolve_keyword_category_name("不存在", names) is None


def test_get_existing_keyword_draw_message_detects_same_day_record():
    msg = random_keyword._get_existing_keyword_draw_message(
        {"date": "2026-06-11", "items": ["洛希极限", "雨夜"]},
        "2026-06-11",
    )
    stale = random_keyword._get_existing_keyword_draw_message(
        {"date": "2026-06-10", "items": ["旧词"]},
        "2026-06-11",
    )

    assert "你今天已经领取过关键词了" in msg
    assert "洛希极限、雨夜" in msg
    assert stale is None


def test_normalize_draws_state_supports_legacy_schema():
    state = random_keyword._normalize_draws_state(
        {
            "12345": {"date": "2026-06-12", "count": 2, "items": ["洛希极限", "雨夜"]},
        },
        "2026-06-13",
    )

    assert state["schema_version"] == random_keyword.DRAW_STATE_VERSION
    assert state["users"]["12345"]["date"] == "2026-06-12"
    assert state["users"]["12345"]["count"] == 2
    assert state["users"]["12345"]["items"] == ["洛希极限", "雨夜"]
    assert state["groups"] == {}


def test_get_or_create_group_draw_record_resets_on_new_day():
    state = random_keyword._normalize_draws_state(
        {
            "groups": {
                "1001": {"date": "2026-06-12", "drawn_items": ["洛希极限"]},
            }
        },
        "2026-06-13",
    )

    record = random_keyword._get_or_create_group_draw_record(state, "1001", "2026-06-13")

    assert record == {"date": "2026-06-13", "drawn_items": []}


def test_filter_categories_excluding_drawn_items_removes_taken_keywords():
    filtered = random_keyword._filter_categories_excluding_drawn_items(
        [("科学隐喻", ["洛希极限", "潮汐锁定"]), ("自然意象", ["暴雨"])],
        {"洛希极限", "暴雨"},
    )

    assert filtered == [("科学隐喻", ["潮汐锁定"])]


def test_select_daily_keywords_picks_one_per_sampled_category():
    result = random_keyword._select_daily_keywords(
        [("科学隐喻", ["洛希极限", "热寂"]), ("自然意象", ["雨夜"]), ("关系张力", ["错位"])],
        2,
        sample_fn=_sample_first,
        choice_fn=_pick_first,
    )

    assert result == ["洛希极限", "雨夜"]


@pytest.mark.asyncio
async def test_draw_cmd_rejects_private_chat(monkeypatch):
    _patch_draw_runtime(monkeypatch)
    event = Event()

    with pytest.raises(FinishedException) as excinfo:
        await random_keyword.draw_cmd.handlers[0](event)

    assert "该指令仅支持群聊使用" in str(excinfo.value.result)


@pytest.mark.asyncio
async def test_draw_cmd_keeps_keywords_unique_within_group(monkeypatch):
    state = _patch_draw_runtime(monkeypatch, draw_count=2)

    first_event = Event(group_id=1001, user_id="10001")
    with pytest.raises(FinishedException) as first_exc:
        await random_keyword.draw_cmd.handlers[0](first_event)

    first_result = str(first_exc.value.result)
    assert "洛希极限" in first_result
    assert "暴雨" in first_result

    second_event = Event(group_id=1001, user_id="10002")
    with pytest.raises(FinishedException) as second_exc:
        await random_keyword.draw_cmd.handlers[0](second_event)

    second_result = str(second_exc.value.result)
    assert "潮汐锁定" in second_result
    assert "逆光" in second_result
    assert state["groups"]["1001"]["drawn_items"] == ["洛希极限", "暴雨", "潮汐锁定", "逆光"]


@pytest.mark.asyncio
async def test_draw_cmd_allows_same_keyword_in_different_groups(monkeypatch):
    state = _patch_draw_runtime(monkeypatch, draw_count=1)

    first_event = Event(group_id=1001, user_id="10001")
    with pytest.raises(FinishedException) as first_exc:
        await random_keyword.draw_cmd.handlers[0](first_event)

    second_event = Event(group_id=1002, user_id="10002")
    with pytest.raises(FinishedException) as second_exc:
        await random_keyword.draw_cmd.handlers[0](second_event)

    assert "洛希极限" in str(first_exc.value.result)
    assert "洛希极限" in str(second_exc.value.result)
    assert state["groups"]["1001"]["drawn_items"] == ["洛希极限"]
    assert state["groups"]["1002"]["drawn_items"] == ["洛希极限"]


@pytest.mark.asyncio
async def test_draw_cmd_blocks_same_user_across_groups_on_same_day(monkeypatch):
    _patch_draw_runtime(monkeypatch, draw_count=1)

    first_event = Event(group_id=1001, user_id="10001")
    with pytest.raises(FinishedException):
        await random_keyword.draw_cmd.handlers[0](first_event)

    second_event = Event(group_id=1002, user_id="10001")
    with pytest.raises(FinishedException) as second_exc:
        await random_keyword.draw_cmd.handlers[0](second_event)

    assert "你今天已经领取过关键词了" in str(second_exc.value.result)


@pytest.mark.asyncio
async def test_draw_cmd_superuser_does_not_consume_group_pool(monkeypatch):
    state = _patch_draw_runtime(
        monkeypatch,
        draw_count=1,
        categories={"科学隐喻": ["洛希极限"]},
    )

    superuser_event = Event(group_id=1001, user_id=random_keyword.SUPERUSER_QQ)
    with pytest.raises(FinishedException) as superuser_exc:
        await random_keyword.draw_cmd.handlers[0](superuser_event)

    assert "洛希极限" in str(superuser_exc.value.result)
    assert state["users"] == {}
    assert state["groups"] == {}

    user_event = Event(group_id=1001, user_id="10001")
    with pytest.raises(FinishedException) as user_exc:
        await random_keyword.draw_cmd.handlers[0](user_event)

    assert "洛希极限" in str(user_exc.value.result)
    assert state["groups"]["1001"]["drawn_items"] == ["洛希极限"]


@pytest.mark.asyncio
async def test_draw_cmd_returns_exhausted_message_when_group_pool_is_empty(monkeypatch):
    _patch_draw_runtime(
        monkeypatch,
        draw_count=1,
        categories={"科学隐喻": ["洛希极限"]},
    )

    first_event = Event(group_id=1001, user_id="10001")
    with pytest.raises(FinishedException):
        await random_keyword.draw_cmd.handlers[0](first_event)

    second_event = Event(group_id=1001, user_id="10002")
    with pytest.raises(FinishedException) as second_exc:
        await random_keyword.draw_cmd.handlers[0](second_event)

    assert "本群今天的关键词已经抽完了" in str(second_exc.value.result)

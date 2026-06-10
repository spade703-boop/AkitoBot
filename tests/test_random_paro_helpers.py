"""random_paro 的辅助逻辑与真实派生头像渲染测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from nonebot.adapters import Event
from nonebot.exception import FinishedException

import nonebot_plugin_akito.features.random_paro as random_paro

彰人_黑百合 = "黑百合"
彰人_白骑 = "白骑"
彰人_王子 = "王子彰"
彰人_WL2 = "WL2彰"
彰人_Callboy = "Callboy彰"
彰人_白恶魔 = "白恶魔"
彰人_向日葵 = "向日葵彰"
彰人_法师 = "法师彰"

冬弥_王子 = "王子冬"
冬弥_黑骑 = "黑骑"
冬弥_白百合 = "白百合"
冬弥_WL2 = "WL2冬"
冬弥_Callboy = "Callboy冬"
冬弥_向日葵 = "向日葵冬"
冬弥_青鸟 = "青鸟"

个人预览派生组合 = [
    (彰人_Callboy, 冬弥_Callboy),
    (彰人_白骑, 冬弥_王子),
    (彰人_王子, 冬弥_白百合),
    (彰人_WL2, 冬弥_WL2),
    (彰人_法师, 冬弥_青鸟),
    (彰人_白恶魔, 冬弥_黑骑),
    (彰人_向日葵, 冬弥_向日葵),
]

真实头像目录 = Path(__file__).resolve().parents[1] / "data" / "images" / "paro_avatars"


def test_解析抽派生参数_可识别次数和定向():
    次数, 定向 = random_paro._parse_draw_request(f"3 彰人 {彰人_黑百合}")

    assert 次数 == 3
    assert 定向 == f"彰人 {彰人_黑百合}"


def test_解析抽派生参数_默认单抽():
    次数, 定向 = random_paro._parse_draw_request(f"冬弥 {冬弥_王子}")

    assert 次数 == 1
    assert 定向 == f"冬弥 {冬弥_王子}"


def test_定向抽取解析_可处理唯一匹配和歧义():
    固定彰人, 固定冬弥, 错误 = random_paro._resolve_directional_draw(
        "彰人 黑百",
        [彰人_黑百合, 彰人_白骑],
        [冬弥_王子],
    )
    _固定彰人2, _固定冬弥2, 歧义错误 = random_paro._resolve_directional_draw(
        "冬弥 王",
        [彰人_黑百合],
        [冬弥_王子, "王者冬"],
    )

    assert 固定彰人 == 彰人_黑百合
    assert 固定冬弥 is None
    assert 错误 is None
    assert "匹配到多个条目" in 歧义错误


def test_定向抽取解析_拒绝未知前缀():
    _固定彰人, _固定冬弥, 错误 = random_paro._resolve_directional_draw(
        "别的角色 测试",
        [彰人_黑百合],
        [冬弥_王子],
    )

    assert "请指定要固定哪一方" in 错误


def test_限频辅助函数_可裁剪历史并返回提示():
    历史 = random_paro._prune_draw_history([0.0, 100.0, 1700.0], now_ts=1800.0, window=1800)
    用尽提示 = random_paro._build_draw_limit_message(
        remaining_before=0,
        requested_count=1,
        history=[100.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )
    不足提示 = random_paro._build_draw_limit_message(
        remaining_before=1,
        requested_count=3,
        history=[100.0, 200.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )

    assert 历史 == [100.0, 1700.0]
    assert "你已用完次数" in 用尽提示
    assert "仅剩 1 次" in 不足提示


@pytest.fixture()
def 隔离派生统计():
    统计快照 = copy.deepcopy(random_paro.PARO_STATS)
    统计文件 = random_paro._stats_path()
    彩蛋日志文件 = random_paro._egg_log_path()
    统计文本 = 统计文件.read_text(encoding="utf-8") if 统计文件.exists() else None
    彩蛋日志文本 = 彩蛋日志文件.read_text(encoding="utf-8") if 彩蛋日志文件.exists() else None

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(random_paro._new_stats_state())
    if 统计文件.exists():
        统计文件.unlink()
    if 彩蛋日志文件.exists():
        彩蛋日志文件.unlink()

    yield

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(统计快照)
    if 统计文本 is None:
        if 统计文件.exists():
            统计文件.unlink()
    else:
        统计文件.write_text(统计文本, encoding="utf-8")
    if 彩蛋日志文本 is None:
        if 彩蛋日志文件.exists():
            彩蛋日志文件.unlink()
    else:
        彩蛋日志文件.write_text(彩蛋日志文本, encoding="utf-8")


@pytest.fixture()
def 使用真实派生头像(monkeypatch):
    assert 真实头像目录.exists()
    monkeypatch.setattr(random_paro, "AVATAR_BASE", 真实头像目录)


def test_每日统计翻页只重置日桶(隔离派生统计):
    群统计 = random_paro._new_group_stats("2026-06-10")
    群统计["profiles"] = {"42": "测试用户"}
    群统计["users"] = {"42": random_paro._new_user_stats()}
    群统计["daily"]["total_draws"] = 5
    群统计["history"]["total_draws"] = 9

    已翻页 = random_paro._roll_daily_stats(群统计, "2026-06-11")

    assert 已翻页 is True
    assert 群统计["profiles"] == {"42": "测试用户"}
    assert set(群统计["users"]) == {"42"}
    assert 群统计["daily"]["date"] == "2026-06-11"
    assert 群统计["daily"]["total_draws"] == 0
    assert 群统计["history"]["total_draws"] == 9


def test_阶段统计_固定彰人时只累计冬弥命中和真实彩蛋():
    阶段统计 = random_paro._new_period_stats()
    结果 = [
        (彰人_黑百合, 冬弥_王子, False, None),
        (彰人_白骑, 冬弥_黑骑, True, None),
        (彰人_Callboy, 冬弥_Callboy, False, "fox"),
        (彰人_王子, 冬弥_白百合, False, "foxbun"),
        (彰人_WL2, 冬弥_WL2, False, "foxrabbit"),
        (彰人_向日葵, 冬弥_向日葵, False, "rabbit"),
    ]

    random_paro._record_draw_stats_for_period(
        阶段统计,
        user_id="42",
        results=结果,
        fixed_side="akito",
    )

    assert 阶段统计["total_draws"] == 6
    assert 阶段统计["user_draw_counts"] == {"42": 6}
    assert 阶段统计["akito_hits"] == {}
    assert 阶段统计["toya_hits"] == {冬弥_王子: 1, 冬弥_黑骑: 1}
    assert 阶段统计["egg_user_counts"] == {"42": 2}
    assert 阶段统计["fox_total"] == 1
    assert 阶段统计["rabbit_total"] == 1
    assert 阶段统计["foxrabbit_total"] == 1
    assert 阶段统计["foxbun_total"] == 1
    assert 阶段统计["toya_last_hit_seq"] == {冬弥_王子: 1, 冬弥_黑骑: 2}


def test_角色排行行_同名次只显示前三并省略剩余():
    行 = random_paro._build_character_rows(
        {
            彰人_白骑: 8,
            彰人_王子: 8,
            彰人_WL2: 8,
            彰人_白恶魔: 8,
            彰人_Callboy: 9,
        },
        limit=3,
        character="彰人",
        last_hit_seq={
            彰人_Callboy: 9,
            彰人_白骑: 12,
            彰人_王子: 15,
            彰人_WL2: 20,
            彰人_白恶魔: 23,
        },
    )

    assert 行[0]["left"] == f"TOP1 {彰人_Callboy}"
    assert 行[0]["suffix_avatar_names"] == [彰人_Callboy]
    assert 行[1]["left"] == f"TOP2 {彰人_白骑} / {彰人_王子} / {彰人_WL2} / ..."
    assert 行[1]["suffix_avatar_names"] == [彰人_白骑, 彰人_王子, 彰人_WL2]


def test_狐兔排行行_按次数倒序():
    行 = random_paro._build_fox_rows(
        {
            "foxrabbit_total": 3,
            "foxbun_total": 7,
            "fox_total": 2,
            "rabbit_total": 9,
        }
    )

    assert [一行["fox_type"] for 一行 in 行] == ["rabbit", "foxbun", "foxrabbit", "fox"]


def test_群统计_会写入彩蛋日志(隔离派生统计):
    结果 = [
        (彰人_黑百合, 冬弥_王子, True, None),
        (彰人_白骑, 冬弥_黑骑, False, "foxbun"),
        (彰人_Callboy, 冬弥_Callboy, False, "fox"),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=结果,
        fixed_side=None,
        fixed_name=None,
        requested_count=3,
        now_ts=123.0,
    )

    群统计 = random_paro.PARO_STATS["groups"]["1001"]
    日统计 = 群统计["daily"]
    历史统计 = 群统计["history"]
    彩蛋日志 = [
        json.loads(一行)
        for 一行 in random_paro._egg_log_path().read_text(encoding="utf-8").strip().splitlines()
    ]

    assert 群统计["profiles"] == {"42": "测试用户"}
    assert 日统计["total_draws"] == 3
    assert 历史统计["total_draws"] == 3
    assert 日统计["egg_user_counts"] == {"42": 2}
    assert 日统计["foxbun_total"] == 1
    assert 日统计["fox_total"] == 1
    assert len(彩蛋日志) == 2
    assert {记录["egg_type"] for 记录 in 彩蛋日志} == {"cooking", "foxbun"}
    assert all(记录["user_id"] == "42" for 记录 in 彩蛋日志)


def test_个人历史_固定一侧时仍记录可见派生(隔离派生统计):
    结果 = [
        (彰人_白骑, 冬弥_王子, False, None),
        (彰人_白骑, 冬弥_黑骑, False, None),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=结果,
        fixed_side="akito",
        fixed_name=彰人_白骑,
        requested_count=2,
        now_ts=123.0,
    )

    群统计 = random_paro.PARO_STATS["groups"]["1001"]
    用户统计 = 群统计["users"]["42"]

    assert 群统计["history"]["akito_hits"] == {}
    assert 用户统计["akito_hits"] == {彰人_白骑: 2}
    assert 用户统计["toya_hits"] == {冬弥_王子: 1, 冬弥_黑骑: 1}
    assert 用户统计["pair_hits"] == {
        random_paro._make_pair_key(彰人_白骑, 冬弥_王子): 1,
        random_paro._make_pair_key(彰人_白骑, 冬弥_黑骑): 1,
    }


def test_个人做饭历史_只统计真实做饭和狐兔饭(隔离派生统计):
    结果 = [
        (彰人_黑百合, 冬弥_王子, False, None),
        (彰人_白骑, 冬弥_黑骑, True, None),
        (彰人_Callboy, 冬弥_Callboy, False, "foxbun"),
        (彰人_向日葵, 冬弥_向日葵, False, None),
        (彰人_王子, 冬弥_白百合, True, None),
    ]

    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="84",
        display_name="测试群友",
        results=结果,
        fixed_side=None,
        fixed_name=None,
        requested_count=5,
        now_ts=456.0,
    )

    做饭历史 = random_paro._collect_user_egg_history(1001, "84")

    assert 做饭历史["cooking_count"] == 2
    assert 做饭历史["foxbun_count"] == 1
    assert 做饭历史["cooking_pair_hits"] == {
        random_paro._make_pair_key(彰人_白骑, 冬弥_黑骑): 1,
        random_paro._make_pair_key(彰人_王子, 冬弥_白百合): 1,
    }
    assert random_paro._count_total_cooking_hits(做饭历史) == 3


def test_个人预览里用到的派生都有真实头像素材(使用真实派生头像):
    for 彰人名, 冬弥名 in 个人预览派生组合:
        assert random_paro._find_avatar("彰人", 彰人名) is not None
        assert random_paro._find_avatar("冬弥", 冬弥名) is not None


def test_排行头像后缀_真实派生名可正常加载(使用真实派生头像):
    行 = {
        "left": f"TOP1 {彰人_Callboy} / {彰人_白骑}",
        "right": "9次",
        "suffix_avatar_names": [彰人_Callboy, 彰人_白骑],
        "suffix_character": "彰人",
    }

    图标 = random_paro._resolve_row_suffix_icons(行)

    assert len(图标) == 2
    assert all(图标项 is not None for 图标项 in 图标)


def test_排行榜和个人页图片构建_不会因真实派生头像报错(隔离派生统计):
    random_paro._record_group_draw_stats(
        group_id=1001,
        user_id="42",
        display_name="测试用户",
        results=[
            (彰人_黑百合, 冬弥_王子, True, None),
            (彰人_白骑, 冬弥_黑骑, False, "foxbun"),
        ],
        fixed_side=None,
        fixed_name=None,
        requested_count=2,
        now_ts=123.0,
    )

    assert isinstance(random_paro._build_paro_rank_image(1001, "daily"), bytes)
    assert isinstance(random_paro._build_egg_rank_image(1001, "history"), bytes)
    assert isinstance(random_paro._build_personal_paro_image(1001, "42", "测试用户"), bytes)
    assert isinstance(random_paro._build_rank_preview_image("daily"), bytes)
    assert isinstance(random_paro._build_egg_rank_preview_image("history"), bytes)
    assert isinstance(random_paro._build_personal_preview_image(), bytes)


def test_保存并重载统计_会保留限频记录(隔离派生统计):
    random_paro._cooldown_store()["42"] = [100.0, 200.0]
    random_paro._save_stats()

    random_paro.PARO_STATS.clear()
    random_paro.PARO_STATS.update(random_paro._new_stats_state())
    random_paro.reload_paro_data()

    assert random_paro._cooldown_store()["42"] == [100.0, 200.0]


@pytest.mark.asyncio
async def test_抽派生指令_私聊会被拒绝():
    事件 = Event()

    with pytest.raises(FinishedException) as 异常:
        await random_paro.draw_cmd.handlers[0](事件, 事件.message)

    assert "该指令仅支持群聊使用" in str(异常.value.result)


@pytest.mark.asyncio
async def test_每日排行指令_私聊会被拒绝():
    事件 = Event()

    with pytest.raises(FinishedException) as 异常:
        await random_paro.daily_rank_cmd.handlers[0](事件)

    assert "该指令仅支持群聊使用" in str(异常.value.result)


@pytest.mark.asyncio
async def test_我的派生指令_私聊会被拒绝():
    事件 = Event()

    with pytest.raises(FinishedException) as 异常:
        await random_paro.my_paro_cmd.handlers[0](事件)

    assert "该指令仅支持群聊使用" in str(异常.value.result)


@pytest.mark.asyncio
async def test_预览排行指令_非超管群消息会直接忽略():
    事件 = Event(group_id=1001, user_id="12345")

    await random_paro.test_daily_rank_cmd.handlers[0](事件)
    await random_paro.test_history_rank_cmd.handlers[0](事件)
    await random_paro.test_daily_egg_rank_cmd.handlers[0](事件)
    await random_paro.test_history_egg_rank_cmd.handlers[0](事件)
    await random_paro.test_my_paro_cmd.handlers[0](事件)

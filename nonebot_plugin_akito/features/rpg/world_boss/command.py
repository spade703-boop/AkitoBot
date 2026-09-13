"""世界 BOSS 命令入口与播报辅助。"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Bot, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from ...gift.pages import build_world_boss_rank_page_data
from ...gift.render import render_bond_page
from . import settlement as service

LOCK = service.LOCK
SUPERUSER_QQ = service.SUPERUSER_QQ
_copy = service._copy
_error = service._error
_line = service._line
is_sleeping = service.is_sleeping


def _load_data():
    return service._load_data()


def _save_data(data):
    return service._save_data(data)


def _get_group(data, group_id):
    return service._get_group(data, group_id)


def _display_name(event):
    return service._display_name(event)


def _first_at_qq(message):
    return service._first_at_qq(message)


def _today_str():
    return service._today_str()


def _boss_at_line(key: str, ctx: dict):
    return service._render_with_ats(random.choice(service._copy(key)), ctx)


async def _render_world_boss_settlement_image(settlement: dict) -> bytes | None:
    rows = settlement.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return None
    try:
        page_data = build_world_boss_rank_page_data(settlement.get("monster", "世界BOSS"), rows)
        return await render_bond_page("world_boss_rank.html", page_data, viewport_width=760)
    except Exception as exc:
        service._logger.warning(f"world boss settlement render failed ({exc}), falling back to text")
        return None


def _merge_lines_with_optional_image(lines: list, image_bytes: bytes | None = None):
    msg = lines[0]
    for line in lines[1:]:
        msg = msg + "\n" + line
    if image_bytes is not None:
        msg = msg + "\n" + MessageSegment.image(image_bytes)
    return msg


_TEST_WORLD_BOSS_ROWS: list[dict] = [
    {
        "rank": 1,
        "uid": "10001",
        "name": "测试冒险者01",
        "damage": 1280,
        "damage_pct": 29,
        "exp": 126,
        "points": 15,
        "exp_bonus": 0,
        "points_bonus": 0,
        "bond": 1,
        "special_drop": "赤鳞龙鳞",
        "old_level": 9,
        "new_level": 10,
        "levelup": True,
        "levelup_text": "Lv9→Lv10",
        "last_hit": False,
    },
    {
        "rank": 2,
        "uid": "10002",
        "name": "测试冒险者02",
        "damage": 1186,
        "damage_pct": 27,
        "exp": 118,
        "points": 14,
        "exp_bonus": 0,
        "points_bonus": 0,
        "bond": 1,
        "special_drop": "",
        "old_level": 8,
        "new_level": 8,
        "levelup": False,
        "levelup_text": "",
        "last_hit": False,
    },
    {
        "rank": 3,
        "uid": "10003",
        "name": "测试冒险者03",
        "damage": 1014,
        "damage_pct": 23,
        "exp": 111,
        "points": 15,
        "exp_bonus": 8,
        "points_bonus": 2,
        "bond": 0,
        "special_drop": "",
        "old_level": 7,
        "new_level": 8,
        "levelup": True,
        "levelup_text": "Lv7→Lv8",
        "last_hit": True,
    },
    {
        "rank": 4,
        "uid": "10004",
        "name": "测试冒险者04",
        "damage": 462,
        "damage_pct": 10,
        "exp": 57,
        "points": 7,
        "exp_bonus": 0,
        "points_bonus": 0,
        "bond": 0,
        "special_drop": "",
        "old_level": 6,
        "new_level": 6,
        "levelup": False,
        "levelup_text": "",
        "last_hit": False,
    },
    {
        "rank": 5,
        "uid": "10005",
        "name": "测试冒险者05",
        "damage": 258,
        "damage_pct": 6,
        "exp": 39,
        "points": 5,
        "exp_bonus": 0,
        "points_bonus": 0,
        "bond": 1,
        "special_drop": "断潮虾壳",
        "old_level": 5,
        "new_level": 5,
        "levelup": False,
        "levelup_text": "",
        "last_hit": False,
    },
    {
        "rank": 6,
        "uid": "10006",
        "name": "测试冒险者06",
        "damage": 181,
        "damage_pct": 4,
        "exp": 31,
        "points": 4,
        "exp_bonus": 0,
        "points_bonus": 0,
        "bond": 1,
        "special_drop": "",
        "old_level": 4,
        "new_level": 4,
        "levelup": False,
        "levelup_text": "",
        "last_hit": False,
    },
]


world_boss_cmd = on_command("世界BOSS", priority=5, block=True)


@world_boss_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id, rejection = service._resolve_group(event)
    if rejection:
        await world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = service._today_str()
    async with service.LOCK:
        data = service._load_data()
        group = service._get_group(data, group_id)
        settlement_lines, changed = service._cleanup_stale_world_boss(group, today)
        if changed:
            service._save_data(data)
        lines = [*settlement_lines, *service._world_boss_status_lines(group, today)]
    await world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


force_world_boss_cmd = on_command("强制开启世界BOSS", priority=5, block=True)


@force_world_boss_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = service._resolve_group(event)
    if rejection:
        await force_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, _changed = service._cleanup_stale_world_boss(group, today)
        current = service._active_world_boss(group, today)
        if current:
            lines = [
                *settlement_lines,
                _line("world_boss_force_exists"),
                *service._world_boss_status_lines(group, today),
            ]
        else:
            spawned = service._spawn_world_boss(
                group,
                today,
                event.get_user_id(),
                rng=random,
                snapshot=service._force_world_boss_snapshot(group, today),
                forced=True,
            )
            _save_data(data)
            lines = [*settlement_lines, _line("world_boss_force_opened"), *service._world_boss_spawn_lines(spawned)]
    await force_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


test_world_rank_cmd = on_command("test世界排行", aliases={"测试世界排行"}, priority=5, block=True)


@test_world_rank_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = service._resolve_group(event)
    if rejection:
        await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    image_bytes = await service._render_world_boss_settlement_image(
        {
            "monster": "赤鳞灾龙",
            "rows": [row.copy() for row in _TEST_WORLD_BOSS_ROWS],
            "last_hit_uid": "10003",
            "last_hit_name": "测试冒险者03",
            "last_hit_reward": {"exp": 8, "points": 2},
            "lines": [_line("world_boss_kill"), _line("world_boss_last_hit", name="测试冒险者03")],
        }
    )
    if image_bytes is None:
        lines = [
            "测试世界排行图渲染失败，已切回文字预览。",
            *service._world_boss_reward_lines([row.copy() for row in _TEST_WORLD_BOSS_ROWS]),
        ]
        await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    msg = _merge_lines_with_optional_image(
        [_line("world_boss_kill"), _line("world_boss_last_hit", name="测试冒险者03")], image_bytes
    )
    await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + msg)


attack_world_boss_cmd = on_command("攻击世界BOSS", priority=5, block=True)


@attack_world_boss_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id, rejection = service._resolve_group(event)
    if rejection:
        await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    today = _today_str()
    settlement = None
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, changed = service._cleanup_stale_world_boss(group, today)
        boss = service._active_world_boss(group, today)
        if not boss:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(
                MessageSegment.reply(event.message_id) + "\n".join([*settlement_lines, _error("boss_none")])
            )
        user = service._ensure_player(group, user_id, _display_name(event))
        if user.get("equip_date") != today:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        participant = service._ensure_boss_participant(boss, user_id, user, today, rng=random)
        if participant is None:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if participant.get("equip_used") and not is_superuser:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_already_attacked"))
        dealt = service._apply_world_boss_damage(
            boss, {user_id: service._boss_damage(participant, user, today, rng=random)}
        ).get(str(user_id), 0)
        service._consume_equip(participant)
        service.record_world_boss_attack(group, today, user_ids=[user_id], damage=dealt, boss=boss)
        if int(boss.get("hp", 0)) <= 0:
            settlement = service._world_boss_kill_settlement(
                group, boss, last_hit_uids=boss.get("last_hit_uids") or boss.get("last_hit")
            )
            lines = list(settlement["lines"])
        else:
            lines = [
                _boss_at_line(
                    "world_boss_attack",
                    {
                        "a": user_id,
                        "monster": boss.get("name", "世界BOSS"),
                        "damage": dealt,
                        "hp": boss.get("hp", 0),
                        "max_hp": boss.get("max_hp", 0),
                    },
                )
            ]
        _save_data(data)
    settlement_image = await service._render_world_boss_settlement_image(settlement) if settlement else None
    if settlement and settlement_image is None:
        lines.extend(service._world_boss_reward_lines(settlement["rows"]))
    await attack_world_boss_cmd.finish(
        MessageSegment.reply(event.message_id) + _merge_lines_with_optional_image(lines, settlement_image)
    )


team_world_boss_cmd = on_command("组队世界BOSS", priority=5, block=True)


@team_world_boss_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id, rejection = service._resolve_group(event)
    if rejection:
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    initiator = event.get_user_id()
    is_superuser = initiator == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    target = _first_at_qq(getattr(event, "original_message", None))
    if not target or target == "all" or target == initiator or target == str(getattr(bot, "self_id", "")):
        return
    today = _today_str()
    settlement = None
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, changed = service._cleanup_stale_world_boss(group, today)
        boss = service._active_world_boss(group, today)
        if not boss:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(
                MessageSegment.reply(event.message_id) + "\n".join([*settlement_lines, _error("boss_none")])
            )
        b = service._ensure_player(group, initiator, _display_name(event))
        if b.get("equip_date") != today:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        a = service._ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if a.get("equip_date") != today:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))
        b_participant = service._ensure_boss_participant(boss, initiator, b, today, rng=random)
        a_participant = service._ensure_boss_participant(boss, target, a, today, rng=random)
        if b_participant is None:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if a_participant is None:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))
        if b_participant.get("equip_used") and not is_superuser:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_already_attacked"))
        if a_participant.get("equip_used"):
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_broken"))
        raw_intimacy = service._get_intimacy(group, initiator, target)
        bond_level = service._bond_level(raw_intimacy)["team_level"]
        success = random.random() < service._team_success_rate(bond_level)
        lines: list = []
        if success:
            raw_hits = {
                initiator: service._boss_damage(b_participant, b, today, rng=random),
                target: service._boss_damage(a_participant, a, today, rng=random),
            }
            team_hits, bonus_total = service._apply_team_bonus(raw_hits)
            dealt = service._apply_world_boss_damage(boss, team_hits)
            service._consume_equip(b_participant)
            service._consume_equip(a_participant)
            service.record_world_boss_attack(
                group, today, user_ids=[initiator, target], damage=sum(dealt.values()), boss=boss, event="team_attack"
            )
            kill_done = int(boss.get("hp", 0)) <= 0
            if kill_done:
                boss["last_hit_uids"] = [str(initiator), str(target)]
                boss.pop("last_hit", None)
            service._grant_world_boss_team_bond(
                group, boss, initiator, target, today, kill=kill_done, negative=raw_intimacy < 0
            )
            b_name = b.get("display_name") or f"群友{initiator}"
            if not kill_done:
                lines.append(
                    _boss_at_line(
                        "world_boss_team_attack",
                        {
                            "a": initiator,
                            "b": target,
                            "monster": boss.get("name", "世界BOSS"),
                            "a_name": b_name,
                            "b_name": a_name,
                            "a_damage": dealt.get(str(initiator), 0),
                            "b_damage": dealt.get(str(target), 0),
                            "total_damage": sum(dealt.values()),
                            "hp": boss.get("hp", 0),
                            "max_hp": boss.get("max_hp", 0),
                            "last_hit_name": f"{b_name} 与 {a_name}",
                        },
                    )
                )
            if bonus_total > 0 and int(boss.get("hp", 0)) > 0:
                lines.append(_line("world_boss_team_bonus", bonus_total=bonus_total))
        else:
            fail_event = service._roll_fail_flavor()
            if fail_event:
                lines.append(_line(f"world_boss_fail_event_{fail_event}", b_name=a_name))
            lines.append(
                _boss_at_line(
                    "world_boss_team_fail", {"a": initiator, "b_name": a_name, "monster": boss.get("name", "世界BOSS")}
                )
            )
            dealt = service._apply_world_boss_damage(
                boss, {initiator: service._boss_damage(b_participant, b, today, rng=random)}
            )
            service._consume_equip(b_participant)
            service.record_world_boss_attack(
                group,
                today,
                user_ids=[initiator],
                damage=sum(dealt.values()),
                boss=boss,
                event=f"team_fail:{fail_event}" if fail_event else "team_fail",
            )
            lines.append(
                _boss_at_line(
                    "world_boss_attack_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_attack",
                    {
                        "a": initiator,
                        "monster": boss.get("name", "世界BOSS"),
                        "damage": dealt.get(str(initiator), 0),
                        "hp": boss.get("hp", 0),
                        "max_hp": boss.get("max_hp", 0),
                    },
                )
            )
        if int(boss.get("hp", 0)) <= 0:
            settlement = service._world_boss_kill_settlement(
                group, boss, last_hit_uids=boss.get("last_hit_uids") or boss.get("last_hit")
            )
            lines = list(settlement["lines"])
        _save_data(data)
    settlement_image = await service._render_world_boss_settlement_image(settlement) if settlement else None
    if settlement and settlement_image is None:
        lines.extend(service._world_boss_reward_lines(settlement["rows"]))
    await team_world_boss_cmd.finish(
        MessageSegment.reply(event.message_id) + _merge_lines_with_optional_image(lines, settlement_image)
    )


service.world_boss_cmd = world_boss_cmd
service.force_world_boss_cmd = force_world_boss_cmd
service.test_world_rank_cmd = test_world_rank_cmd
service.attack_world_boss_cmd = attack_world_boss_cmd
service.team_world_boss_cmd = team_world_boss_cmd
service._render_world_boss_settlement_image = _render_world_boss_settlement_image
service._TEST_WORLD_BOSS_ROWS = _TEST_WORLD_BOSS_ROWS

__all__ = [
    "world_boss_cmd",
    "force_world_boss_cmd",
    "test_world_rank_cmd",
    "attack_world_boss_cmd",
    "team_world_boss_cmd",
]

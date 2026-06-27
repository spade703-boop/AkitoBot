"""商店：查看在售道具、用积分购买（每日限购）。买到的道具进背包，积分是与送礼共享的同一份。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _save_data,
    _today_str,
)
from .config import _error, _line
from .inventory import _add_item, _item_by_name, _items
from .player import _ensure_player, _resolve_group

# ==================== 指令：商店 ====================

shop_cmd = on_command("商店", aliases={"商店列表"}, priority=5, block=True)


@shop_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await shop_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    lines = ["🏪 冒险商店："]
    for it in _items():
        price = int(it.get("price", 0))
        if price <= 0:
            continue  # 非卖品（如仅掉落获取）不在货架
        limit = int(it.get("daily_buy_limit", 0))
        lim = f"（每日 {limit}）" if limit else ""
        lines.append(f"· {it['name']}　{price} 积分{lim}　{it.get('desc', '')}")
    if len(lines) == 1:
        lines.append("（货架空空，暂无在售）")
    lines.append("用法：购买 [道具名] [数量]")
    await shop_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：购买 ====================

buy_cmd = on_command("购买", aliases={"购买道具"}, priority=5, block=True)


@buy_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await buy_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if is_sleeping():
        await buy_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    parts = args.extract_plain_text().strip().split()
    if not parts:
        await buy_cmd.finish(MessageSegment.reply(event.message_id) + _error("buy_bad_qty"))
    name = parts[0]
    qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    if qty <= 0:
        await buy_cmd.finish(MessageSegment.reply(event.message_id) + _error("buy_bad_qty"))

    item = _item_by_name(name)
    if not item or int(item.get("price", 0)) <= 0:
        await buy_cmd.finish(MessageSegment.reply(event.message_id) + _error("shop_unknown", name=name))
    price = int(item["price"])
    limit = int(item.get("daily_buy_limit", 0))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))

        if user.get("buy_date") != today:
            user["buy_date"], user["buy_counts"] = today, {}
        buy_counts = user.setdefault("buy_counts", {})
        bought = int(buy_counts.get(name, 0))

        if limit and bought + qty > limit:
            result = _error("buy_limit", name=name, limit=limit)
        else:
            cost = price * qty
            total = int(user.get("points", 0))
            if total < cost:
                result = _error("buy_poor", name=name, qty=qty, cost=cost, total=total)
            else:
                user["points"] = total - cost
                _add_item(user, name, qty)
                buy_counts[name] = bought + qty
                _save_data(data)
                result = _line("buy_ok", name=name, qty=qty, cost=cost, total=user["points"])
    await buy_cmd.finish(MessageSegment.reply(event.message_id) + result)

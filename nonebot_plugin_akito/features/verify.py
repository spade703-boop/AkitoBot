import json
from pathlib import Path
import time

from nonebot import on_command, on_notice
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import Bot, GroupDecreaseNoticeEvent, GroupIncreaseNoticeEvent
from nonebot.log import logger
from nonebot.params import CommandArg

# ==============================================================================
# 1. 配置与数据加载区
# ==============================================================================
CONFIG_FILE = Path("data/verify_config.json")
VERIFY_FILE = Path("data/pending_verify.json")

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ 读取 verify_config.json 失败，使用默认配置: {e}")
        return {"TARGET_GROUP_ID": "1058884117", "ADMIN_GROUP_ID": "1078300612"}

config = load_config()
TARGET_GROUP_ID = config.get("TARGET_GROUP_ID")
ADMIN_GROUP_ID = config.get("ADMIN_GROUP_ID")

def load_verify_queue():
    if not VERIFY_FILE.exists(): return {}
    try:
        with open(VERIFY_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_verify_queue(data):
    VERIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VERIFY_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ==============================================================================
# 2. 核心监控逻辑区
# ==============================================================================
# [入群探头]
join_monitor = on_notice(priority=50, block=False)
@join_monitor.handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent):
    gid_str = str(event.group_id)
    if gid_str != TARGET_GROUP_ID: return

    data = load_verify_queue()
    if gid_str not in data: data[gid_str] = []

    uid_str = str(event.user_id)
    if not any(item['uid'] == uid_str for item in data[gid_str]):
        data[gid_str].append({"uid": uid_str, "join_time": time.time()})
        save_verify_queue(data)
        logger.info(f"📥 已将新人 {uid_str} 加入待核对名单")

# [退群探头]
leave_monitor = on_notice(priority=50, block=False)
@leave_monitor.handle()
async def _(bot: Bot, event: GroupDecreaseNoticeEvent):
    gid_str = str(event.group_id)
    if gid_str != TARGET_GROUP_ID: return

    data = load_verify_queue()
    if gid_str not in data: return

    uid_str = str(event.user_id)
    original_len = len(data[gid_str])
    data[gid_str] = [item for item in data[gid_str] if item['uid'] != uid_str]

    if len(data[gid_str]) < original_len:
        save_verify_queue(data)
        logger.info(f"🗑️ 新人 {uid_str} 退群，已清理名单")

# ==============================================================================
# 3. 指令交互区
# ==============================================================================
check_verify_cmd = on_command("待审核名单", aliases={"查自证", "新人名单"}, priority=5, block=True)
@check_verify_cmd.handle()
async def _(event: Event):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    data = load_verify_queue()
    pending_list = data.get(TARGET_GROUP_ID, [])
    if not pending_list:
        await check_verify_cmd.finish("（翻了翻名单）……目前没有需要核对自证的新人。")

    now_ts = time.time()
    msg = "📋 【停车场待核对名单】\n"
    for item in pending_list:
        uid = item['uid']
        diff = now_ts - item['join_time']
        days, hours, mins = int(diff // 86400), int((diff % 86400) // 3600), int((diff % 3600) // 60)

        if days > 0: time_tag = f"{days}天{hours}小时前"
        elif hours > 0: time_tag = f"{hours}小时{mins}分钟前"
        else: time_tag = f"{mins}分钟前" if mins > 0 else "刚刚"
        msg += f"- {uid} ({time_tag})\n"

    msg += "\n💡 确认收到后请发：通过审核/审核通过 [QQ号]"
    await check_verify_cmd.finish(msg)

pass_verify_cmd = on_command("通过审核", aliases={"审核通过", "自证通过"}, priority=5, block=True)
@pass_verify_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    target_qq = args.extract_plain_text().strip()
    if not target_qq: await pass_verify_cmd.finish("（皱眉）……你要划掉谁？发个QQ号过来。")

    data = load_verify_queue()
    if TARGET_GROUP_ID in data:
        original_len = len(data[TARGET_GROUP_ID])
        data[TARGET_GROUP_ID] = [item for item in data[TARGET_GROUP_ID] if item['uid'] != target_qq]
        if len(data[TARGET_GROUP_ID]) < original_len:
            save_verify_queue(data)
            await pass_verify_cmd.finish(f"✅ OK，已将 {target_qq} 从名单中划掉。")
        else:
            await pass_verify_cmd.finish("（查了查）名单里没这个号，是不是填错了？")

add_verify_cmd = on_command("手动添加", aliases={"添加待审核", "加入名单"}, priority=5, block=True)
@add_verify_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    raw_text = args.extract_plain_text().strip()
    if not raw_text:
        await add_verify_cmd.finish("（叹气）……你要加谁？把QQ号发过来，多个号用空格隔开。")

    # 用空格/换行符将输入的字符串打碎成列表
    target_qqs = raw_text.split()

    data = load_verify_queue()
    if TARGET_GROUP_ID not in data:
        data[TARGET_GROUP_ID] = []

    success_list = []
    duplicate_list = []
    invalid_list = []

    # 遍历处理每一个提取出来的 QQ 号
    for qq in target_qqs:
        if not qq.isdigit():
            invalid_list.append(qq)
            continue

        # 防止重复添加
        if any(item['uid'] == qq for item in data[TARGET_GROUP_ID]):
            duplicate_list.append(qq)
            continue

        # 加入成功名单
        data[TARGET_GROUP_ID].append({"uid": qq, "join_time": time.time()})
        success_list.append(qq)

    # 只要有成功添加的，就统一保存一次
    if success_list:
        save_verify_queue(data)
        logger.info(f"✍️ 管理员批量加入待核对名单: {success_list}")

    # 拼装详细的汇报文本
    msg = ""
    if success_list:
        msg += f"✅ 已成功将 {len(success_list)} 个号扔进名单：\n{', '.join(success_list)}\n"
    if duplicate_list:
        msg += f"⚠️ 这 {len(duplicate_list)} 个号已经在名单里了，帮你跳过了：\n{', '.join(duplicate_list)}\n"
    if invalid_list:
        msg += f"❌ 还有 {len(invalid_list)} 个格式不对（带有非数字），已忽略：\n{', '.join(invalid_list)}"

    if not msg:
        msg = "（皱眉）……你发了一堆什么乱七八糟的，没有一个有效的QQ号。"

    await add_verify_cmd.finish(msg.strip())

# ==============================================================================
# 4. 羁绊刷取管理区 (冲榜专属)
# ==============================================================================
BOND_FILE = Path("data/bond_verify.json")

def load_bond_queue():
    if not BOND_FILE.exists(): return {}
    try:
        with open(BOND_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_bond_queue(data):
    BOND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BOND_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# [羁绊名单：手动批量添加]
# [羁绊名单：一键转移/添加]
add_bond_cmd = on_command("缺羁绊", aliases={"刷羁绊", "没刷羁绊", "刷羁绊添加"}, priority=5, block=True)
@add_bond_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    raw_text = args.extract_plain_text().strip()
    if not raw_text:
        await add_bond_cmd.finish("（抱臂）……谁没刷够羁绊？把QQ号发过来，多个号用空格隔开。")

    target_qqs = raw_text.split()

    # 同时加载两个名单的数据
    bond_data = load_bond_queue()
    verify_data = load_verify_queue()

    if TARGET_GROUP_ID not in bond_data: bond_data[TARGET_GROUP_ID] = []

    success_list, duplicate_list = [], []
    transferred_count = 0  # 记录成功从原名单里挪出来的人数

    for qq in target_qqs:
        if not qq.isdigit(): continue

        # 1. 核心联动：自动从原【待审核名单】中删掉这个号
        if TARGET_GROUP_ID in verify_data:
            original_len = len(verify_data[TARGET_GROUP_ID])
            verify_data[TARGET_GROUP_ID] = [item for item in verify_data[TARGET_GROUP_ID] if item['uid'] != qq]
            if len(verify_data[TARGET_GROUP_ID]) < original_len:
                transferred_count += 1

        # 2. 加入【羁绊名单】
        if any(item['uid'] == qq for item in bond_data[TARGET_GROUP_ID]):
            duplicate_list.append(qq)
            continue

        bond_data[TARGET_GROUP_ID].append({"uid": qq, "join_time": time.time()})
        success_list.append(qq)

    # 只要有任何变动，就保存数据
    if success_list or transferred_count > 0:
        if success_list: save_bond_queue(bond_data)
        if transferred_count > 0: save_verify_queue(verify_data)

        msg = "✅ 名单转移更新完毕：\n"
        if success_list:
            msg += f"🎸 已将 {len(success_list)} 人挂入待刷羁绊列表：\n{', '.join(success_list)}\n"
        if transferred_count > 0:
            msg += f"🗑️ （系统已自动将其中 {transferred_count} 人从初始自证名单中划除）\n"
        if duplicate_list:
            msg += f"⚠️ 其余 {len(duplicate_list)} 人已在羁绊列表中。"

        await add_bond_cmd.finish(msg.strip())
    else:
        await add_bond_cmd.finish("（皱眉）……操作失败，你要加的人已经在羁绊名单里了。")


# [羁绊名单：查看]
check_bond_cmd = on_command("羁绊名单", aliases={"查羁绊", "谁要刷羁绊"}, priority=5, block=True)
@check_bond_cmd.handle()
async def _(event: Event):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    data = load_bond_queue()
    pending_list = data.get(TARGET_GROUP_ID, [])
    if not pending_list:
        await check_bond_cmd.finish("（看了一眼屏幕）……目前没人排队刷羁绊。")

    now_ts = time.time()
    msg = f"🎸 【{TARGET_GROUP_ID} 羁绊待刷名单】\n"
    for item in pending_list:
        uid = item['uid']
        diff = now_ts - item['join_time']
        mins = int(diff // 60)
        time_tag = f"{mins//60}h{mins%60}m前" if mins >= 60 else f"{mins}m前"
        msg += f"- {uid} (已等:{time_tag})\n"

    msg += "\n💡 刷完请发：羁绊通过/通过羁绊 [QQ号]"
    await check_bond_cmd.finish(msg)

# [羁绊名单：通过/划掉]
pass_bond_cmd = on_command("羁绊通过", aliases={"羁绊完成", "通过羁绊"}, priority=5, block=True)
@pass_bond_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    target_qq = args.extract_plain_text().strip()
    if not target_qq: await pass_bond_cmd.finish("（不耐烦）……谁刷完了？把号给我。")

    data = load_bond_queue()
    if TARGET_GROUP_ID in data:
        original_len = len(data[TARGET_GROUP_ID])
        data[TARGET_GROUP_ID] = [item for item in data[TARGET_GROUP_ID] if item['uid'] != target_qq]
        if len(data[TARGET_GROUP_ID]) < original_len:
            save_bond_queue(data)
            await pass_bond_cmd.finish(f"✅ 知道了，{target_qq} 已从羁绊名单划掉。")
        else:
            await pass_bond_cmd.finish("（翻了翻）羁绊名单里没这个号啊？")

# ==============================================================================
# 5. 自定义理由延期/挂起区
# ==============================================================================
HOLD_FILE = Path("data/hold_verify.json")

def load_hold_queue():
    if not HOLD_FILE.exists(): return {}
    try:
        with open(HOLD_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_hold_queue(data):
    HOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HOLD_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# [自定义挂起：智能转移/添加]
add_hold_cmd = on_command("特殊挂号", aliases={"延期审核", "加备注转出"}, priority=5, block=True)
@add_hold_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    raw_text = args.extract_plain_text().strip()
    if not raw_text:
        await add_hold_cmd.finish("（叹气）……格式：特殊挂号 [QQ号] [理由]。多个QQ号和理由用空格隔开就行。")

    # 智能分词核心逻辑
    parts = raw_text.split()
    target_qqs = [p for p in parts if p.isdigit()]        # 把所有纯数字提出来当做 QQ 号
    reason_parts = [p for p in parts if not p.isdigit()]  # 把所有非数字提出来当做理由

    if not target_qqs:
        await add_hold_cmd.finish("（皱眉）……你发了一堆字，但没有包含任何纯数字的QQ号，我怎么知道你要挂起谁？")

    # 将提取出来的文字碎片拼成完整的理由，如果没有则提供默认值
    custom_reason = " ".join(reason_parts) if reason_parts else "其他/未备注原因"

    hold_data = load_hold_queue()
    verify_data = load_verify_queue()

    if TARGET_GROUP_ID not in hold_data: hold_data[TARGET_GROUP_ID] = []

    success_list, duplicate_list = [], []
    transferred_count = 0

    for qq in target_qqs:
        # 1. 自动从初始【待审核名单】中删掉这个号
        if TARGET_GROUP_ID in verify_data:
            original_len = len(verify_data[TARGET_GROUP_ID])
            verify_data[TARGET_GROUP_ID] = [item for item in verify_data[TARGET_GROUP_ID] if item['uid'] != qq]
            if len(verify_data[TARGET_GROUP_ID]) < original_len:
                transferred_count += 1

        # 2. 查重
        if any(item['uid'] == qq for item in hold_data[TARGET_GROUP_ID]):
            duplicate_list.append(qq)
            continue

        # 3. 加入新名单（带上 custom_reason 字段）
        hold_data[TARGET_GROUP_ID].append({"uid": qq, "join_time": time.time(), "reason": custom_reason})
        success_list.append(qq)

    # 只要有任何变动，就保存数据
    if success_list or transferred_count > 0:
        if success_list: save_hold_queue(hold_data)
        if transferred_count > 0: save_verify_queue(verify_data)

        msg = f"✅ 已将 {len(success_list)} 人转入【特殊挂起】名单，备注：[{custom_reason}]\n"
        if transferred_count > 0: msg += f"🗑️ （系统已自动从初始名单划除 {transferred_count} 人）\n"
        if duplicate_list: msg += f"⚠️ 另有 {len(duplicate_list)} 人已在挂起列表中。"

        await add_hold_cmd.finish(msg.strip())
    else:
        await add_hold_cmd.finish("操作失败，你要加的人已经都在挂起名单里了。")


# [挂起名单：查看]
check_hold_cmd = on_command("挂号名单", aliases={"延期名单", "特殊名单"}, priority=5, block=True)
@check_hold_cmd.handle()
async def _(event: Event):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    data = load_hold_queue()
    pending_list = data.get(TARGET_GROUP_ID, [])
    if not pending_list:
        await check_hold_cmd.finish("（查了一下）……目前没有因为特殊原因挂起的新人。")

    now_ts = time.time()
    msg = f"📌 【{TARGET_GROUP_ID} 特殊延期名单】\n"
    for item in pending_list:
        uid = item['uid']
        reason = item.get('reason', '未备注原因')  # 读取自定义理由
        diff = now_ts - item['join_time']
        mins = int(diff // 60)
        time_tag = f"{mins//60}h{mins%60}m" if mins >= 60 else f"{mins}m"

        # 格式化输出：时间戳后面紧跟自定义理由
        msg += f"- {uid} ({time_tag}前) [{reason}]\n"

    msg += "\n💡 处理完毕请发：延期通过 [QQ号]"
    await check_hold_cmd.finish(msg)


# [挂起名单：通过/划掉]
pass_hold_cmd = on_command("挂号通过", aliases={"延期通过", "挂号清理"}, priority=5, block=True)
@pass_hold_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(getattr(event, "group_id", "")) != ADMIN_GROUP_ID: return

    target_qq = args.extract_plain_text().strip()
    if not target_qq: await pass_hold_cmd.finish("（不耐烦）……谁处理完了？发号码过来。")

    data = load_hold_queue()
    if TARGET_GROUP_ID in data:
        original_len = len(data[TARGET_GROUP_ID])
        data[TARGET_GROUP_ID] = [item for item in data[TARGET_GROUP_ID] if item['uid'] != target_qq]
        if len(data[TARGET_GROUP_ID]) < original_len:
            save_hold_queue(data)
            await pass_hold_cmd.finish(f"✅ OK，{target_qq} 已从特殊延期名单中划掉。")
        else:
            await pass_hold_cmd.finish("（翻了翻）特殊名单里没这个号啊？")

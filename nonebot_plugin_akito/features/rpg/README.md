# RPG 子系统（精简版）

在「送礼」社交玩法之上扩展的轻量群文字 RPG。设计原则：**每天只做两件事，少刷屏**。

- **签到** → 领积分 + 经验 + 今日装备
- **打怪** → 用今日装备挑战野怪，赢经验与掉落

角色对外只有「等级」一个数值；战力是今日装备的隐藏值；运势是隐藏值（只暗中影响打怪）。

---

## 指令说明表

> RPG 指令；积分与「送礼」系统共享同一份账本。睡眠时段（0–6 点）会拦截写操作（超管除外）。

| 指令 | 别名 | 功能 | 运作方式 |
|---|---|---|---|
| `签到` | （由送礼系统提供） | 领积分 + 经验 + 今日装备 | 送礼系统发积分；RPG 钩子在同一次签到里：暗掷当日运势（隐藏）、发固定经验（`signin.exp`）、发一套**今日装备**（战力随当前等级涨 + 随机浮动）。每日一次。 |
| `打怪` | `打野`、`挑战` | 用今日装备挑战随机野怪 | 需「今日装备未损坏」（即今天签到过且还没打）。判定：今日装备战力 × 随机系数 × 隐藏运势系数 × 随机事件，与野怪 `power_req` 比胜负；**胜多输少**经验（按等级）、按掉率出道具（胜负 / 运势影响掉率）。打完**装备损坏**，次日签到再领。每日一次。 |
| `强化` | `锻造`、`强化装备` | 花积分提升今日装备战力 | 唯一的积分出口。逐次涨价（第 n 次 = `forge.cost_base × n`）、每日上限 `forge.max_per_day`、次日随装备重置。提高当天打怪胜率。 |
| `我的角色` | `角色`、`状态`、`角色面板` | 查看角色 | 显示 等级（经验进度）、今日装备状态（已就绪 / 已强化 ×N / 已损坏 / 未签到）、积分、背包件数。**战力为隐藏值，不显示数字。** |
| `背包` | `我的背包`、`道具` | 查看道具 | 列出背包里的道具与数量。 |
| `使用 [道具名]` | — | 使用消耗品 | `双倍经验卡`（下次打怪经验 ×2）、`经验书`（立即获得经验）。 |
| `冒险帮助` | `打怪帮助`、`冒险说明` | 指令帮助 | 列出以上指令。 |

野怪/道具掉落与数值见 `data/content/rpg_config.json`，改完发 `重载配置` 即热更（无需重启）。

---

## 模块架构

```
core/game_store.py            共享存储层：积分 / 亲密度 / 每日数据 / 锁 / 加权随机 / @渲染 / 群校验
                              —— 送礼(gift) 与 RPG 共用同一份 gift_data.json + 同一把锁；并提供签到钩子注册表
features/rpg/
├── __init__.py               导入各子模块（注册指令 + 签到钩子）；re-export on_signin / reload_rpg_config
├── config.py                 全部数值 / 文案 / 配置（DEFAULT_RPG_CONFIG，可被 rpg_config.json 覆盖热重载）；_cfg/_copy/_error/_line
├── player.py                 经验→等级派生；今日装备 helper（发放/战力/损坏/状态）；_combat_power；群校验 _resolve_group
├── fortune.py                隐藏运势掷取（含连签保底 / 大凶转大吉修正）+ 签到钩子 on_signin（暗掷运势 + 发经验 + 发今日装备）
├── hunt.py                   `打怪` 指令：遭遇 / 随机事件 / 胜负 / 经验 / 掉落
├── smith.py                  `强化` 指令（积分出口）
├── inventory.py              `背包` / `使用` 指令 + 道具效果 + 打怪掉落 helper
└── character.py              `我的角色` 面板 + `冒险帮助`
```

**依赖方向**：`features/gift.py` 与 `features/rpg/*` 都依赖 `core/game_store.py`，两者之间不互相 import。
签到的衔接走 **钩子注册表**解耦：`fortune.on_signin` 在 import 时 `register_signin_hook` 注册；
送礼系统的 `签到` 结算时调用 `run_signin_hooks(...)` 回调它（`gift.py` 不依赖 rpg）。

**数据流**：
- 签到：`gift.签到` →（持锁）`run_signin_hooks` → `fortune.on_signin` → `player._grant_equip`（发今日装备）。
- 打怪：`hunt` → `player._combat_power`（今日装备战力）+ `fortune` 运势系数 + `inventory._roll_drops/_add_item`（掉落）。

**玩家数据**（存于 `gift_data.json` 的 `users[uid]` 内，与送礼共用一条记录）：
- 共享：`points`（积分）、`display_name` 等（送礼系统维护）。
- RPG：`exp`（经验，→等级）、`inventory`（背包）、`fortune/fortune_date/last_fortune/no_lucky_streak`（隐藏运势）、
  `equip_date/equip_level/equip_roll/equip_forge/equip_used`（今日装备）、`exp_buff_uses/exp_buff_mult`（双倍经验卡）。
- 字段都挂在 user 记录内，`game_store._normalize_data` 原样保留，天然持久化。

---

## 测试

`tests/test_rpg.py`：纯逻辑（等级/今日装备/运势/胜负/掉落/强化）+ 指令行为（签到钩子/打怪/强化/面板/背包）。
数值断言一律从 `_cfg(...)` 读，调数值不会让测试变脆。

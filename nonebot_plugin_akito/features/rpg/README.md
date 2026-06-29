# RPG 子系统（精简版）

在「送礼」社交玩法之上扩展的轻量群文字 RPG。设计原则：**每天只做两件事，少刷屏**。
它不是送礼的附庸，而是和送礼并列的一条积分去向：给手里有分、但一时没地方送礼的人一个稳定好玩的消耗口。

- **签到** → 领积分 + 经验（+ 连签递增）+ 今日装备
- **打怪** → 用今日装备挑战野怪，赢经验、积分与掉落；偶遇**精英怪**、撞上**今日增益**（藏着不外显）
- **组队**（可选）→ 打怪前 `组队@某人` 合力，羁绊越深越容易拉动，串起「送礼攒羁绊 → 组队」闭环
- **攀比层** → `排行榜` 看等级榜、`我的角色` 显示**称号 + 战绩**（都按需查询、不刷屏）

角色对外只有「等级」一个数值；战力是今日装备的隐藏值；运势/今日增益是隐藏值（只暗中影响打怪）。

---

## 指令说明表

> RPG 指令；积分与「送礼」系统共享同一份账本。睡眠时段（0–6 点）会拦截写操作（超管除外）。

| 指令 | 说明 |
|---|---|
| `签到` | （由送礼系统提供）领积分 + 经验 + 今日装备；送礼系统发积分，RPG 钩子同时暗掷运势、发经验和装备。每日一次 |
| `今日打怪` | 用今日装备出门打怪，胜败均有经验和积分、概率掉道具；遇精英怪/今日增益藏着不外显；低等级先碰温和怪；打完装备损耗，每日一次 |
| `组队@某人` | 拉群友合力打怪，直接 @ 即组队；成功率绑羁绊等级（满羁绊≈必成）；成功双方各得经验积分掉落，失败退化为发起人单刷（队友无损） |
| `强化今日装备` | 花积分把今天装备提一提；分段收费 `[60,150,300]`，每日限 3 次，次日重置 |
| `我的角色` | 看等级/称号/战绩/装备状态/积分/背包，战力为隐藏值不显示 |
| `群排行榜` | 本群冒险者经验 Top 10，纯文字不 @ 不出图 |
| `我的背包` | 列出道具与数量 |
| `使用 [道具名]` | 用消耗品：`双倍经验卡`（下次经验 ×2）、`经验书`（立即加经验） |
| `冒险帮助` | 列出以上所有指令 |

野怪/道具掉落与数值见 `data/content/rpg_config.json`，改完发 `重载配置` 即热更（无需重启）。

---

## 模块架构

```
core/game_store.py            共享存储层：积分 / 亲密度 / 每日数据 / 锁 / 加权随机 / @渲染 / 群校验
                              —— 送礼(gift) 与 RPG 共用同一份 gift_data.json + 同一把锁；并提供签到钩子注册表
features/rpg/
├── __init__.py               导入各子模块（注册指令 + 签到钩子）；re-export on_signin / reload_rpg_config
├── config.py                 全部数值 / 文案 / 配置（DEFAULT_RPG_CONFIG，可被 rpg_config.json 覆盖热重载）；_cfg/_copy/_error/_line
├── player.py                 经验→等级派生；称号派生 `_title_of`；今日装备 helper；_combat_power；群校验 _resolve_group
├── fortune.py                隐藏运势掷取（含连签保底 / 大凶转大吉修正）+ 签到钩子 on_signin（暗掷运势 + 发经验[含连签] + 发今日装备）
├── hunt.py                   `打怪` 指令 + 战斗结算（精英 `_pick_encounter` / 今日增益 `_today_buff` / 单刷 `_settle_solo` / 组队合力 `_settle_coop` / 发奖 `_apply_rewards` 共用）
├── team.py                   `组队@某人` 指令：羁绊定成功率、失败退化单刷（复用 hunt 结算）
├── smith.py                  `强化` 指令（积分出口）
├── inventory.py              `背包` / `使用` 指令 + 道具效果 + 打怪掉落 helper
└── character.py              `我的角色` 面板（含称号/战绩）+ `排行榜`（等级榜）+ `冒险帮助`
```

**依赖方向**：`features/gift.py` 与 `features/rpg/*` 都依赖 `core/game_store.py`。
签到的衔接走 **钩子注册表**解耦：`fortune.on_signin` 在 import 时 `register_signin_hook` 注册；
送礼系统的 `签到` 结算时调用 `run_signin_hooks(...)` 回调它（**`gift.py` 不依赖 rpg**）。
组队需要「羁绊等级」，故 `team.py` 引入一条 **rpg→gift 单向依赖**（`from ..gift import _bond_level`，消费 gift 拥有的羁绊体系）；gift 仍不反向依赖 rpg，无环。

**数据流**：
- 签到：`gift.签到` →（持锁）`run_signin_hooks` → `fortune.on_signin` → `player._grant_equip`（发今日装备）。
- 打怪：`hunt` → `player._combat_power`（今日装备战力）+ `fortune` 运势系数 + `inventory._roll_drops/_add_item`（掉落）+ 少量积分。
- 组队：`team` → `game_store._get_intimacy` + `gift._bond_level`（定成功率）→ `hunt._settle_coop`（成）/ `hunt._settle_solo`（败，单刷）。

**玩家数据**（存于 `gift_data.json` 的 `users[uid]` 内，与送礼共用一条记录）：
- 共享：`points`（积分）、`display_name` 等（送礼系统维护）。
- RPG：`exp`（经验，→等级）、`inventory`（背包）、`fortune/fortune_date/last_fortune/no_lucky_streak`（隐藏运势）、
  `equip_date/equip_level/equip_roll/equip_forge/equip_used`（今日装备）、`exp_buff_uses/exp_buff_mult`（双倍经验卡）、
  `hunt_total/hunt_wins`（战绩，喂排行榜/面板）、`signin_streak/signin_last_date`（连续签到）。
- 称号（按等级派生）与今日增益（按日期算）**不落库**。字段都挂在 user 记录内，`game_store._normalize_data` 原样保留，天然持久化。

---

## 测试

`tests/test_rpg.py`：纯逻辑（等级/今日装备/运势/胜负/掉落/强化/组队成功率/称号分档/连签递增·断签/今日增益按日期确定）
+ 指令行为（签到钩子含连签/打怪含积分·精英·今日增益/战绩计数/强化/面板含称号战绩/排行榜排序·过滤·空榜/背包/组队成功·失败退化单刷）。
数值断言一律从 `_cfg(...)` 读，调数值不会让测试变脆。

> 组队复用「送礼」**现有**的羁绊梯（6 档正向，顶档「从今往后直到永远」=Lv6），送礼数值平衡由 `gift` 自身维护——
> `_team_success_rate(Lv6)=0.35+5*0.12=0.95` 封顶，即满羁绊≈必成功；rpg 不再 fork 送礼的礼物/门槛数值。

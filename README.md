# AkitoBot — 东云彰人 AI Roleplay Bot

基于 [NoneBot2](https://nonebot.dev) + OneBot V11 的「初音未来：缤纷舞台」同人角色扮演 QQ 机器人，以「东云彰人」身份在群内进行 AI 驱动的沉浸式互动。

- **CP 立场**：彰冬（不拆不逆）
- **AI 后端**：DeepSeek API（对话）/ 智谱 GLM-4V（图片识别）/ Tavily（联网搜索）
- **当前版本**：0.3.2

---

## 快速开始

### 1. 环境要求

- Python ≥ 3.9
- 一个 NoneBot2 兼容的 OneBot V11 实现（[NapCat](https://github.com/NapNeko/NapCatQQ)、[LLOneBot](https://github.com/LLOneBot/LLOneBot) 等），并配置好正向 WebSocket

### 2. 安装依赖

本项目用 `pyproject.toml` 声明依赖（无 `requirements.txt`），直接 pip 安装：

```bash
pip install "nonebot2[fastapi]" nonebot-adapter-onebot openai python-dotenv Pillow aiohttp \
            numpy nonebot-plugin-htmlrender nonebot-plugin-alconna nonebot-plugin-apscheduler nonebot-plugin-uninfo
```

> `nonebot-plugin-htmlrender` 首次运行会自动下载 Playwright 浏览器内核（用于图库清单等 HTML 渲染）。

### 3. 配置

复制 `.env.example` 为 `.env`，至少填好 OneBot 连接和三个 API Key：

```ini
# OneBot 连接（指向你的 NapCat/LLOneBot 正向 WS 端口）
ONEBOT_WS_URLS=["ws://127.0.0.1:3000"]

# API Keys
DEEPSEEK_API_KEY=sk-your-deepseek-key    # DeepSeek 对话
TAVILY_API_KEY=tvly-your-tavily-key      # Tavily 联网搜索
ZHIPU_API_KEY=your-zhipu-key             # 智谱 GLM-4V 图片识别
SILICONFLOW_API_KEY=sk-your-siliconflow  # SiliconFlow embedding（语义检索，可选）

# 管理
SUPERUSER_QQ=你的QQ号                     # 超管：重置 / 热重载 / WL2 等
TOYA_QQ_ID=冬弥的QQ号                     # 影响 CP 模式触发
```

群组白名单见下文「群组白名单配置」。

### 4. 运行

```bash
python bot.py        # 或：nb run
```

---

## 功能概览

### 核心对话

| 触发方式 | 说明 |
|----------|------|
| `小彰 [文本]` / `东云小彰 [文本]` | 以彰人身份进行角色扮演对话 |

**对话引擎特性**：
- 联网搜索（双轨触发）：① 命中信息类关键词（搜/查/天气/新闻/多少钱…）确定性强制联网；② 其余情况由 LLM 通过 ReAct Function Calling 自主决定是否搜索。两条路都会把搜索结果回灌进人设提示重新生成，由彰人用自己的语气复述，不直出原始摘要
- 语义检索（RAG）：BGE-M3 自动匹配相关剧本示例 + PJSK 黑话，替代全量随机注入；未配置则自动降级回静态行为
- 时间流逝感知：跨时段对话自动切换场景，不会续接久远话题
- 睡眠系统：凌晨 0–6 点自动进入休眠状态，搜索类请求会「被迫营业」
- 节假日感知：自动感知日本节日气氛
- 晨跑状态：早上 6 点自动进入晨跑角色状态
- 冬弥联动：提及冬弥时开启护短 / CP 模式，并锚定当前作息推断冬弥去向、整轮保持一致

### 图片识别

发送图片时自动调用智谱 GLM-4.6V-Flash（免费，开启深度思考）进行二次元特化识别，支持：
- PJSK 全 26 角色识别（5 团 20 人 + 6 名虚拟歌手）
- 彰人 / 冬弥 / 合照精准防伪：模型输出结构化 JSON + 布尔发色特征，由代码侧裁决，证据不足一律降级，杜绝 OCR 文本误触发角色分支
- 多图识别（单条消息最多 3 张一次分析）与动图多帧理解（自动抽首/中/尾帧）
- 周边谷子识别（吧唧 / 立牌 / 橡胶挂件）
- 游戏截图识别 + OCR 文字提取（截图类自动追加一次高清专项 OCR）

### 印象系统（`群印象`）

- 读取目标用户最近 50 条发言，AI 生成盐系侧写
- 支持 @ 查看他人印象
- 3% 概率随机插嘴

### 派生抽取器（`抽派生`）

服务于 CP 同人创作：从彰人 / 冬弥双池各随机抽取一个平行宇宙身份，拼合为配对灵感。

| 指令 | 说明 |
|------|------|
| `抽派生` | 双方随机抽取 |
| `抽派生 彰人 XX` | 彰人固定为 XX，冬弥随机 |
| `抽派生 冬弥 XX` | 冬弥固定为 XX，彰人随机 |
| `/添加彰人派生 [名称]` | 向彰人池添加派生（**超管**） |
| `/添加冬弥派生 [名称]` | 向冬弥池添加派生（**超管**） |
| `/删除彰人派生 [名称]` | 按名称删除（**超管**） |
| `/删除冬弥派生 [名称]` | 按名称删除（**超管**） |
| `/查看彰人派生` | 查看彰人派生池（图片输出） |
| `/查看冬弥派生` | 查看冬弥派生池（图片输出） |

- 30 分钟内最多抽取 3 次
- 3% 概率触发做饭彩蛋
- 模糊匹配：输入支持大小写不敏感、前缀 / 包含匹配（如 `wl` → `WL2彰`，`黑百` → `黑百合`）；多个匹配时列出候选
- 头像拼合：将 `data/images/paro_avatars/彰人/` 和 `data/images/paro_avatars/冬弥/` 下的对应图片自动拼合输出

### 今日关键词（`今日关键词`）

服务于同人写作灵感：从关键词池中随机抽取 1–3 个意象 / 情境 / 关系张力短语，作为同人文创作的核心 motif。

| 指令 | 说明 |
|------|------|
| `今日关键词` | 随机抽取 1–3 个关键词，群内同日不放回，每人每日限 1 次 |
| `/查看关键词` | 查看全部关键词池（图片输出） |
| `/添加关键词 [名称]` | 添加新关键词（**超管**） |
| `/删除关键词 [名称]` | 按名称删除，支持模糊匹配（**超管**） |

- 仅支持群聊使用；普通用户同一天在同一群不会抽到别人已经抽过的词
- 每人每日 1 次，基于日期比较自动跨天失效；当日群内词池抽空后需等到次日再抽
- 超管仍可无限抽取，但不占用群内当天词池
- 关键词涵盖科学隐喻、病症设定、自然意象、画面场景、文学化用、关系张力六大类
- PIL 卡片式渲染输出

### 送礼系统（`送礼`）

群友互送的轻量社交小游戏：签到攒积分，`送礼 @对方` 由系统从你买得起的礼物里随机送一份（越贵的越容易抽中），累积两个群友之间的「同好羁绊」。礼物与文案均为彰冬同人圈主题。

| 指令 | 说明 |
|------|------|
| `签到` | 每天 1 次领取积分（赚取入口），随机 50–100 分；重复签到静默不应答；回复有 3–5 秒随机延迟（错开另一个签到 bot） |
| `送礼 @对方` | 每天 1 次，从你当前积分买得起的礼物里随机送一份给对方（越贵的越容易抽中） |
| `偷 @对方`（`偷积分`） | 每天 2 次，小概率顺走对方少量积分；有强保护，且每次都会掉一点和对方的羁绊（偷越亲近掉越多） |
| `我的积分`（`积分`） | 查看自己的积分与今日可签到 / 可送礼状态 |
| `礼物列表`（`送礼帮助`） | 查看礼物档位与价位 |
| `亲密度 [@对方]`（`羁绊`） | 查看与某人的同好羁绊：等级称号 + 距下一级进度 + 分方向送礼次数；不带 @ 则列出自己羁绊最高的几位 |
| `亲密度排行`（`羁绊排行`） | 本群同好羁绊对子排行（带等级称号） |
| `重置送礼` | 清空本群送礼 / 积分 / 羁绊数据（**超管**） |

- 礼物 10 档，按「心意 / 稀有度」递增（无料 → 谷子 → 豆豆眼 → 亚克力立牌 → 同人本 → 画集 → 约稿点图 → 手办 → 自己产的彰冬饭 → 彰冬婚礼邀请函）；越贵的礼基础羁绊越高，攒钱送贵礼更划算
- 送礼按权重触发随机事件：普通 / 暴击（羁绊翻倍）/ 回礼（对方随机回赠 5 种彰冬周边之一，礼尚往来额外加羁绊、稀有档还退部分积分，全部可在 `return_gifts` 配置增删调）/ 失败（不涨羁绊，退回部分积分安慰，比例见 `fail_refund_ratio`）/ 意外（9 种小概率：加赠·稀有·手写卡·被夸·上头·迟到·翻车·撞款·寄丢，偏正向小惊喜，羁绊随礼物档位缩放、部分返还积分，全部可在 `mishaps` 配置增删调）
- 保证礼「自己产的彰冬饭」「彰冬婚礼邀请函」一旦抽中**必定惊喜升级**（不暴击不失败、固定加满羁绊）；婚礼邀请函是终极礼（1112 消耗 / 羁绊 +1314·一生一世），数值与文案随礼物条目配置（`special`/`copy`）
- 羁绊分 6 级：初识 → 相熟 → 要好 → 挚友 → 知己 → 莫逆之交；`亲密度 @某人` 显示当前等级、距下一级进度、以及你和 ta 分方向的送礼次数（门槛/称号见 `bond_levels`，可改可热重载）
- `偷`：对抗玩法（轻量·欢乐向）——得手 / 被抓（倒赔对方）/ 扑空 / 反被顺 四种结果；金额小且封顶、低分免疫、每日被偷上限、签到后保护期；**偷必掉羁绊、偷越亲近掉越多**（社交代价当刹车）；偷多了羁绊会跌进负数（结怨：有过节 → 结了梁子 → 宿敌，封底 -1000）。数值/文案见 `steal` 配置
- 仅白名单群可用（与聊天同一白名单）；亲密度按群隔离；每人每天各 1 次签到与送礼（超管不限次，便于测试），基于日期自动跨天刷新
- 0–6 点（北京时间）小彰睡觉，签到 / 送礼 / 偷会收到固定回复、暂不结算（接入全局 `is_sleeping()`）；查询类（积分 / 亲密度 / 排行）照常。超管不受睡眠限制，便于测试
- 礼物档位 / 事件权重 / 签到积分 / 羁绊等级 / 偷窃参数 / 全部文案都在 `data/content/gift_config.json`（可热重载，仓库附带模板），缺省值同时内置于代码
- 「重置本群签到 / 重置全群签到 / 重置签到次数」可仅清掉本群当日签到闸门，不碰 RPG 的连签和装备状态（**超管**）

### RPG 冒险系统（`打怪` / `组队` / `强化` / `背包` / `排行榜`）

在送礼社交之上的轻量群文字 RPG。与送礼共享同一套积分账本，签到同时触发两种结算（积分 + 经验 + 今日装备）。设计原则：**每天就两步——先签到领装备，再决定今天要不要出去打一趟。**

| 指令 | 说明 |
|------|------|
| `签到` | （送礼系统提供）领积分 + 经验 + 暗掷当日运势（隐藏）+ 今日装备（战力随等级涨、随机浮动）；连签有额外经验递增 |
| `今日打怪` | 用今日装备出门打怪，胜败均有经验和积分、概率掉道具；遇精英怪/今日增益藏着不外显；低等级先碰温和怪；打完装备损耗，每日一次 |
| `组队 @某人` | 拉群友合力打怪，直接 @ 即组队；成功率绑羁绊等级（满羁绊≈必成）；成功双方各得经验积分掉落，失败退化为发起人单刷（队友无损） |
| `强化今日装备` | 花积分把今天装备提一提；分段收费 `[60,150,300]`，每日限 3 次，次日重置 |
| `我的角色` | 看等级/称号/战绩/装备状态/积分/背包，战力为隐藏值不显示 |
| `群排行榜` | 本群冒险者经验 Top 10，纯文字不 @ 不出图 |
| `我的背包` | 列出道具与数量 |
| `使用 [道具名]` | 用消耗品：`双倍经验卡`（下次经验 ×2）、`经验书`（立即加经验） |
| `冒险帮助` | 列出以上所有指令 |

- 角色对外的唯一数值是「等级」；战力/运势/今日增益均为隐藏值（暗中影响打怪），只在面板和播报中给模糊反馈
- 称号随等级分档（如 Lv1 新手冒险者 → Lv10 老练冒险者 → Lv20 传说冒险者…），与战绩搭配供攀比，不刷屏
- 组队把「送礼攒羁绊 → 拉人成功率」串成了社交闭环：羁绊越深越容易拉动，反过来促进送礼
- 数值 / 野怪 / 掉落 / 文案全部在 `data/content/rpg_config.json`（热重载），缺省值内置于代码
- 睡眠时段 0–6 点拦截写操作（打怪/组队/强化），查询类照常；超管不受限

### 记忆系统

| 指令 | 说明 |
|------|------|
| `查看记忆` | 查看当前生效的临时设定 |
| `植入记忆 [时长] [内容]` | 注入临时设定（最长 2 小时） |
| `清除记忆` | 清空所有临时设定 |
| `查看长期记忆` | 查看 AI 自动记住的长期事实 |
| `遗忘 [序号/全部]` | 删除长期记忆 |

AI 也会在对话中通过 `[[记下: ...]]` 标记自动提取长期记忆。

### 图库系统

| 指令 | 说明 |
|------|------|
| `看你的 / 发张 / 来张 [分类]` | 随机发一张本地图库图片 |
| `图库清单 / 查看图库 [分类]` | HTML 缩略图浏览相册 |
| `存 / 收下 / 投喂 / 增加 [分类]` | 手动存图 |
| `开始进货 / 停止进货 [分类]` | 批量自动存图模式 |

分类：冬弥(toya) / 彰人(self) / 美食(food) / 群友(groupmate) / 合照(vbs) / 表情(meme)

### 新人审核系统

三套名单管理（待审核 / 羁绊 / 特殊挂起），支持智能转移和自定义理由，所有指令限管理群使用。

### 定时任务

- 每天早上 6:00 早安问候
- 每天晚上 23:50 晚安问候
- 每小时清理过期临时记忆

### WL2 世界线模式

| 指令 | 说明 |
|------|------|
| `开启WL2模式` | 切换至 WL2 剧情线（**超管**） |
| `关闭WL2模式` | 返回正常世界线（**超管**） |

### 其他

| 触发 / 指令 | 说明 |
|-----------|------|
| 戳一戳 | 按时段返回反应词 |
| `重置对话` | 清空对话历史和数据库上下文（**超管**） |
| `重载配置 [persona\|assets\|全部]` | 热更新配置文件，无需重启；`assets` / `全部` 含派生池、关键词池、送礼配置等功能数据（**超管**） |

---

## 项目结构

```
akito_bot/
├── bot.py                          # 启动入口
├── .env.example                    # 配置模板
├── pyproject.toml                  # 项目配置 + 依赖声明
├── README.md                       # 本文件（用户向）
├── PLUGIN_MAINTENANCE.md           # 维护手册（开发 / 维护向）
├── docs/PROJECT_SPEC.md            # 项目规范（编码 / 提交 / 安全）
├── tools/                          # 维护工具脚本
│   ├── classify_scripts.py         # 剧本分类打标（home/story/noise）
│   ├── enrich_scripts.py           # LLM 富集（生成 cn_key + category + topics，断点续跑）
│   ├── build_embeddings.py         # 语义向量库构建（scripts/pjsk/all）
│   ├── eval_retrieval.py           # 检索精度评测（cosine 基线 vs bge-reranker 精排）
│   └── eval_set.json               # 评测黄金考题集（纯文本可直接编辑）
├── tests/                          # 关键路径测试（pytest）
├── nonebot_plugin_akito/
│   ├── __init__.py                 # 插件入口
│   ├── core/                       # 基础层（无副作用，可被任意模块导入）
│   │   ├── __init__.py             # 常量定义 & 统一导出
│   │   ├── api.py                  # DeepSeek / 智谱 / Tavily API 封装
│   │   ├── context.py              # Prompt 组装（人设 / 剧本 / 歌曲 / 关系）
│   │   ├── data.py                 # JSON 数据文件加载 & 热重载
│   │   ├── life_state.py           # 状态机（routine / 睡眠 / 节日）
│   │   ├── memory.py               # 长期记忆 & SQLite 群聊上下文
│   │   ├── retrieval.py            # 语义检索引擎（BGE-M3 + 均值中心化）
│   │   ├── time_awareness.py       # 时间流逝感知
│   │   ├── game_store.py           # 共享玩家存储层（gift/rpg 共用）
│   │   └── paths.py                # 数据路径定位
│   ├── handlers/                   # 主处理层
│   │   ├── chat.py                 # 主对话引擎（ReAct Agent）
│   │   ├── commands.py             # 记忆管理指令
│   │   └── reactions.py            # 戳一戳 / 自我监控
│   └── features/                   # 独立功能模块
│       ├── impression.py           # 群印象 & 随机插嘴
│       ├── gallery.py              # 相册图库
│       ├── random_paro.py          # 派生抽取器
│       ├── random_keyword.py       # 今日关键词
│       ├── verify.py               # 新人审核管理
│       ├── scheduled.py            # 定时任务
│       ├── event_mode.py           # WL2 世界线开关
│       ├── director.py             # 导演骰子（可安全删除，删除后主对话自动降级）
│       ├── gift.py                 # 送礼系统（积分/送礼/偷分/羁绊/签到闸门）
│       └── rpg/                    # RPG 子包：签到/打怪/组队/强化/背包/排行榜
│           ├── __init__.py
│           ├── config.py           # 全部数值/文案/配置（可被 rpg_config.json 热更新）
│           ├── player.py           # 经验→等级/称号/装备/战力
│           ├── fortune.py          # 隐藏运势 + 签到钩子
│           ├── hunt.py             # 打怪指令 + 战斗结算
│           ├── team.py             # 组队 @某人 指令
│           ├── smith.py            # 强化指令（积分出口）
│           ├── inventory.py        # 背包 / 使用道具
│           └── character.py        # 角色面板 / 排行榜 / 冒险帮助
└── data/                           # 持久化数据 + 本地素材（不纳入 Git）
```

> 依赖方向：`features/` → `core/` ← `handlers/`，三层职责与每个文件的接口详见 `PLUGIN_MAINTENANCE.md`。

---

## 本地测试

这套测试是给“本地沙箱里先测核心逻辑”准备的，不会去碰云服务器上的实时聊天数据。

- 改代码后**不会自动跑测试**。只有你手动执行 `pytest`，测试才会开始。
- `pytest -q` 会跑**整套测试**。
- 测试按模块拆成独立文件，可以只跑某一块，不需要每次全量回归。
- 更实用的做法是：**AI 改了哪块，就先跑哪块对应的测试文件**；只有改到共享底层、跨多个模块，或者准备统一提交前，再跑一次全量。

常用命令：

```bash
ruff check .
pytest -q
pytest tests/test_chat_helpers.py -q
pytest tests/test_commands_helpers.py -q
pytest tests/test_reactions_helpers.py -q
pytest tests/test_impression_helpers.py tests/test_impression_rescue_regression.py -q
pytest tests/test_verify_helpers.py -q
pytest tests/test_gallery_helpers.py -q
pytest tests/test_random_paro_helpers.py -q
pytest tests/test_random_keyword_helpers.py -q
pytest tests/test_data.py -q
pytest tests/test_director.py -q
pytest tests/test_event_mode_helpers.py -q
pytest tests/test_scheduled_helpers.py -q
pytest tests/test_gift.py -q
pytest tests/test_rpg.py -q
```

常见对应关系：

- 改 `handlers/chat.py` → 先跑 `pytest tests/test_chat_helpers.py -q`
- 改 `handlers/commands.py` → 先跑 `pytest tests/test_commands_helpers.py -q`
- 改 `handlers/reactions.py` → 先跑 `pytest tests/test_reactions_helpers.py -q`
- 改 `features/impression.py` → 先跑 `pytest tests/test_impression_helpers.py tests/test_impression_rescue_regression.py -q`
- 改 `features/verify.py` → 先跑 `pytest tests/test_verify_helpers.py -q`
- 改 `features/gallery.py` → 先跑 `pytest tests/test_gallery_helpers.py -q`
- 改 `features/random_paro.py` → 先跑 `pytest tests/test_random_paro_helpers.py -q`
- 改 `features/random_keyword.py` → 先跑 `pytest tests/test_random_keyword_helpers.py -q`
- 改 `features/director.py` → 先跑 `pytest tests/test_director.py -q`
- 改 `features/event_mode.py` → 先跑 `pytest tests/test_event_mode_helpers.py -q`
- 改 `features/scheduled.py` → 先跑 `pytest tests/test_scheduled_helpers.py -q`
- 改 `features/gift.py` → 先跑 `pytest tests/test_gift.py -q`
- 改 `features/rpg/` → 先跑 `pytest tests/test_rpg.py -q`
- 改 `core/data.py` → 先跑 `pytest tests/test_data.py -q`
- 改 `core/` 里的共享底层，或一次改了多块联动逻辑 → 直接补跑 `pytest -q`

本地测试怎么绕开真实运行环境：

- `tests/conftest.py` 会把 `tests/fixtures/test_data/` 复制到临时目录。
- 然后通过环境变量 `AKITO_DATA_DIR` 把代码里的读写路径指向这个临时目录，不碰你真实的 `data/`。
- `AKITO_SKIP_PLUGIN_LOAD=1` 会跳过真实插件加载。
- NoneBot、OneBot、OpenAI、HTML 渲染、网络请求这些边界都换成了假对象，所以本地能测“真业务逻辑 + 假平台外壳”。

这意味着本地最适合测的是：

- 指令参数解析
- 名单/记忆/路径这类数据处理
- 不依赖真实 QQ 收发的核心判断逻辑

不适合直接在本地测的是：

- 云端 `/data` 里的实时聊天记录
- 真实 QQ 发消息行为
- 外部 API 的真实返回

---

## 数据文件

可编辑的内容文件按用途归入子目录，绝大多数支持热重载（群内发送 `重载配置` 即可生效，无需重启）：

**`data/persona/`（人设与 Prompt）**

| 文件 | 说明 |
|------|------|
| `akito_persona.txt` / `wl2_persona.txt` | 主人设 / WL2 世界线人设 |
| `prompts_system.json` | Prompt 模板·系统机制（输出格式 / 记忆机制） |
| `prompts_character.json` | Prompt 模板·角色演绎（语气 / 模式 / 冬弥相关） |

**`data/content/`（语料 / 行为 / 世界观）**

| 文件 | 说明 |
|------|------|
| `akito_routine.json` / `wl2_routine.json` | 各时段日常状态（`status` + `poke`） |
| `akito_sleep.json` | 睡眠文案（梦话 / 抱怨 / 各场景睡眠反应） |
| `akito_reactions.json` | 被动反应（旧 flat 布局兼容保留；戳一戳兜底已移至 routine.json） |
| `gallery_text.json` | 图库文案（存图回复 / 发图语气） |
| `greetings.json` | 早晚安问候 |
| `akito_scripts.json` | 台词剧本库（含 `type`/`category`/`topics`/`cn_key`/`context`/`dialogue`，检索键为 `cn_key`） |
| `scripts_embeddings.npz` | 剧本语义向量库（`tools/build_embeddings.py` 生成，embed key=cn_key） |
| `akito_songs.json` | 歌曲知识库（含 `keywords`，用于歌曲圈内昵称 / 别名匹配） |
| `akito_relationships.json` | 人物关系档案（含 `keywords` 白名单） |
| `akito_director.json` | 导演骰子资产 |
| `pjsk_knowledge.json` | PJSK 世界观 / 黑话库 |
| `gift_config.json` | 送礼系统配置（礼物档位 / 事件权重 / 签到积分 / 播报文案，可热重载；仓库附带一份可直接编辑的模板，缺省值同时内置于代码） |
| `rpg_config.json` | RPG 全量配置（战斗/运势/强化/掉落/精英/野怪/道具/文案/错误信息，热重载；仓库附带模板，缺省值内置于代码） |

**`data/` 根目录（功能 / 运行时，多为自动读写）**：`paro_pools.json`、`fanfic_keywords.json`、`keyword_draws.json`、`gift_data.json`、`akito_memories.json`、`verify_*.json`、`last_interactions.json`、`impression_history.db`

> `core/data.py` 自动搜索 `persona/`、`content/` 子目录（兼容旧扁平布局）；`PROMPTS_DB` / `REACTIONS_DB` 由各自的拆分文件合并加载。

> ⚠️ 编辑 JSON 时字符串值里的双引号需转义为 `\"`，或改用中文书名号 `「」`，否则加载失败会静默回落到内置默认值。

---

## 群组白名单配置

编辑 `.env`（群号逗号分隔，`GROUP_IMAGE_PERMISSIONS` 为 JSON）：

```ini
ALLOWED_CHAT_GROUPS=群号1,群号2
ALLOWED_CP_GROUPS=群号1,群号2
ALLOWED_MEMORY_GROUPS=群号1
TARGET_GROUPS=群号1,群号2
GROUP_IMAGE_PERMISSIONS={"群号1":["all"],"群号2":["toya","self"]}
```

修改后重启生效。

---

## 文档

| 文档 | 面向 | 内容 |
|------|------|------|
| `README.md` | 用户 | 项目介绍、功能、部署（本文件） |
| `PLUGIN_MAINTENANCE.md` | 维护者 | 模块地图、每文件接口、数据清单、维护操作、AI 风险点 |
| `docs/PROJECT_SPEC.md` | 开发者 | 编码规范、命名、类型注解 / docstring、版本号与 Commit、安全规则 |

---

## 依赖

```
nonebot2[fastapi]         >= 2.4.4
nonebot-adapter-onebot    >= 2.4.6
openai                    >= 1.0.0
python-dotenv             >= 1.0.0
Pillow                    >= 10.0.0
aiohttp                   >= 3.9.0
numpy                     >= 1.21.0
nonebot-plugin-htmlrender >= 0.3.0
nonebot-plugin-alconna    >= 0.50.0
nonebot-plugin-apscheduler>= 0.4.0
nonebot-plugin-uninfo     >= 0.7.0
```

> **语义检索（可选）**：配置 `SILICONFLOW_API_KEY` + `pip install numpy`：
> ```bash
> py tools/classify_scripts.py --write --yes     # 首次：剧本打 type
> py tools/enrich_scripts.py --write             # LLM 富集（cn_key + category + topics）
> py tools/build_embeddings.py all               # 构建 .npz 向量库
> py tools/eval_retrieval.py compare             # 检索精度评测（基线 vs 精排）
> ```
> 未配置 key 时自动降级为原有随机/全量注入行为，不影响正常对话。

---

## License

MIT

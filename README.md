# AkitoBot — 东云彰人 AI Roleplay Bot

基于 [NoneBot2](https://nonebot.dev) + OneBot V11 的「初音未来：缤纷舞台」同人角色扮演 QQ 机器人，以「东云彰人」身份在群内进行 AI 驱动的沉浸式互动。

- **CP 立场**：彰冬（不拆不逆）
- **AI 后端**：DeepSeek API（对话）/ 智谱 GLM-4V（图片识别）/ Tavily（联网搜索）
- **当前版本**：0.2.1

---

## 快速开始

### 1. 环境要求

- Python ≥ 3.9
- 一个 NoneBot2 兼容的 OneBot V11 实现（[NapCat](https://github.com/NapNeko/NapCatQQ)、[LLOneBot](https://github.com/LLOneBot/LLOneBot) 等），并配置好正向 WebSocket

### 2. 安装依赖

本项目用 `pyproject.toml` 声明依赖（无 `requirements.txt`），直接 pip 安装：

```bash
pip install "nonebot2[fastapi]" nonebot-adapter-onebot openai python-dotenv Pillow aiohttp httpx \
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
- ReAct Agent 循环：LLM 自主决定是否发起联网搜索
- 语义检索（RAG）：BGE-M3 自动匹配相关剧本示例 + PJSK 黑话，替代全量随机注入；未配置则自动降级回静态行为
- 时间流逝感知：跨时段对话自动切换场景，不会续接久远话题
- 睡眠系统：凌晨 0–6 点自动进入休眠状态，搜索类请求会「被迫营业」
- 节假日感知：自动感知日本节日气氛
- 晨跑状态：早上 6 点自动进入晨跑角色状态
- 冬弥雷达：提及冬弥时开启护短 / CP 模式

### 图片识别

发送图片时自动调用智谱 GLM-4V 进行二次元特化识别，支持：
- PJSK 角色鉴定（彰人 / 冬弥 / KAITO / 天马司 精准防伪）
- 周边谷子识别（吧唧 / 立牌 / 橡胶挂件）
- 游戏截图识别
- OCR 文字提取

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
| `今日关键词` | 随机抽取 1–3 个关键词，每日限 1 次 |
| `/查看关键词` | 查看全部关键词池（图片输出） |
| `/添加关键词 [名称]` | 添加新关键词（**超管**） |
| `/删除关键词 [名称]` | 按名称删除，支持模糊匹配（**超管**） |

- 每日 0:00 自动刷新抽取次数
- 关键词涵盖科学隐喻、病症设定、自然意象、画面场景、文学化用、关系张力六大类
- PIL 卡片式渲染输出

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
| `冬弥呢` / `搭档呢` | 冬弥位置推断 |
| `重置对话` | 清空对话历史和数据库上下文（**超管**） |
| `重载配置 [persona\|assets\|全部]` | 热更新配置文件，无需重启（**超管**） |

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
│   └── build_embeddings.py         # 语义向量库构建（scripts/pjsk/all）
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
│   │   └── time_awareness.py       # 时间流逝感知
│   ├── handlers/                   # 主处理层
│   │   ├── chat.py                 # 主对话引擎（ReAct Agent）
│   │   ├── commands.py             # 记忆管理指令
│   │   └── reactions.py            # 戳一戳 / 冬弥雷达 / 自我监控
│   └── features/                   # 独立功能模块
│       ├── impression.py           # 群印象 & 随机插嘴
│       ├── gallery.py              # 相册图库
│       ├── random_paro.py          # 派生抽取器
│       ├── random_keyword.py       # 今日关键词
│       ├── verify.py               # 新人审核管理
│       ├── scheduled.py            # 定时任务
│       ├── event_mode.py           # WL2 世界线开关
│       └── director.py             # 导演骰子（可安全删除，删除后主对话自动降级）
└── data/                           # 持久化数据 + 本地素材（不纳入 Git）
```

> 依赖方向：`features/` → `core/` ← `handlers/`，三层职责与每个文件的接口详见 `PLUGIN_MAINTENANCE.md`。

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
| `akito_reactions.json` | 被动反应（行为种子 / 戳一戳兜底） |
| `gallery_text.json` | 图库文案（存图回复 / 发图语气） |
| `greetings.json` | 早晚安问候 |
| `akito_scripts.json` | 台词剧本示例（含 `type` 字段，`home` 类参与语义检索） |
| `scripts_embeddings.npz` | 剧本语义向量库（`tools/build_embeddings.py` 生成） |
| `akito_songs.json` | 歌曲知识库 |
| `akito_relationships.json` | 人物关系档案（含 `keywords` 白名单） |
| `akito_director.json` | 导演骰子资产 |
| `pjsk_knowledge.json` | PJSK 世界观 / 黑话库 |

**`data/` 根目录（功能 / 运行时，多为自动读写）**：`paro_pools.json`、`fanfic_keywords.json`、`keyword_draws.json`、`akito_memories.json`、`verify_*.json`、`last_interactions.json`、`impression_history.db`

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
httpx                     >= 0.27.0
numpy                     >= 1.21.0
nonebot-plugin-htmlrender >= 0.3.0
nonebot-plugin-alconna    >= 0.50.0
nonebot-plugin-apscheduler>= 0.4.0
nonebot-plugin-uninfo     >= 0.7.0
```

> **语义检索（可选）**：配置 `SILICONFLOW_API_KEY` + `pip install numpy`，然后运行 `py tools/build_embeddings.py all` 生成向量库。
> 未配置时自动降级为原有随机/全量注入行为，不影响正常对话。

---

## License

MIT

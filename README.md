# AkitoBot — 东云彰人 AI Roleplay Bot

基于 [NoneBot2](https://nonebot.dev) + OneBot V11 的「初音未来：缤纷舞台」同人角色扮演 QQ 机器人，以「东云彰人」身份在群内进行 AI 驱动的沉浸式互动。

**CP 立场**：彰冬（不拆不逆）。

**AI 后端**：DeepSeek API / 智谱 GLM-4V（图片识别）/ Tavily（联网搜索）

---

## 快速开始

### 1. 环境要求

- Python ≥ 3.9
- NoneBot2 兼容的 OneBot 实现（[NapCat](https://github.com/NapNeko/NapCatQQ)、[LLOneBot](https://github.com/LLOneBot/LLOneBot) 等）

### 2. 安装

```bash
pip install -r requirements.txt
# 或
pip install nonebot2[fastapi] nonebot-adapter-onebot openai Pillow aiohttp httpx nonebot-plugin-htmlrender nonebot-plugin-alconna nonebot-plugin-apscheduler nonebot-plugin-uninfo
```

### 3. 配置

复制 `.env.example` 为 `.env` 并填入你的 API Key：

```ini
DEEPSEEK_API_KEY=sk-your-deepseek-key    # DeepSeek API
TAVILY_API_KEY=tvly-your-tavily-key      # Tavily 搜索
ZHIPU_API_KEY=your-zhipu-key             # 智谱 GLM-4V 视觉
SUPERUSER_QQ=你的QQ号                     # 超级管理员
TOYA_QQ_ID=冬弥的QQ号                     # 触发 CP 模式
```

### 4. 运行

```bash
nb run
```

---

## 功能概览

### 核心对话

| 触发方式 | 说明 |
|----------|------|
| `小彰 [文本]` / `东云小彰 [文本]` | 以彰人身份进行角色扮演对话 |

**对话引擎特性**：
- ReAct Agent 循环：LLM 自主决定是否发起联网搜索
- 时间流逝感知：跨时段对话自动切换场景，不会续接久远话题
- 睡眠系统：凌晨 0-6 点自动进入休眠状态，搜索类请求会「被迫营业」
- 节假日感知：自动感知日本节日气氛
- 晨跑状态：早上 6 点自动进入晨跑角色状态
- 冬弥雷达：提及冬弥时开启护短/CP 模式

### 图片识别

发送图片时自动调用智谱 GLM-4V 进行二次元特化识别，支持：
- PJSK 角色鉴定（彰人/冬弥/KAITO/天马司 精准防伪）
- 周边谷子识别（吧唧/立牌/橡胶挂件）
- 游戏截图识别
- OCR 文字提取

### 印象系统（`群印象`）

- 读取目标用户最近 50 条发言，AI 生成盐系侧写
- 支持 @ 查看他人印象
- 3% 概率随机插嘴

### 派生抽取器（`抽派生`）

服务于 CP 同人创作：从彰人/冬弥双池各随机抽取一个平行宇宙身份，拼合为配对灵感。

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
- 模糊匹配：输入支持大小写不敏感、前缀/包含匹配（如 `wl` → `WL2彰`，`黑百` → `黑百合`）；多个匹配时列出候选
- 头像拼合：将 `data/images/paro_avatars/彰人/` 和 `data/images/paro_avatars/冬弥/` 下的对应图片自动拼合输出

### 今日关键词（`今日关键词`）

服务于同人写作灵感：从关键词池中随机抽取 1-3 个意象/情境/关系张力短语，作为同人文创作的核心 motif。

| 指令 | 说明 |
|------|------|
| `今日关键词` | 随机抽取 1-3 个关键词，每日限 1 次 |
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
| `看你的/发张/来张 [分类]` | 随机发一张本地图库图片 |
| `图库清单/查看图库 [分类]` | HTML 缩略图浏览相册 |
| `存/收下/投喂/增加 [分类]` | 手动存图 |
| `开始进货/停止进货 [分类]` | 批量自动存图模式 |

分类：冬弥(toya) / 彰人(self) / 美食(food) / 群友(groupmate) / 合照(vbs) / 表情(meme)

### PJSK 榜线预测

| 指令 | 说明 |
|------|------|
| `sn预测` / `cn预测` | 查询当前活动榜线 + Moesekai 预测，PIL 渲染图片 |

### 新人审核系统

三套名单管理（待审核 / 羁绊 / 特殊挂起），支持智能转移和自定义理由。

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

| 触发/指令 | 说明 |
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
├── pyproject.toml                  # NoneBot2 项目配置
├── nonebot_plugin_akito/
│   ├── __init__.py                 # 插件入口
│   ├── core/                       # 基础层
│   │   ├── __init__.py             # 常量定义 & 统一导出
│   │   ├── api.py                  # DeepSeek / 智谱 / Tavily API 封装
│   │   ├── context.py              # Prompt 组装（人设/剧本/歌曲/关系）
│   │   ├── data.py                 # JSON 数据文件加载 & 热更新
│   │   ├── life_state.py           # 状态机（routine/睡眠/节日）
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
│       ├── snowy.py                # PJSK 榜线预测
│       ├── verify.py               # 新人审核管理
│       ├── scheduled.py            # 定时任务
│       ├── event_mode.py           # WL2 世界线开关
│       └── director.py             # R18 导演骰子（可删除，删除后主对话自动降级）
└── data/                           # 持久化数据（不上传 Git）
```

---

## 数据文件

所有可编辑的配置文件位于 `data/` 目录，支持热重载（发送 `重载配置` 生效，无需重启）：

| 文件 | 说明 |
|------|------|
| `akito_persona.txt` | 主人设 Prompt |
| `akito_routine.json` | 各时段日常状态 |
| `akito_reactions.json` | 反应词库（抱怨/问候/戳一戳/睡眠） |
| `akito_prompts.json` | Prompt 模板库 |
| `akito_scripts.json` | 台词剧本示例 |
| `akito_songs.json` | 歌曲知识库 |
| `akito_relationships.json` | 人物关系档案 |
| `pjsk_knowledge.json` | PJSK 世界观/黑话库 |
| `wl2_routine.json` | WL2 世界线状态 |
| `akito_memories.json` | 运行时记忆库（自动读写） |

---

## 群组白名单配置

编辑 `.env`（逗号分隔，`GROUP_IMAGE_PERMISSIONS` 为 JSON）：

```ini
ALLOWED_CHAT_GROUPS=群号1,群号2
ALLOWED_CP_GROUPS=群号1,群号2
ALLOWED_MEMORY_GROUPS=群号1
TARGET_GROUPS=群号1,群号2
GROUP_IMAGE_PERMISSIONS={"群号1":["all"],"群号2":["toya","self"]}
```
重启生效。
```

---

## 依赖

```
nonebot2[fastapi] >= 2.4.4
nonebot-adapter-onebot >= 2.4.6
openai >= 1.0.0
python-dotenv >= 1.0.0
Pillow >= 10.0.0
aiohttp >= 3.9.0
httpx >= 0.27.0
nonebot-plugin-htmlrender >= 0.3.0
nonebot-plugin-alconna >= 0.50.0
nonebot-plugin-apscheduler >= 0.4.0
nonebot-plugin-uninfo >= 0.7.0
```

---

## License

MIT

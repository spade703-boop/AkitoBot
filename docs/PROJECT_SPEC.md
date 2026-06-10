# 东云小彰 (AkitoBot) 项目规范

## 1. 项目概述

### 1.1 项目定位

「东云小彰」是一个基于 NoneBot2 的角色扮演 QQ 机器人，扮演《初音未来：缤纷舞台》（Project SEKAI）中的 **东云彰人**（Shinonome Akito）。

**CP 立场：彰冬（不拆不逆）。** 所有设计决策应尊重此立场。

### 1.2 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | NoneBot2 (>=2.4.4) + OneBot V11 适配器 |
| AI 对话 | DeepSeek API (`deepseek-v4-flash`) |
| 图像识别 | 智谱 GLM-4V |
| 网络搜索 | Tavily API |
| 语义检索 | SiliconFlow BGE-M3 embedding + numpy（可选，未配置自动降级） |
| 图像渲染 | Pillow + nonebot-plugin-htmlrender |
| 持久化 | JSON 文件 + SQLite |
| 定时任务 | APScheduler |
| 运行环境 | Python 3.9+（生产 Docker 容器实际为 3.10+） |

### 1.3 架构

```
nonebot_plugin_akito/
├── core/         基础层：API 封装、数据加载、状态机、记忆、时间感知、语义检索
├── handlers/     消息处理层：主对话引擎、管理指令、被动反应
└── features/     独立功能模块：8 个（director 可安全删除，scheduled 为定时基础设施）
```

依赖方向：`features/` → `core/` ← `handlers/`，三层默认单向依赖。

`core/__init__.py` 是统一导出入口，模块应**优先**通过 `from ..core import ...` 获取核心能力。

**已知的合理例外**（不视为违规，刻意保留）：

- `handlers/chat.py` 通过 `try/except` **惰性导入**可选功能 `features/director.py`，以保证该模块可被一键删除；
- `core/data.py` 的 `reload_assets()` 惰性导入各 feature 的热重载钩子（注册式刷新，非启动期依赖）；
- `core/retrieval.py` 与 `core/data.py` 互为惰性导入（函数内 import，规避循环依赖）；core 子模块之间允许直接引用兄弟模块的内部工具（如 `_DATA_SEARCH_DIRS`），不必经过包入口。
  （历史例外已收敛：features 层现统一走公共 `find_data_path` / `get_data_dir`，不再直引 core 内部工具。）

---

## 2. 目录结构标准

```
gemini_bot/
├── bot.py                       # 启动入口
├── pyproject.toml                # 项目配置 + 依赖声明
├── .env.example                  # 环境变量模板（可提交）
├── .env                          # 实际密钥（不可提交）
├── .gitignore
├── README.md                     # 用户向项目介绍
├── PLUGIN_MAINTENANCE.md         # 开发向维护手册
│
├── .claude/                      # AI 助手配置（不可提交）
│   └── CLAUDE.md
│
├── docs/                         # 项目文档
│   ├── PROJECT_SPEC.md           # 本规范文件
│   └── FEATURE_LOGIC.md          # 功能逻辑全解（维护者本地参考，已 gitignore 不入库）
│
├── data/                         # 运行时数据（不可提交）
│   ├── *.json                    # 人设/提示词/记忆/功能数据
│   ├── *.txt                     # 人设文本
│   └── images/                   # 图库素材
│
├── tests/                        # 测试代码
│   ├── conftest.py
│   └── test_*.py
│
├── tools/                        # 维护工具脚本（剧本分类 / LLM 富集 / 向量库构建）
│
└── nonebot_plugin_akito/         # 主插件包
    ├── __init__.py               # 插件入口：元数据 + require() + 导入三大子包
    ├── core/                     # 基础层
    │   ├── __init__.py           # 常量 + 统一导出
    │   ├── api.py                # API 封装
    │   ├── context.py            # Prompt 组装
    │   ├── data.py               # 数据加载 + 热重载
    │   ├── life_state.py         # 状态机
    │   ├── memory.py             # 记忆系统
    │   ├── retrieval.py          # 语义检索引擎
    │   └── time_awareness.py     # 时间感知
    ├── handlers/                 # 消息处理层
    │   ├── chat.py               # 主对话引擎
    │   ├── commands.py           # 管理指令
    │   └── reactions.py          # 被动反应
    └── features/                 # 独立功能模块
        ├── director.py           # 导演骰子（可安全删除）
        ├── event_mode.py         # WL2 世界线
        ├── gallery.py            # 图库
        ├── impression.py         # 群印象 + 随机插嘴
        ├── random_keyword.py     # 今日关键词
        ├── random_paro.py        # 抽派生
        ├── scheduled.py          # 定时任务
        ├── verify.py             # 加群审核
        └── msyhbd.ttc            # 渲染字体（random_paro / random_keyword 共用）
```

---

## 3. 命名规范

| 元素 | 规范 | 示例 |
|------|------|------|
| 模块/文件 | `snake_case` | `life_state.py`, `random_paro.py` |
| 函数 | `snake_case` | `get_daily_activity()`, `build_time_gap_prompt()` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_HISTORY_LEN`, `TZ_CN`, `STATE_DURATION` |
| 私有函数/变量 | `_leading_underscore` | `_normalize_period()`, `_find_data_path()` |
| 模块级变量 | `UPPER_SNAKE_CASE` | `MEMORY_DB`, `AKITO_STATUS` |
| 类 | `PascalCase` | 当前项目无类定义，预留规范 |

> 例外：函数内部「常量性」局部变量（仅在该函数内使用、值固定）允许用 `UPPER_SNAKE_CASE`，如 `ITEMS_PER_PAGE`、`FR_TEXTS`；ruff 已忽略 `N806`。

---

## 4. 导入顺序规范

每个文件的导入按以下顺序分为三组，组间用空行分隔，同一组内按字母顺序排列：

```python
# 第 1 组：标准库
import json
import re
from pathlib import Path

# 第 2 组：第三方库
from nonebot import on_command
from nonebot.log import logger
from PIL import Image

# 第 3 组：本地导入（相对路径）
from ..core import (
    TZ_CN, DB_PATH,
    MEMORY_DB, get_user_memory,
    save_memory,
)
```

- **优先**使用 `from ..core import` 导入核心能力，不直接引用 core 的子模块（少数共享内部工具的例外见 §1.3）
- 如需 `from __future__ import annotations`，放在文件最顶部（docstring 之后）

---

## 5. 代码风格

| 规则 | 值 |
|------|-----|
| 字符串引号 | 双引号 `"` |
| 行宽上限 | 120 字符 |
| 缩进 | 4 空格（禁止 Tab） |
| 文件编码 | UTF-8 |
| 行尾 | LF（Unix 风格） |
| 文件末尾 | 保留一个空行 |
| 运算符两侧 | 保留空格 |
| 逗号后 | 保留空格 |

> 既有代码大量使用「一行写完简单 `if` / 赋值」的紧凑风格（如 `if not x: return`），予以保留；ruff 已忽略 `E701` / `E702`。

---

## 6. 类型注解

### 6.1 要求

- **新增 / 重构的公共函数**：必须标注参数类型和返回值类型
- **存量公共函数**：逐步补齐，不作为阻塞项；`core/` 层作为参考实现已基本完成
- **内部辅助函数**：类型注解可选，但推荐在逻辑复杂的函数上使用
- 使用 `from __future__ import annotations` 以支持前向引用与新式联合类型

### 6.2 Python 版本兼容

项目 `pyproject.toml` 声明 `requires-python = ">=3.9"`。`Path | None` 等新式联合类型是 Python 3.10+ 语法，在 3.9 中运行时不可用。

**本项目采用方案 A**：所有用到新式联合类型注解的模块均需在文件顶部添加 `from __future__ import annotations`，使注解延迟求值，因此 **3.9+ 均可正常运行**；生产 Docker 容器实际为 3.10+。

新增模块若使用 `X | None` 等写法，同样需在文件顶部添加 `from __future__ import annotations`。

### 6.3 示例

```python
from __future__ import annotations
from typing import Any, Optional
from pathlib import Path

def load_json_file(filename: str, default_data: Any = None) -> Any:
    """加载 JSON 数据文件。"""
    ...

def _find_data_path(filename: str) -> Optional[Path]:
    """在多个搜索目录中定位数据文件。"""
    ...
```

---

## 7. 文档字符串规范

统一使用 **Google 风格**的 docstring。

### 7.1 模块级

**应包含**，描述模块职责和对外接口（`core/` 层已完成，features/handlers 存量逐步补齐）：

```python
"""
时间流逝感知模块
---------------
追踪每个群的"最后一次 bot 回复时间 + routine 快照"，
在下次回复时按 gap 大小和时段变化数量注入时间感知文本。

外部接口：
  record_bot_response(group_id)      — 发完回复后调用
  build_time_gap_prompt(group_id)    — 构建 system prompt 注入文本
"""
```

### 7.2 公共函数

新增 / 重构的公共函数**必须**有简短描述（一行即可），存量逐步补齐。
参数或返回值不直观时，用 Google 风格的 `Args:` / `Returns:` 块补充说明：

```python
def build_time_gap_prompt(group_id: int | str) -> str:
    """构建时间流逝感知注入文本。

    Args:
        group_id: 群 ID，用于查找该群的上次交互记录。

    Returns:
        注入到 system prompt 的时间感知文本。gap < 30 分钟时返回空字符串。
    """
```

简单的取值 / 设值函数只需一行摘要，无需强制 `Args:` / `Returns:`：

```python
def get_safe_until() -> float:
    """返回安全期截止时间戳。"""
    ...
```

### 7.3 内部辅助函数

可选，复杂逻辑必须加注释说明：

```python
def _normalize_period(key: str) -> str:
    """将带 _weekday/_weekend 后缀的 key 归一化为基础时段名。"""
```

---

## 8. 错误处理规范

### 8.1 基本原则

- 始终使用 `try/except` 捕获异常，**严禁**裸 `except`（至少写 `except Exception`）
- 采用 graceful degradation 模式：失败时回落到安全的默认值
- **关键错误**不得静默吞掉，至少记录 warning/error 级别日志
- **尽力而为的可选操作**（如可选数据解析、热重载钩子、可选模块导入）允许静默抑制（`try/except: pass`），建议补一行 `logger.debug`；ruff 已忽略 `SIM105`
- 每个 API 调用必须包裹 try/except + 超时处理

### 8.2 标准模式

```python
try:
    result = some_operation()
except SpecificError as e:
    logger.error(f"❌ 操作失败: {e}")
    return fallback_value
except Exception as e:
    logger.error(f"❌ 未知错误: {e}")
    return safe_default
```

### 8.3 日志 emoji 前缀

| 前缀 | 含义 |
|------|------|
| ✅ | 操作成功 |
| ❌ | 失败/错误 |
| ⚠️ | 警告/降级 |
| 🔧 | 修复/回退/降级处理 |
| 🤖 | AI/Agent 相关 |
| 🔍 | 网络搜索 |
| 📸 | 图片处理 |
| 🎭 | 角色推理/内心OS |
| 🧠 | 记忆操作 |
| ⏱️ | 时间感知 |
| 🔄 | 热重载 |
| 🧹 | 清理 |
| 💾 | 持久化/保存 |
| 📥 | 下载 |
| 👁️ | 视觉识别 |
| 📚 | 上下文/知识库 |
| 💣 | 重置/删除 |
| 🏃 | 状态/晨跑 |
| 😴 | 睡眠相关 |
| 🎬 | 导演模块 |
| 🎮 | 游戏/世界观 |

---

## 9. 日志规范

### 9.1 通用要求

- 始终使用 `from nonebot.log import logger`
- 日志消息使用中文，保持 emoji 前缀风格

### 9.2 级别含义

| 级别 | 使用场景 |
|------|----------|
| `logger.info` | 正常操作成功、状态变更、热重载完成、关键流程节点 |
| `logger.warning` | 降级行为、数据缺失使用默认值、恢复成功、非致命异常 |
| `logger.error` | 真实错误、API 调用失败、数据损坏 |
| `logger.debug` | 调试信息（仅开发时启用） |

### 9.3 示例

```python
logger.info(f"💾 长期记忆已加载！包含 {len(MEMORY_DB)} 个会话数据")
logger.warning(f"⚠️ 检测到复读！强制注入去重指令重新生成...")
logger.error(f"❌ API请求失败: {e}")
logger.debug(f"⏱️ [TimeAwareness] 群 {group_id} 时间戳已记录")
```

---

## 10. 文件 I/O 规范

### 10.1 数据文件写入

**默认使用原子写入模式**（先写 `.tmp` 文件再 `os.replace`），防止断电/崩溃导致文件损坏：

```python
import os
from pathlib import Path

target_path = Path("data/example.json")
target_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = target_path.with_suffix(".tmp")
with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp_path, target_path)
```

### 10.2 数据文件读取

- JSON 文件：通过 `core/data.py` 的 `load_json_file()` 统一加载
- 文本文件：使用 `path.read_text(encoding="utf-8")`

### 10.3 例外

- 临时文件、日志输出、一次性导出可不使用原子写入
- 读取操作无限制

---

## 11. 全局状态管理

两种可接受模式，**禁止混用**：

### 模式 A：可变容器原地更新

适用于 `dict` / `list` 类型全局变量。使用 `.clear()` + `.update()` / `.extend()`：

```python
# data.py — 热重载后原地更新，其他模块持有的引用自动生效
def reload_assets():
    REACTIONS_DB.clear()
    REACTIONS_DB.update(load_json_file("akito_reactions.json"))
```

### 模式 B：`global` 声明 + 重新赋值

适用于不可变类型（`float`、`int`、`str`）。**必须**在函数内使用 `global` 关键字：

```python
# life_state.py — 安全期时间戳是 float（不可变），必须用 global 重新绑定
def grant_safety_pass(seconds: int = 5):
    global AKITO_SAFE_UNTIL
    AKITO_SAFE_UNTIL = time.time() + seconds
```

### 规则

| 变量类型 | 使用模式 | 原因 |
|----------|----------|------|
| `dict` / `list` | 模式 A（`.clear()` + `.update()`） | 避免 import 重新绑定问题 |
| `float` / `int` / `str` | 模式 B（`global` + 赋值） | 不可变类型无法原地修改 |

---

## 12. 模块结构规范

每个 `.py` 文件内部按以下顺序组织：

1. 文件编码声明（如需要）：`# -*- coding: utf-8 -*-`
2. 模块级 docstring
3. `from __future__ import`（如需要）
4. 标准库导入
5. 第三方库导入
6. 本地导入
7. 模块级常量
8. 模块级全局变量
9. 私有辅助函数
10. 公共函数
11. 模块级初始化代码（如 `init_db()`）
12. 事件处理器注册（`on_command()` / `on_message()` 装饰器）

---

## 13. 版本号与 Commit 规范

### 13.1 版本号

采用 `主版本.次版本.修订号`（如 `0.2.1`）：

- **主版本**：架构大改、不兼容的变更
- **次版本**：新功能模块、新指令
- **修订号**：Bug 修复、Prompt 调优、小改动

### 13.2 Commit 格式

- **格式**：`type: 中文描述`（Commit message 一律中文填写，后续维护统一遵守）
- **类型**：
  - `feat` — 新功能
  - `fix` — Bug 修复
  - `docs` — 文档更新
  - `refactor` — 重构
  - `chore` — 杂项（依赖、配置等）
- **示例**：`feat: 新增今日关键词功能模块，六分类去重抽取，每日限1次`

---

## 14. 数据文件规范

### 14.1 格式要求

- 格式：JSON（UTF-8 编码）
- 位置：只读内容文件归入 `data/persona/`（人设 / prompt）与 `data/content/`（语料 / 行为 / 世界观）子目录；功能 / 运行时（写回）文件留在 `data/` 根目录。
- 加载：通过 `core/data.py` 的 `load_json_file()` 统一加载；`_find_data_path()` 自动搜索 `persona/`、`content/` 子目录与根目录（向后兼容旧 flat 布局）。
- 热重载：通过 `reload_assets()` 实现。
- 拆分文件合并加载：`PROMPTS_DB`（= `prompts_system.json` + `prompts_character.json`）、`REACTIONS_DB`（= `akito_reactions.json` + `gallery_text.json` + `greetings.json`）在加载时合并回单一 DB，consumer 不感知拆分。

### 14.2 新增数据文件

在 `data.py` 的 `reload_assets()` 中注册对应的热重载逻辑：

```python
def reload_assets():
    global NEW_DATA
    NEW_DATA.clear()
    NEW_DATA.update(load_json_file("new_data.json"))
    NEW_DATA.update(fallback_default)  # 确保有回退默认值
```

### 14.3 JSON 编码注意事项

- JSON 字符串值中使用双引号时需转义为 `\"`，或优先使用中文书名号 `「」`
- 所有 JSON 文件必须有对应的硬编码 fallback 默认值（在 `data.py` 中定义）

---

## 15. 版本控制与安全规则

项目分两区管理：

| 区域 | 路径 | 规则 |
|------|------|------|
| **版本控制区** | `/data` 以外所有文件 | 纳入 Git，随版本迭代更新 |
| **本地调试区** | `/data` 全部内容 | **不纳入版本控制**，仅本地保留为调试样本；迁移到新环境需手动拷贝 |

### 15.1 绝不提交到 Git

- `.env` — 含 DeepSeek / Tavily / 智谱 API Key
- `.claude/settings.local.json` — 含本地 Auth Token
- `data/` 目录的全部内容 — 运行时数据、本地素材

### 15.2 可以提交

- `.env.example` — 仅保留字段名和占位符值
- `data/` 目录**不存在**于仓库中（`.gitignore` 已配置 `/data`）

### 15.3 代码中禁止

- 硬编码 API Key、Token、QQ 号 → 统一走 `.env` 读取
- 在日志中打印完整的 API Key 或敏感用户数据

### 15.4 推送前检查清单

1. 确认 `.env` 未被追踪（`git status` 中不出现）
2. 确认 `/data` 下无新增被追踪文件
3. 确认 `.env.example` 已同步最新的可配置项
4. `ruff check nonebot_plugin_akito/` 通过（必需，应为 0 错误）；`mypy` 可选，未纳入日常推送流程
5. 测试执行遵循“**先测改动处，再看风险补全量**”：
   - 只改单个叶子模块时，至少跑对应测试文件；
   - 改到 `core/`、`tests/conftest.py`、多模块共享逻辑、或一次改了多个功能模块时，必须补跑全量；
   - 准备推送前，原则上应完成一次全量回归。
6. 关键路径测试通过：`pytest tests/ -v`

# 测试目录说明

这份文档只负责两件事：

1. 告诉后续 AI / 维护者去哪里找测试。
2. 告诉后续 AI / 维护者应该怎么跑测试。

它**不负责**版本发布、热重载、部署、Git 推送、生产环境运维；这些内容看根目录的 `PLUGIN_MAINTENANCE.md`。

## 快速开始

常用命令：

```bash
ruff check tests
pytest -q
pytest -q tests/core
pytest -q tests/handlers/test_chat.py
pytest -q tests/features/gift
pytest -q tests/features/rpg/test_boss.py
pytest -q tests/features/random_paro -k helper
```

推荐执行顺序：

1. 先按改动范围跑对应目录或单文件。
2. 如果改到 `nonebot_plugin_akito/core/`、`tests/conftest.py`、共享 helper、跨模块联动逻辑，再补跑 `pytest -q`。
3. 准备提交前，再跑一次 `pytest -q`。

## 目录映射

测试目录现在按源码结构镜像，不再把所有测试平铺在 `tests/test_*.py`。

```text
tests/
├── README.md
├── conftest.py
├── fixtures/test_data/
├── core/
├── handlers/
└── features/
    ├── director/
    ├── event_mode/
    ├── gallery/
    ├── gift/
    ├── impression/
    ├── random_keyword/
    ├── random_paro/
    ├── rpg/
    ├── scheduled/
    └── verify/
```

对应关系：

- `nonebot_plugin_akito/core/*.py` → `tests/core/`
- `nonebot_plugin_akito/handlers/*.py` → `tests/handlers/`
- `nonebot_plugin_akito/features/<功能包>/` → `tests/features/<功能包>/`

大功能内部继续按子功能拆：

- `tests/features/gift/`
  - `test_logic.py`
  - `test_signin.py`
  - `test_commands.py`
  - `test_steal.py`
  - `test_admin.py`
  - `helpers.py`
- `tests/features/rpg/`
  - `test_player.py`
  - `test_fortune.py`
  - `test_hunt.py`
  - `test_inventory.py`
  - `test_smith.py`
  - `test_team.py`
  - `test_character.py`
  - `test_boss.py`
  - `helpers.py`

规则很简单：**测试跟着功能走，不再回到“大一统测试文件”。**

## 先跑哪里

常见映射：

- 改 `nonebot_plugin_akito/handlers/chat.py` → `pytest -q tests/handlers/test_chat.py`
- 改 `nonebot_plugin_akito/handlers/commands.py` → `pytest -q tests/handlers/test_commands.py`
- 改 `nonebot_plugin_akito/handlers/reactions.py` → `pytest -q tests/handlers/test_reactions.py`
- 改 `nonebot_plugin_akito/core/retrieval.py` → `pytest -q tests/core/test_retrieval.py`
- 改 `nonebot_plugin_akito/core/api.py` → `pytest -q tests/core/test_api.py`
- 改 `nonebot_plugin_akito/features/gift/` → `pytest -q tests/features/gift`
- 改 `nonebot_plugin_akito/features/rpg/` → `pytest -q tests/features/rpg`
- 改 `nonebot_plugin_akito/features/random_paro/` → `pytest -q tests/features/random_paro`
- 改 `nonebot_plugin_akito/features/random_keyword/` → `pytest -q tests/features/random_keyword`
- 改 `nonebot_plugin_akito/features/impression/` → `pytest -q tests/features/impression`

如果一次改了多块，直接跑全量：

```bash
pytest -q
```

## 测试环境约束

这套测试默认是“假平台 + 真业务逻辑”：

- `tests/conftest.py` 会注入 fake `nonebot` / `onebot` / `openai` / `htmlrender` / `aiohttp`
- `tests/fixtures/test_data/` 会复制到临时目录
- `AKITO_DATA_DIR` 会被指向这个临时目录
- `AKITO_SKIP_PLUGIN_LOAD=1` 会跳过真实插件加载
- 默认不碰真实 `data/`、真实 QQ、真实外网 API

所以本地测试最适合验证：

- 纯函数
- 指令参数解析
- JSON / 路径 / 存储层行为
- 假事件驱动下的命令结算逻辑

默认不直接覆盖：

- 云端实时聊天数据
- 真实 QQ 收发链路
- 外部 API 在线返回

## 写新测试时的规则

- 文件编码统一用 UTF-8。
- 优先沿用 `tests/conftest.py` 的 fake 平台，不要在每个文件里重复造大环境。
- 只在同一功能包内放 `helpers.py`；不要把 `gift` 的 helper 拿去给 `rpg` 直接复用，反之亦然。
- 新增测试优先放到对应功能目录下，不要再新建根层 `tests/test_xxx.py`。
- `gift` / `rpg` 这类大功能，继续按子功能拆，不要重新合并成超大单文件。
- 如果测试依赖 `__file__` 推导资源路径，目录下沉后记得一起改相对层级。

已知特例：

- `tests/features/random_paro/test_helpers.py` 会读取仓库内的 `data/images/paro_avatars/` 真实素材路径；改动相关资源目录时要连这个测试一起看。

## 给后续 AI 的最低行动准则

1. 先看改动落在哪个源码目录。
2. 先跑对应测试子目录或单文件。
3. 改到共享底层或跨模块逻辑时补跑全量。
4. 不要参考旧的平铺路径写法，例如 `tests/test_rpg.py`、`tests/test_gift.py`；它们已经被拆包替代。

# RPG 目录重构执行清单

## 目标与约束

- [x] 按业务域整理 `features/rpg`，不改变命令、概率、数值、播报顺序和数据结构。
- [x] 删除旧的平铺实现入口，不保留 `rpg.hunt`、`rpg.rewards`、`rpg.boss` 等兼容 shim。
- [x] 保留 `config.py`、`types.py`、`state.py`、`utils.py` 作为根目录公共基础模块。
- [x] 保留工作区中与 RPG 无关的未提交修改，不混入本次变更。

## 阶段清单

### 1. 目录与模块迁移

- [x] 创建 `battle/`、`hunt/`、`team/`、`world_boss/`、`player/`、`inventory/`、`equipment/`、`supply/`、`profile/`、`reporting/`、`signin/`、`simulation/` 子包。
- [x] 移动普通战斗、玩家、背包、装备、补给、角色、报表、签到和模拟器模块。
- [x] 移动组队和世界 BOSS 模块。
- [x] 删除旧平铺文件并完成所有生产代码导入迁移。

### 2. 普通战斗拆分

- [x] 建立 `battle/rewards/calculations.py` 奖励计算入口。
- [x] 建立 `battle/rewards/encounters.py` 支援与小奇遇入口。
- [x] 建立 `battle/rewards/settlement.py` 战斗结算入口。
- [x] 建立 `hunt/broadcast.py` 播报入口。
- [x] 建立 `hunt/drop_test.py` 掉落测试命令入口。
- [x] 将实现内部依赖切换到拆分后的模块，确保 monkeypatch 和跨模块调用指向唯一实现。

### 3. 世界 BOSS 拆分

- [x] 建立 `world_boss/logic.py` 状态、生成和伤害处理入口。
- [x] 建立 `world_boss/settlement.py` 结算与跨日清理入口。
- [x] 建立 `world_boss/command.py` 命令入口。
- [x] 将世界 BOSS 命令实现与逻辑/结算实现真正分离，避免重复注册和反向依赖。
- [x] 更新定时任务使用新的结算路径。

### 4. 测试、类型与文档

- [x] 更新现有 RPG 测试的导入路径。
- [x] 按新目录镜像整理测试文件，并拆分 rewards/hunt 测试职责。
- [x] 更新 `pyproject.toml` 的 Mypy 文件列表和模块覆盖配置。
- [x] 更新 RPG README、维护文档和开发脚本中的路径说明。
- [x] 全局搜索确认无旧平铺路径引用（仅保留新路径与测试导入）。

### 5. 验证与收尾

- [x] 通过 RPG 测试集（212 passed）。
- [x] 通过全量测试（901 passed）。
- [x] 通过 Ruff；Mypy 仅剩工作区原有的 `core/game_store.py` 与 `features/gift/logic.py` 类型错误，RPG 报表新增错误已清理。
- [x] 运行成长模拟器并确认输出稳定。
- [x] 检查命令注册数量、导入顺序和工作区差异；保留非 RPG 未提交修改不纳入本次提交。

## 第二阶段：实现职责收紧

- [x] 将 `battle/rewards/calculations.py` 从 re-export facade 收紧为纯计算实现，并由结算模块调用。
- [x] 将 `battle/rewards/encounters.py` 承载援护/小奇遇奖励实现，结算模块只编排调用。
- [x] 将 `hunt/broadcast.py` 承载播报行生成，`hunt/command.py` 只保留指令与流程编排。
- [x] 复核 `world_boss/logic.py`、`settlement.py`、`command.py` 的实现边界；命令入口通过 `command.py` 导出，结算实现集中在 `settlement.py`，避免重复注册。
- [x] 为拆分后的公开内部函数保留稳定导入路径，确保 monkeypatch 与现有调用不失效。
- [x] 每完成职责块均通过局部回归；最终全量测试 901 passed、Ruff 通过、命令注册保持 20 个。

## 生产前收尾路线

### 必须处理

- [x] **世界 BOSS 真正拆分**：将命令 handler 移入 `world_boss/command.py`，状态/生成/伤害留在 `logic.py`，结算与跨日清理留在 `settlement.py`；禁止重复注册命令。
- [x] **移除播报动态兼容桥**：去掉 `hunt/broadcast.py` 对 `sys.modules` 和 `command` helper 的隐式依赖，改为稳定的配置/渲染接口。
- [x] **增加 RPG 启动 smoke test**：真实导入 RPG 包，检查命令注册数量、命令名唯一性和签到钩子单次注册。
- [x] **补生产数据兼容测试**：覆盖旧版 `gift_data.json` 读取/规范化/保存、非法配置热重载回滚、世界 BOSS 跨日清理幂等与防重复发奖。
- [x] **清理全局 Mypy 基线**：修复 `core/game_store.py` 与 `features/gift/logic.py` 的已知错误，`mypy` 配置范围内 21 个源文件全部通过。

### 建议处理

- [ ] **继续拆分测试职责**：将 `test_hunt.py`、`test_boss.py` 按计算、结算、播报、命令职责拆成更小的测试文件。
- [x] **复核大模块边界**：完成 `reporting/analytics.py` 的运营看板计算与命令入口拆分，新增 `reporting/command.py` 并保留旧导入兼容；`config.py` 与 `inventory/inventory.py` 暂无低风险边界可提取，列入后续 dataclass/TypedDict 收紧。
- [x] **增加并发与锁测试**：覆盖签到、打怪、组队同时写入时的锁行为和存档不覆盖。
- [x] **建立发布前检查脚本**：统一执行冷启动、配置加载、命令扫描、旧路径扫描、RPG/全量测试和 Ruff。

### 可以延后

- [ ] 将配置对象和跨模块结算对象逐步改为标准库 `dataclass`。
- [ ] 为 `_cfg`、`_copy`、`_line` 等只读配置查询增加可失效缓存，并在热重载时原子清理。
- [ ] 继续收紧动态 `dict`，扩大 `TypedDict` 或其他结构化类型覆盖范围。

### 推荐执行顺序

1. 世界 BOSS 拆分与启动 smoke test。
2. 播报兼容桥清理与对应测试迁移。
3. 生产数据兼容、热重载回滚和跨日幂等测试。
4. 并发锁测试、发布前检查脚本与全量验证。
5. 最后单独处理全局 Mypy 基线和延后项。

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

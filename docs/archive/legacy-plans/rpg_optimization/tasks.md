# RPG 功能调优 - 实现计划

## [/] Task 1: 提取公共工具函数
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 创建 `utils.py` 模块，提取 `hunt.py`、`boss.py`、`team.py` 中真正共享的函数
  - 包括：`_team_success_rate`、`_team_power_bonus`、`_fortune_combat_factor` 等
  - **注意**: 仅提取真正跨模块共享的逻辑，不跨业务线（单人打怪 vs 世界BOSS）提取
  - 更新所有调用处引用共享函数
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 所有原有测试通过，功能不变
  - `programmatic` TR-1.2: 代码行数减少（消除重复）
  - `human-judgment` TR-1.3: 代码结构更清晰，无重复实现

## [/] Task 2: 拆分 hunt.py 为战斗逻辑模块
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 创建 `combat.py`，提取战斗核心逻辑（`resolve_hunt`、`_pick_monster`、`_pick_encounter` 等）——两条线共用
  - 创建 `events.py`，提取事件处理逻辑（`_roll_hunt_event`、`_roll_coop_event`、`_roll_minor_encounter` 等）——仅单人/组队线
  - 创建 `rewards.py`，提取奖励计算逻辑（`_apply_rewards`、`_challenge_exp`、`_challenge_points` 等）——仅单人/组队线
  - **注意**: `boss.py` 保持独立，不与 hunt 混拆，保持两条线的独立性
  - 更新 `hunt.py` 调用新模块，保留指令入口和主流程
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-2.1: 所有原有测试通过
  - `programmatic` TR-2.2: 每个新文件 < 300 行
  - `human-judgment` TR-2.3: 模块职责单一，依赖关系清晰，`boss.py` 独立

## [/] Task 3: 添加类型安全增强
- **Priority**: medium
- **Depends On**: Task 1, Task 2
- **Description**:
  - 添加类型注解到所有函数参数和返回值
  - 使用 `dataclasses` 创建配置数据类（如 `EquipConfig`、`CombatConfig`、`FortuneConfig`）
  - 创建玩家数据类（如 `Player`、`BossParticipant`）
  - **注意**: 不改变运行时行为，仅增强类型提示
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `human-judgment` TR-3.1: 代码具有清晰的类型提示
  - `programmatic` TR-3.2: 所有测试通过

## [/] Task 4: 优化配置访问
- **Priority**: medium
- **Depends On**: Task 3
- **Description**:
  - 在 `config.py` 中添加配置缓存机制
  - 使用 `lru_cache` 缓存常用配置查询（`_cfg`、`_copy`、`_error`）
  - 在 `reload_rpg_config()` 中清理缓存，确保热重载机制继续有效
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-4.1: 所有测试通过
  - `human-judgment` TR-4.2: 热重载机制继续有效

## [ ] Task 5: 运行测试并验证兼容性
- **Priority**: high
- **Depends On**: Task 1-4
- **Description**:
  - 运行所有 RPG 相关测试
  - 运行整个项目测试套件
  - 重点验证单人打怪和世界BOSS两条线的独立性
  - 修复发现的任何问题
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-5.1: 所有 RPG 测试通过
  - `programmatic` TR-5.2: 所有项目测试通过
  - `programmatic` TR-5.3: 功能保持完全兼容，两条线独立

## [/] Task 6: 清理和优化
- **Priority**: low
- **Depends On**: Task 1-5
- **Description**:
  - 清理未使用的导入
  - 优化代码格式（遵循项目规范）
  - 更新 `__init__.py` 确保正确导出所有必要模块
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `programmatic` TR-6.1: 所有测试通过
  - `human-judgment` TR-6.2: 代码整洁，无未使用导入

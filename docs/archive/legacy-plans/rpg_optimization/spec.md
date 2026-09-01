# RPG 功能调优 - 产品需求文档

## Overview
- **Summary**: 对 nonebot_plugin_akito/features/rpg 模块进行代码质量和架构调优，在保持"单人打怪"与"世界BOSS"两条独立线设计的前提下，消除重复代码、改善可维护性、增强类型安全、优化性能。
- **Purpose**: 提升代码质量，降低维护成本，增强扩展性，同时保持原有功能和两条线的独立性不变。
- **Target Users**: 开发者和维护者

## Goals
- [ ] 消除重复代码（如 `_team_success_rate`、`_team_power_bonus`、`_fortune_combat_factor` 等函数的重复实现）
- [ ] 改善代码结构，将 `hunt.py` 拆分为更小的模块（不跨业务线）
- [ ] 增强类型安全，添加类型注解和配置类
- [ ] 优化性能，减少不必要的配置查询和文件读写
- [ ] 保持与现有功能的完全兼容性，不破坏两条线的独立性

## Non-Goals (Out of Scope)
- [ ] 新增游戏玩法或功能
- [ ] 修改游戏数值平衡
- [ ] 重构核心数据存储层（game_store）
- [ ] 修改指令名称或接口
- [ ] 引入新的第三方依赖
- [ ] 合并单人打怪和世界BOSS的业务逻辑

## Background & Context
当前 RPG 模块存在以下问题：
1. **重复代码**：`boss.py` 和 `team.py` 中有多处重复的函数实现（`_team_success_rate`、`_team_power_bonus`）
2. **模块过大**：`hunt.py` 文件超过 900 行，职责过于集中
3. **类型安全不足**：大量使用 `dict` 作为数据结构，缺乏类型检查
4. **配置访问效率低**：每次访问配置都要进行类型检查

**核心设计原则**（来自 README/PLUGIN_MAINTENANCE）：
- **单人打怪 vs 世界BOSS是两条独立线**：普通装备是否损坏不影响打世界BOSS；每只BOSS有独立临时装备和1次出手机会；BOSS奖励不计入普通战绩

## Functional Requirements
- **FR-1**: 提取真正共享的公共函数到 `utils.py`（不跨业务线提取）
- **FR-2**: 将 `hunt.py` 拆分为战斗逻辑、事件处理、奖励计算等子模块（仅单人/组队线）
- **FR-3**: 添加类型注解，引入 `dataclass` 配置类
- **FR-4**: 优化配置访问，添加缓存机制
- **FR-5**: 确保所有原有测试通过，保持功能兼容性

## Non-Functional Requirements
- **NFR-1**: 代码变更应保持与现有接口的完全兼容
- **NFR-2**: 代码行数不应显著增加（目标：减少 10%+）
- **NFR-3**: 测试覆盖率应保持或提升
- **NFR-4**: 性能不应退化（配置访问速度应提升）
- **NFR-5**: 保持单人打怪和世界BOSS两条线的独立性

## Constraints
- **Technical**: 基于现有 nonebot 框架，Python 3.9+
- **Business**: 不改变游戏玩法和数值平衡
- **Dependencies**: 不引入新的第三方库
- **Design**: 不破坏单人打怪与世界BOSS的独立设计

## Assumptions
- [ ] 现有测试套件能够验证核心功能的正确性
- [ ] 热重载机制继续有效
- [ ] 配置文件格式保持不变

## Acceptance Criteria

### AC-1: 重复代码消除
- **Given**: 当前代码中存在 `_team_success_rate`、`_team_power_bonus`、`_fortune_combat_factor` 的重复实现
- **When**: 将这些真正共享的函数提取到 `utils.py` 中
- **Then**: 所有调用处更新为引用共享函数，代码行数减少，功能保持不变，两条线的独立性不受影响
- **Verification**: `programmatic`

### AC-2: hunt.py 模块化拆分
- **Given**: `hunt.py` 文件超过 900 行，包含战斗逻辑、事件处理、奖励计算等多种职责
- **When**: 将其拆分为 `combat.py`（战斗结算）、`events.py`（事件系统）、`rewards.py`（奖励计算）等子模块
- **Then**: 每个文件职责单一（<300行），依赖关系清晰，`boss.py` 保持独立，功能保持不变
- **Verification**: `programmatic`

### AC-3: 类型安全增强
- **Given**: 当前大量使用 `dict` 作为数据结构，缺乏类型检查
- **When**: 添加类型注解，引入 `dataclass` 配置类
- **Then**: 代码具有更好的类型提示，运行时类型错误减少，不影响运行时行为
- **Verification**: `human-judgment`

### AC-4: 配置访问优化
- **Given**: 当前每次访问配置都要进行类型检查和字典查找
- **When**: 添加配置缓存机制，使用 `lru_cache` 缓存常用配置查询
- **Then**: 配置访问速度提升，热重载机制继续有效
- **Verification**: `programmatic`

### AC-5: 测试兼容性
- **Given**: 现有测试套件覆盖核心功能，包含单人打怪、组队、世界BOSS等场景
- **When**: 完成所有代码调优后运行测试
- **Then**: 所有测试通过，功能保持不变，两条线的独立性得到验证
- **Verification**: `programmatic`

## Open Questions
- [ ] 是否需要引入 pydantic 或其他类型校验库？（当前倾向于使用标准库 `dataclasses`）

## 现有测试覆盖分析

### 测试文件清单
| 文件 | 覆盖范围 | 测试用例数 |
|------|----------|-----------|
| `test_player.py` | 等级曲线、装备授予、战力计算、称号系统 | 6 |
| `test_fortune.py` | 签到、运势系统、连签奖励 | 8 |
| `test_hunt.py` | 战斗结算、事件系统、奖励计算、支援场景、精英怪 | 20+ |
| `test_team.py` | 组队成功率、羁绊系统、协作事件、负面事件 | 15+ |
| `test_boss.py` | 世界BOSS生成、攻击、击杀结算、奖励分配 | 20+ |
| `test_smith.py` | 装备强化、回购装备、每日重置 | 10 |
| `test_inventory.py` | 背包管理、物品使用、掉落系统 | 7 |
| `test_character.py` | 状态面板、排行榜、帮助界面 | 8 |

### 测试覆盖评估
- **核心功能覆盖**：所有主要游戏机制都有测试覆盖
- **随机数控制**：使用 `_FixedRand` 和 `monkeypatch` 精确控制随机结果，确保测试可重复
- **边界情况**：包含了装备损坏、未签到、负羁绊等边界场景
- **独立线验证**：测试验证了"普通装备损坏不影响打世界BOSS"等独立性设计

### 与调优需求的匹配
- AC-1（重复代码消除）：现有测试验证功能正确性，重构后测试应全部通过
- AC-2（hunt.py 拆分）：拆分不改变对外接口，现有测试仍然有效
- AC-3（类型安全增强）：不影响运行时行为，现有测试覆盖功能验证
- AC-4（配置访问优化）：现有测试验证配置读取正确性
- AC-5（测试兼容性）：所有测试通过即为验证

### 测试缺口
- [ ] 无专门的性能测试（非必须，可手动验证）
- [ ] 部分错误路径可能未覆盖（如配置缺失的降级处理）

# RPG 功能调优 - 验证清单

- [ ] Checkpoint 1: 重复代码已消除（`_team_success_rate`、`_team_power_bonus`、`_fortune_combat_factor` 等函数只存在一份实现）
- [ ] Checkpoint 2: `hunt.py` 已拆分为多个子模块（`combat.py`、`events.py`、`rewards.py`），每个文件 < 300 行
- [ ] Checkpoint 3: 公共工具函数已提取到 `utils.py`，且不跨业务线
- [ ] Checkpoint 4: `boss.py` 保持独立，未与 hunt 混拆，两条线独立性得到保持
- [ ] Checkpoint 5: 所有函数都有类型注解
- [ ] Checkpoint 6: 配置数据类已创建（`dataclasses`）
- [ ] Checkpoint 7: 配置访问已优化（添加缓存机制），热重载正常工作
- [ ] Checkpoint 8: 所有 RPG 测试通过（`tests/features/rpg/`）
- [ ] Checkpoint 9: 整个项目测试套件通过
- [ ] Checkpoint 10: 代码无未使用导入
- [ ] Checkpoint 11: `__init__.py` 正确导出所有必要模块
- [ ] Checkpoint 12: 功能保持完全兼容（无破坏性变更）
- [ ] Checkpoint 13: 单人打怪和世界BOSS两条线的独立性得到验证

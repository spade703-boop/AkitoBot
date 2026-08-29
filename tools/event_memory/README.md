# 事件记忆工具

- `build_from_scripts.py`：从旧口吻脚本构建兼容事件资产的历史工具。
- `retrieval/`：事件索引构建、召回评测与安全降级验证。
- `story_import/`：剧情抓取、双语证据整理、人工审核、去重和发布工具。

对应测试位于 `tests/tools/event_memory/`，设计文档位于 `docs/conversation_ai/event_memory/`，剧情导入工作区位于 `data/event_memory/story_import/`。

`retrieval/build_index.py` 只有在全部 EventMemory 都成功生成 embedding 时才会原子替换正式索引；任一条失败都会终止发布并保留现有索引。生产构建后应确认成功数等于事件总数。

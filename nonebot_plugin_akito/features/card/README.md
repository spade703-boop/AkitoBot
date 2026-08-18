# 卡面知识库功能

这个目录包含 VBS 卡面知识库的运行时代码：

- `catalog.py`：卡面目录加载、精确别称解析、歧义澄清、事实 Prompt 和别称写回。
- `retrieval.py`：卡面视觉字段质量闸门与卡面语义检索文本。
- `commands.py`：超管维护单卡/卡组别称的 NoneBot 指令。
- `__init__.py`：功能初始化、热重载和 `cards` 检索语料注册。

卡面 JSON、审核队列和向量文件仍放在 `data/` 下，构建和审核脚本仍放在 `tools/` 下；它们是运行数据和离线工具，不参与 `core` 的通用数据层。

`core.retrieval` 只提供通用的可注册语料接口，卡面功能通过 `register_corpus()` 接入，不再把卡面状态或业务逻辑放进 `core`。

# EventMemory 检索工具

- `build_index.py`：为编译后的 `akito_event_memories.json` 构建 Git 忽略的事件向量索引。
- `evaluate.py` / `eval_set.json`：验证事件召回、拒绝误认和禁止候选降噪。

部署时先检查数据，再构建索引：

```powershell
python tools/event_memory/retrieval/build_index.py --check
python tools/event_memory/retrieval/build_index.py
```

构建过程只调用 SiliconFlow embedding API，不访问剧情 Wiki。构建成功后将 `AKITO_EVENT_MEMORY_RETRIEVAL` 从 `lexical` 改为 `hybrid` 并重启或重载配置；索引缺失、过期或 rerank 不可用时会自动使用安全词法降级。

索引发布采用全量成功约束：826 条事件必须全部生成 embedding，任一条失败都会终止构建并保留现有正式索引；运行时同样会拒绝事件下标缺失或数组形状异常的索引。

离线词法评测：

```powershell
python tools/event_memory/retrieval/evaluate.py
```

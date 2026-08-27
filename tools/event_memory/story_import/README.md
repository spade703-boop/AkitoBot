# 彰人/冬弥剧情采集工具

本目录集中放置剧情导入功能的所有部件：

- `web.py`：本地网页服务和 JSON API。
- `ui/index.html`：网页审核界面。
- `cli.py`：抓取、查看、审核、去重和发布的命令行入口。
- `runtime.py`：加载仓库核心以及可选 LLM 分析。
- `start_story_import.bat`：双击启动入口。

抓取、草稿与修订工作区位于 `data/event_memory/story_import/`，发布后的紧凑事件记忆仍写入 `data/content/akito_event_memories.json`。

在仓库根目录可直接双击 `start_story_import.bat`，或运行：

```text
python tools/event_memory/story_import/web.py --data-dir data --port 8765
python tools/event_memory/story_import/cli.py --help
```

基础依赖见 `requirements.txt`；只有启用 LLM 分析时才需要 `requirements-llm.txt` 和 `.env` 中的 `DEEPSEEK_API_KEY`。完整操作说明见 `docs/conversation_ai/event_memory/STORY_IMPORT.md`。

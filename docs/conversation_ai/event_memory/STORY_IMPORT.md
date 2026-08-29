# 彰人/冬弥剧情采集

`tools/event_memory/story_import/web.py` 提供独立的本地网页工作台；`tools/event_memory/story_import/cli.py` 是同一套核心的命令行备用入口。它们把资讯站剧情页面转换为可审核的本地草稿，再把已确认的内容合并进事件记忆。运行时只读取本地事件记忆，不会因为聊天请求访问网页。

## 网页工作台

Windows 用户也可以直接双击仓库根目录的 `start_story_import.bat`；它会启动服务窗口并自动打开浏览器。

在仓库根目录启动：

```text
python tools/event_memory/story_import/web.py --data-dir data --port 8765
```

然后打开 `http://127.0.0.1:8765/`。服务只监听本机，关闭窗口或按 `Ctrl+C` 即可停止；它不会启动 NoneBot，也不会自动打开浏览器。网页可以完成抓取、查看、分析编辑、审核、去重预览和发布，也可以维护页面下方的“已知来源覆盖台账”。

覆盖台账把已抓取草稿、已发布人工事件和手动加入的待办 URL 合并到同一个列表，并显示 `todo`、`draft`、`approved`、`published`、`rejected`、`revision_pending` 状态。AI 可以建议时间阶段、事件类型、参与范围和召回评测问题，但不会自动确认分类，也不会直接修改正式评测集；必须在网页中人工核对并批准。

## 快速流程

在仓库根目录执行：

```text
python tools/event_memory/story_import/cli.py capture --url https://pjsk.moe/zh-cn/story/event/140/8/
python tools/event_memory/story_import/cli.py list --status draft
python tools/event_memory/story_import/cli.py show story-<draft-id>
python tools/event_memory/story_import/cli.py review story-<draft-id> --status approved --note "已核对原作"
python tools/event_memory/story_import/cli.py dedupe story-<draft-id>
python tools/event_memory/story_import/cli.py publish story-<draft-id>
# 同一剧情内容变更时，确认修订后再发布
python tools/event_memory/story_import/cli.py publish story-<draft-id> --confirm-revision
```

`capture` 默认只做规则整理；只有明确追加 `--enrich llm` 才会调用 `DEEPSEEK_API_KEY` 配置的模型生成分析草稿。模型分析仍然必须人工审核，不能把分析草稿当作事实直接发布。长剧情可通过 `DEEPSEEK_MAX_TOKENS` 调整输出上限（默认 3200，程序限制在 256-8192），避免响应在 JSON 中途被截断。

默认使用 `data/` 作为数据根目录，工作文件写入 `data/event_memory/story_import/`：

- `cache/`：网页、主数据、翻译和剧情资产的本地缓存，便于失败重试和复核。
- `drafts/`：完整剧情动作、日文原文、中文译文、彰人/冬弥片段及审核记录。
- `revisions/`：同一剧情确认修订时保存的旧事件和新事件快照。
- `data/content/akito_event_memories.json`：`publish` 的最终事件记忆目标；该文件只写入与现有事件库一致的紧凑事件卡，完整双语原文和证据索引继续保留在草稿中。

部署时只需要把审核后的 `data/content/akito_event_memories.json` 同步到运行环境，并通过 `重载配置` 或重启使其生效。缓存和草稿是本地工作资料，不由 bot 运行时联网读取。

覆盖维护元数据位于 `tools/event_memory/coverage/`，覆盖报告生成到 `docs/conversation_ai/event_memory/COVERAGE_REPORT.md`。无网页环境可用单行命令 `python tools/event_memory/coverage/cli.py sync` 同步状态。该台账的分母只是“已经知道的来源”，不能解释成全游戏剧情覆盖率。

## 支持的路由

入口可以是中文页面，也可以省略语言前缀（省略时按 `zh-CN` 解析）：

| 页面路由 | 例子 | 记录粒度 |
| --- | --- | --- |
| `event` | `/story/event/<event_id>/<episode_no>/` | 活动剧情一话 |
| `unit` | `/story/unit/<story_id>/<episode_no>/` | 组别主线一话 |
| `card` | `/story/card/<card_id>/`（也兼容带 `<episode_no>` 的旧格式） | 卡面剧情 |
| `area` | `/story/area/<area_id>/<episode_no>/` | 区域/地图对话 |
| `self` | `/story/self/<story_id>/` | 自我介绍等单段资料 |
| `special` | `/story/special/<story_id>/<episode_no>/` | 特殊剧情一话 |

抓取器会严格校验 HTTPS、页面域名和参数数量；只允许资讯站页面及其公开资源域名，拒绝任意外部 URL。卡面 ID 始终按日服 `cards.json` / `cardEpisodes.json` 解析，`zh-cn` 只是资讯站界面语言，不代表国服卡面已经上线。省略分段编号时会按日服主数据收集该卡的全部剧情分段；随后用相同的 `assetbundleName` 和 `scenarioId` 分别读取日服日文包与国服中文包，按 `TalkData` 动作顺序配对。带分段编号时只收集指定分段；若日服主数据找不到卡 ID，会直接报错，不会把 `cardEpisodes.id` 或同编号的其他剧情误当成卡面。活动、主线和区域等非卡面剧情也优先读取日服场景包作为日文原文，再按页面语言读取对应翻译；日服资源不可用时才回退到页面地区资源。活动剧情在当前地区主数据缺失时会回退到日服，并在子资源包不可用时尝试父级活动资源包。

## 草稿字段

草稿固定为 `schema_version: 1`，主要字段如下：

- `source`：原始 URL、规范化 URL、语言、路由参数、抓取时间、来源哈希、日服原文/国服译文区域和资产清单。
- `story`：页面标题、场景 ID（多分段以 `+` 连接）、资源包名称；卡面另有 `scenario_ids` 和 `parts` 分段清单。
- `participants`：从说话人中识别出的彰人/冬弥实体。
- `actions`：按顺序保存 `speaker_id`、日文 `text_ja`、中文 `text_zh`、台词类型和动作索引。
- `target_segments`：默认只保留同时涉及彰人/冬弥的目标台词，并用 `evidence_refs` 指向 `actions`；整话动作（包括其他角色）仍保存在 `actions` 中供人工复核。
- `draft_analysis`：可选的摘要、时间线、关系事实、彰人态度、冬弥特征和口吻示例；分析项必须带证据索引。
- `review` / `publish`：审核人、审核时间、备注和发布后的事件记忆 ID。

草稿会完整保留中文证据、`text_ja` 日文原文和全部 `evidence_refs`。发布到事件记忆时，每个目标片段会压缩为一个 `context` / `dialogue` 共同经历单元：`context` 保存冬弥当时的行为、想法和双方情景，`dialogue` 保存同一片段中彰人的关键回应；同一长剧情可以生成多个单元。完整双语原文和长证据索引不重复写入事件库，仍由 `source.draft_id` 和 `record_indices` 回查草稿。页面标题只用于采集界面识别，不作为事件事实或摘要兜底。卡面缺少国服资源时会用日文原文作为紧凑文本的只读兜底，并保留空中文字段供人工核对；不会回退到按卡 ID 命名的旧翻译文件，避免编号碰撞。

## 审核建议

发布前至少确认：

1. 场景 ID、话数和页面标题正确。
2. `target_segments` 中的事实确实能由对应台词支持，没有把旁观者或推测写成彰人的经历。
3. 彰人的态度描述没有把吐槽夸大成否定冬弥，也没有把冬弥的音乐才能等已知事实改写错。
4. 未覆盖的剧情放入 `uncertain_or_missing`，不要为了填满摘要而补写。

写入前建议先执行网页的“去重预览”（CLI 可用 `python tools/event_memory/story_import/cli.py dedupe <draft-id>`）。事件记忆会记录完整动作的 `content_digest` 和目标片段的 `evidence_digest`：同一来源同一内容会幂等跳过；不同来源但摘要相同会阻止新增；同一剧情内容发生变化时，必须确认修订，旧版本会保存到 `data/event_memory/story_import/revisions/`。修正或回滚期间可将灰度变量 `AKITO_M2_MEMORY_MODE` 设为 `off`，立即停止向提示词注入事件记忆。

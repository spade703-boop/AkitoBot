# 任务书：PJSK 知识库补全 + 歌曲/卡面/箱活「圈内昵称」查询支持

> 本文档自包含，按顺序读完即可开工，无需其他上下文。完成后本文件可删除。

---

## 0. 项目背景（30 秒版）

- 这是一个 NoneBot2 + OneBot V11 的 QQ 群角色扮演 bot，扮演 Project Sekai 的「东云彰人」。仓库根目录即插件项目；代码、注释、docstring、commit message **一律中文**。
- 分层规则（docs/PROJECT_SPEC.md §1.3）：`core/`（基础层）←`handlers/`、`features/` 单向依赖；core 不得 import 上层；**所有新核心函数必须在 `core/__init__.py` 导入并加进 `__all__`**。
- 全项目奉行**优雅降级**：任何外部依赖/数据缺失都不许抛异常到调用方，回退旧行为；功能开关用模块级常量（如 `_QUERY_EXPANSION_ENABLED`），一键回退。
- **`data/` 整个目录 gitignore**（语料隐私，刻意不入库）。仓库里看不到数据文件；生产服务器上它们在 `data/content/`。**绝对不要把 data/ 内容提交进 git。**
- 生产部署形态见 `PLUGIN_MAINTENANCE.md`「生产环境部署形态（运维备忘）」一节：bot 跑在 Docker 容器 `mybot`，宿主机 `/akito_bot` 挂载为容器 `/app`；宿主机系统 Python 是 3.6 跑不了本项目，**一切脚本用 `docker exec mybot python ...` 执行**；代码变更需 `docker restart mybot` 生效，数据变更用群内指令「重载配置 assets」热加载。
- 检索系统现状（已上线验证）：语料预计算 BGE-M3 向量（`tools/build_embeddings.py` 产 .npz）→ 运行时 cosine 召回 20 条 → SiliconFlow bge-reranker-v2-m3 精排 → 阈值 0.1 过滤 → 注入 prompt。入口 `core/retrieval.py::retrieve(corpus, query, top_k)`，三态返回（None=不可用降级 / []=无相关命中 / 下标列表）。检索质量评测工具：`tools/eval_retrieval.py` + 考题集 `tools/eval_set.json`。
- 测试：`pytest`（asyncio_mode=auto）；conftest 全局 mock aiohttp/PIL，用 `AKITO_DATA_DIR` 指向 tests/fixtures/test_data。lint：`ruff check nonebot_plugin_akito tools tests`（tools/ 豁免 print，行宽 120；存量错误集中在 test_random_paro_helpers.py，与你无关，**你改的文件必须零新增告警**）。
- commit 规范：`功能: 中文描述` / `修复: ...` / `文档: ...`（见 git log 近期风格），**基于 `master` 开新分支**开发。

## 1. 任务目标（两件事）

1. **补全 `data/content/pjsk_knowledge.json`**：填上已知黑话缺口，新增「VBS 箱活年表」「VBS 卡面图鉴」两个类目（覆盖 VBS 全员：东云彰人/青柳冬弥/白石杏/小豆泽心羽），让群友用圈内昵称问「某个箱活/某张卡面」（如「彰5」「烈火」）时能被现有检索链路命中。
2. **支持圈内昵称问歌**（如「阿吽」=《阿吽のビーツ》）：扩充 `data/content/akito_songs.json`（VBS 原创曲 + 翻唱曲），并**激活其 `keywords` 字段**——当前该字段是死数据，无任何代码消费。机制：群友消息子串命中 keywords → 注入那首歌的完整回忆；同时把现在「每条消息全量注入所有歌简介」改为「平时只注入曲名清单」。

设计分工原则（已和维护者确认）：**歌曲走精确关键词匹配**（专有名词精确匹配优于向量检索；歌曲回忆是第一人称人设内容），**卡面/箱活/黑话走现有 pjsk 语义检索**（世界知识；昵称是强词法锚点，实测 reranker 对专名打分很高），后者零代码、纯内容。

## 2. 数据文件现状（服务器 `data/content/`，repo 里没有；若你的环境有该目录可直接读）

### pjsk_knowledge.json 结构

```json
{
  "introduction": "【⚠️ PJSK 领域知识库 (带语境锁)】……（语境判断总则，勿动）",
  "knowledge_list": [
    {"category": "类目名", "entries": ["条目文本", "..."]}
  ]
}
```

现有 9 个类目共 42 条：①绝密档案·彰冬CP装傻（1条）②易混淆词辨析 SF/车/FC（3条）③基础代称（4条）④难度与打歌 ez~mas/AP/FC/SF（8条）⑤活动/车队 冲榜/效率曲虾龙/跳车/撞车（4条）⑥卡池/抽卡 普池/花前花后/FES/井/补井/沉船（7条）⑦抽卡养成进阶 吃井/限定/复刻/单抽奇迹/专家等级/大小火/大音符（7条）⑧游戏日常社交 跳车拔线/真传皆传/段位（3条）⑨VBS深度主线 白石谦/凪/远野新/古柳大河/Crase Cafe（5条）。

**新条目文风规范**（实测影响检索质量）：核心术语放句首，形如 `术语A/术语B：一句定义。彰人的反应：……`，参考第⑦类的写法；**禁止**模仿第②~⑥类那种 markdown 编号开头（`4. **卡池…** - "井"…`）——评测证明那种开头会稀释向量与精排得分。**现有 42 条一律不动。**

### akito_songs.json 结构

```json
{
  "song_key": {
    "song_name": "《歌名》",
    "description": "彰人第一人称口吻的回忆/态度，一段话",
    "keywords": ["别名1", "别名2"]
  }
}
```

现有 4 首：cinema（《Cinema》）、gekkou（《月光》）、rad_dogs（《RAD DOGS》）、mirai（《未来》），description 都是与剧情绑定的第一人称回忆。**这 4 首的 song_name/description 不动，只允许往 keywords 里补别名。**

## 3. 内容工作明细

> **铁律：禁止臆造。** 能联网就查证（Sekaipedia / pjsek.ai / 官方公告等）：活动名、banner 角色、年月、卡名、星级、限定与否、曲名、作者。**圈内中文俗称**（彰5、烈火这类）绝大多数你查不到可靠来源——一律写占位符 `【俗称待补】`，维护者会手动补。宁可占位，不可编造。无法联网时事实字段也用 `【待补】`。

### 3a. 通用黑话补缺（加入现有类目或新开一类）

至少覆盖：`控分`、`箱活`（单团活动）、`混活`（混合团活动）、`WL/World Link`（单团剧情活动）、`嘉年华/5v5`（Cheerful Carnival 对战活动）、`卡面`（卡牌插画总称）、`歪`（出四星但非当期 UP）、`攒井`（攒 300 抽资源等限定/FES）、`手元`（手元视频）、`划水`（协力摸鱼）。每条带「彰人的反应」短语，语气：毒舌但讲义气、对氪金话题以安抚为主（参考现有⑥⑦类条目的反应写法）。

### 3b. 新类目【VBS 箱活年表（圈内俗称待补）】

每个 VBS banner 活动一条：`「活动名」（谁的 banner，YYYY-MM，箱活/混活）：剧情一句话。圈内俗称：【俗称待补】。` 按时间排列。查证不到剧情梗概就只写事实字段。

### 3c. 新类目【VBS 卡面图鉴（圈内俗称待补）】

覆盖 VBS 四人的**四星卡**（圈内昵称基本只围绕四星）。模板：
`彰N/【俗称待补】：「卡名」（出处活动/卡池，限定|常驻|FES）。卡面：一句客观描述。彰人的态度：……`
（彰N = 该角色第 N 张四星，按实装顺序；冬弥用「冬N」、杏「杏N」、心羽「心羽N」。彰人对自己卡面的态度写得别扭嫌弃一点，对冬弥的卡面嘴上嫌弃内心认可——符合人设。）
另外为维护者点名的俗称「烈火」单独留一条占位：`烈火：【待对应——维护者确认这是哪张卡/哪期活动后补全】`。

### 3d. akito_songs.json 扩充（VBS 原创曲 + 翻唱曲）

- 范围：VBS 的書き下ろし（委托原创）曲全部 + 收录的翻唱（cover）曲。已确认的一例：《阿吽のビーツ》（羽生まゐご），中文圈俗称「阿吽」可直接进 keywords。
- 每首：`song_name`（《》包裹）；`description`——有活动剧情关联的写彰人第一人称回忆（一两句），没有的写一句对曲子/谱面的态度（嫌麻烦但认真的口吻）；`keywords`——日文原名、罗马音、常见中文译名、**有把握的**圈内缩写。
- **keywords 只收高区分度词**：像「未来」这种日常词会让任何聊天误触发，歧义词不进 keywords（可在 description 末尾注一句 `（注：俗称"X"有歧义，待维护者决定是否启用）`）。

### 3e. 评测考题补充（tools/eval_set.json）

为新内容加 4~6 道题（schema 见该文件 `_comment`），例如：`{"query": "这次箱活的剧情好看吗", "corpus": "pjsk", "expect_any": ["箱活"]}`；涉及俗称占位的题在 note 里写明「待维护者补完俗称并重建向量库后生效」。

## 4. 代码工作明细（小改动，照搬现有模式）

### 4a. `nonebot_plugin_akito/core/context.py`

1. 新增 `get_song_mention(text: str) -> str`：
   - 遍历 `SONG_DATA`（dict），对每首歌的 `keywords` 做**子串、不区分大小写**匹配（完全镜像同文件 `get_hybrid_relationship` 对 RELATIONSHIP_DATA keywords 的匹配写法）；
   - 最多取 **2** 首命中（防一条消息点名多首撑爆 prompt）；
   - 输出格式：`\n🎵【歌曲话题】检测到在聊这些歌，回应时用上你的真实记忆：\n- {song_name}：{description}\n`（description **完整注入，不截断**）；
   - 无命中 / SONG_DATA 为空 → 返回 `""`；纯同步函数，无 IO。
2. 改造 `get_song_memories()`（当前实现：遍历全部歌、description 截 120 字、全量注入）→ 改为**曲名清单**：`\n🎵【你会唱的歌】（被问到具体某首时会有详细记忆）：《A》/《B》/…\n`，无数据返回 `""`。docstring 同步更新。

### 4b. `nonebot_plugin_akito/handlers/chat.py`

在主对话 system prompt 组装处（搜 `get_song_memories` 的调用点，就在最终模板拼装函数里）紧挨歌曲清单块注入 `get_song_mention(plain_text_content)` 的结果。注意它是同步函数，不进 `asyncio.gather`。

### 4c. `nonebot_plugin_akito/features/impression.py`

随机插嘴链路同样注入：在它现有的「RELATIONSHIP_DATA 本地关键词扫描」代码块（搜 `relation_info`）旁边并排加 `get_song_mention(msg)`，结果拼进它的 prompt。import 走 `from ..core import get_song_mention`。

### 4d. `nonebot_plugin_akito/core/__init__.py`

`from .context import (...)` 列表与 `__all__` 的 context 段补 `get_song_mention`。

### 4e. 测试

- `tests/test_context.py`：现有 `get_song_memories` 的 3 个用例（空数据/description 优先/120 字截断）按新清单格式重写——注意**截断用例随旧行为一起删除**，改为断言清单含所有曲名、不含 description 正文。
- 新增 `get_song_mention` 用例：命中单首（注入完整 description）；无命中返回空串；命中 3 首只注入 2 首；大小写不敏感（英文别名）；keywords 缺失/空列表不炸。
- mock 方式参照该文件现有用例：`mock.patch("nonebot_plugin_akito.core.context.SONG_DATA", {...})`。

### 4f. 文档（最小化）

- `PLUGIN_MAINTENANCE.md`：context.py 函数表改 `get_song_memories` 描述、加 `get_song_mention` 行；数据清单里 `akito_songs.json` 行注明「keywords 字段被 get_song_mention 消费（圈内昵称放这里）」。
- `README.md` 数据文件表 `akito_songs.json` 行同步一句。

## 5. 验证与交付

1. 本地/CI：`pytest tests/test_context.py tests/test_retrieval.py tests/test_api.py -q` 全绿，然后全量 `pytest -q`（test_random_paro_helpers.py 的 2 个 ERROR 是缺图片素材的存量问题，可忽略）；`ruff check nonebot_plugin_akito tools tests` 不得新增告警。
2. 数据文件**不进 git**：改好的 `pjsk_knowledge.json` / `akito_songs.json` 放服务器 `data/content/` 覆盖（若你在服务器环境直接改即可）。
3. 服务器生效顺序：放数据 → 代码合并后 `git pull` → `docker exec mybot python tools/build_embeddings.py pjsk`（pjsk 条目变了必须重建向量库；songs 不是检索语料，无需 build）→ `docker restart mybot`（有代码变更）。
4. 实测话术：发「阿吽のビーツ的谱面难吗」→ 回复应体现歌曲记忆（日志 DEBUG 可见注入）；发「这次箱活剧情怎么样」→ pjsk 检索命中箱活条目；发「在吗」→ 不应注入任何歌曲详情。
5. 检索质量回归：`docker exec mybot python tools/eval_retrieval.py compare`，新题命中、旧题不退步。

## 6. 红线（再强调）

- 不改 `core/retrieval.py`、`core/api.py`（检索/精排链路刚验证上线，本任务用不着动它们）。
- 不动 pjsk_knowledge.json 现有 42 条与 akito_songs.json 现有 4 首的正文。
- 不编造任何卡名/活动名/俗称；查不到就 `【待补】`。
- 不把 data/ 下任何文件加进 git。
- 中文 commit（`功能:` / `文档:` 前缀），基于 master 开新分支。

"""retrieval.py 纯逻辑测试：mock embed_text + 小向量 + 假 mean，断言中心化 top-k 排序与降级回退。"""

from unittest import mock

import numpy as np
import pytest

from nonebot_plugin_akito.core import retrieval


@pytest.fixture(autouse=True)
def _always_patch_np():
    """确保 tests 下 np 可用（core/__init__.py 的守卫不会覆盖已导入的 numpy）。"""
    with mock.patch.object(retrieval, "np", np, create=True):
        yield


@pytest.fixture
def dummy_vectors():
    """4 条 × 4 维的假语料向量。"""
    return np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [1, 1, 1, 1]], dtype="float32")


@pytest.fixture
def dummy_index(dummy_vectors):
    mean = dummy_vectors.mean(axis=0)
    idx = retrieval._Index(
        vectors=dummy_vectors,
        mean=mean,
        indices=np.arange(len(dummy_vectors), dtype="int32"),
        count=len(dummy_vectors),
    )
    return idx


# ── 辅助 ────────────────────────────────────────────────────────────────────────


async def _mk_embed(text: str) -> list[float] | None:
    """根据 text 内容返回可控 embedding（对应 dummy_vectors）。"""
    if text == "match_0":
        return [1.0, 0.0, 0.0, 0.0]
    if text == "match_1":
        return [0.0, 1.0, 0.0, 0.0]
    if text == "no_match":
        return [0.0, 0.0, 0.0, 1.0]  # 所有行都有 0 维 → 低分
    if text == "error":
        return None
    return None


# ── 加载 / 降级 ──────────────────────────────────────────────────────────────────


def test_reload_indices_no_npz_marks_unavailable():
    """无 .npz 时语料标记为 None 不抛错。"""
    with mock.patch.object(retrieval, "_load_npz", return_value=None):
        cnt = retrieval.reload_indices()
    assert cnt == 0


def test_load_npz_count_mismatch_returns_none(dummy_vectors):
    """count ≠ DB 长度 → 返回 None。"""
    mean = dummy_vectors.mean(axis=0)
    indices = np.arange(len(dummy_vectors), dtype="int32")

    with mock.patch.object(retrieval, "np", np, create=True):
        with mock.patch.object(retrieval, "_find_npz_path", return_value="fake.npz"):
            with mock.patch.object(retrieval, "_ensure_registry"):
                retrieval._registry["scripts"] = {"db": [1, 2, 3, 4], "npz": "fake.npz"}
                # 构造 count=99≠4 的假数据
                with mock.patch.object(np, "load") as mock_load:
                    mock_load.return_value = {
                        "vectors": dummy_vectors,
                        "mean": mean,
                        "indices": indices,
                        "count": np.int32(99),
                    }
                    result = retrieval._load_npz("scripts")
    assert result is None


def test_load_npz_success(dummy_vectors):
    """count==DB长度 → 正确返回 _Index。"""
    mean = dummy_vectors.mean(axis=0)
    indices = np.arange(len(dummy_vectors), dtype="int32")
    count = np.int32(len(dummy_vectors))

    with mock.patch.object(retrieval, "_find_npz_path", return_value="fake.npz"):
        with mock.patch.object(retrieval, "_ensure_registry"):
            retrieval._registry["scripts"] = {"db": [1, 2, 3, 4], "npz": "fake.npz"}
            with mock.patch.object(np, "load") as mock_load:
                mock_load.return_value = {"vectors": dummy_vectors, "mean": mean, "indices": indices, "count": count}
                result = retrieval._load_npz("scripts")
    assert result is not None
    assert result.count == 4


def test_load_npz_scripts_fingerprint_ignores_noise_rows(dummy_vectors):
    """scripts 的 fingerprint 校验应只看 home/story 子集，不能被 noise 干扰。"""
    mean = dummy_vectors.mean(axis=0)
    indices = np.arange(len(dummy_vectors), dtype="int32")
    db = [
        {"type": "home", "cn_key": "a"},
        {"type": "noise", "cn_key": "skip-me"},
        {"type": "story", "cn_key": "b"},
        {"type": "home", "cn_key": "c"},
    ]
    count = np.int32(len(db))
    subset = [db[0], db[2], db[3]]
    fingerprint = retrieval.build_corpus_fingerprint("scripts", subset, retrieval._script_doc_text)

    with mock.patch.object(retrieval, "_find_npz_path", return_value="fake.npz"):
        with mock.patch.object(retrieval, "_ensure_registry"):
            retrieval._registry["scripts"] = {"db": db, "npz": "fake.npz", "doc_text": retrieval._script_doc_text}
            with mock.patch.object(np, "load") as mock_load:
                mock_load.return_value = {
                    "vectors": dummy_vectors,
                    "mean": mean,
                    "indices": indices,
                    "count": count,
                    "fingerprint": np.asarray(fingerprint),
                }
                result = retrieval._load_npz("scripts")
    assert result is not None
    assert result.fingerprint == fingerprint


# ── 检索核心 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_top_k_order(dummy_index):
    """query=match_0 应最高分命中 index 0。"""
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        retrieval._INDICES["scripts"] = dummy_index
        ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert ids is not None
    assert ids[0] == 0  # [1,0,0,0] 与 [1,0,0,0] 完全一致 → 最高分
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_retrieve_centered_similarity():
    """中心化后无关 query 不再都接近 1。"""
    # 创建两个向量：一个类「相关」、一个类「无关」
    vecs = np.array([[1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]], dtype="float32")
    mean = vecs.mean(axis=0)
    idx = retrieval._Index(
        vectors=vecs,
        mean=mean,
        indices=np.arange(2, dtype="int32"),
        count=2,
    )

    async def _embed(text):
        if text == "related":
            return [1.1, 2.1, 3.1, 4.1]  # 接近第一条
        return None

    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _embed):
        retrieval._INDICES["scripts"] = idx
        ids = await retrieval.retrieve("scripts", "related", top_k=2)
    assert ids is not None
    assert ids[0] == 0  # 第 0 条更相似


# ── 降级回退 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_no_numpy_returns_none():
    """np is None → 直接返回 None（不抛错）。"""
    with mock.patch.object(retrieval, "np", None):
        ids = await retrieval.retrieve("scripts", "hello", 5)
    assert ids is None


@pytest.mark.asyncio
async def test_retrieve_unknown_corpus_returns_none():
    """未知语料 → None。"""
    ids = await retrieval.retrieve("nonexistent", "hello", 5)
    assert ids is None


@pytest.mark.asyncio
async def test_retrieve_index_none_returns_none():
    """语料已加载但 _INDICES 中为 None → 降级。"""
    retrieval._INDICES["scripts"] = None
    ids = await retrieval.retrieve("scripts", "hello", 5)
    assert ids is None


@pytest.mark.asyncio
async def test_retrieve_embed_none_returns_none(dummy_index):
    """embed_text 返回 None → 降级。"""
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        retrieval._INDICES["scripts"] = dummy_index
        ids = await retrieval.retrieve("scripts", "error", 5)
    assert ids is None


# ── get_relevant_* 降级 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_relevant_examples_fallback_on_no_retrieval():
    """检索不可用时回退到随机抽取（get_random_examples）。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    with mock.patch(
        "nonebot_plugin_akito.core.context.retrieve",
        return_value=None,
    ):
        result = await get_relevant_examples("test query", 3)
        # 回退到 get_random_examples → 随机结果或空串
        assert isinstance(result, str)


@pytest.mark.asyncio
async def test_get_relevant_pjsk_fallback_to_full_base():
    """PJSK 检索不可用时回退到 intro-only，不再整库灌注。"""
    from nonebot_plugin_akito.core.context import get_relevant_pjsk

    unavailable = retrieval.RetrievalResult(status="unavailable", ids=[], reason="index_unavailable", used_query="test query")
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=unavailable):
        with mock.patch("nonebot_plugin_akito.core.data.PJSK_INTRO", "MOCK_INTRO"):
            result = await get_relevant_pjsk("test query")
            assert result == "MOCK_INTRO"


@pytest.mark.asyncio
async def test_get_relevant_pjsk_intro_always_first():
    """有检索结果时 PJSK_INTRO 永远在最前。"""
    from nonebot_plugin_akito.core.context import get_relevant_pjsk

    fake_entries = [{"category": "测试", "text": "条目1"}, {"category": "测试", "text": "条目2"}]
    fake_intro = "【语境锁】这是前言"
    fake_ids = [0]

    hit = retrieval.RetrievalResult(status="hit", ids=fake_ids, used_query="测试", used_rerank=True)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=hit):
        with mock.patch(
            "nonebot_plugin_akito.core.context.PJSK_ENTRIES",
            fake_entries,
        ):
            # intro 经 data.get_pjsk_intro() 实时读取，patch 源变量
            with mock.patch(
                "nonebot_plugin_akito.core.data.PJSK_INTRO",
                fake_intro,
            ):
                result = await get_relevant_pjsk("测试", num=1)
    # intro 在开头
    assert result.startswith(fake_intro)
    assert "条目1" in result


# ── _Index 结构 ──────────────────────────────────────────────────────────────────


def test_index_centered_and_norms(dummy_index):
    """_Index 自动计算 centered 和 norms。"""
    assert dummy_index.centered.shape == (4, 4)
    assert dummy_index.norms.shape == (4,)
    assert all(n > 0 for n in dummy_index.norms)


# ── 子集契约 ─────────────────────────────────────────────────────────────────────


def test_subset_indices_count_contract():
    """db 长度 10，npz 仅 embed 其中 4 条（原始下标 [0,2,5,7]），count=10。

    应通过 count 校验，且 indices 返回的是原始下标（非 0,1,2,3）。
    此用例覆盖 Part 1 的静默阻断 bug：之前 build 对子集写 count=4，而 db 全量 2502，
    校验恒失败 → scripts 始终降级回随机。
    """
    vecs = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype="float32")
    mean = vecs.mean(axis=0)
    orig_indices = np.array([0, 2, 5, 7], dtype="int32")
    full_count = np.int32(10)  # 全量 DB 长度
    db_mock = list(range(full_count))  # 全量 10 条

    with mock.patch.object(retrieval, "_find_npz_path", return_value="fake.npz"):
        with mock.patch.object(retrieval, "_ensure_registry"):
            retrieval._registry["scripts"] = {"db": db_mock, "npz": "fake.npz"}
            with mock.patch.object(np, "load") as mock_load:
                mock_load.return_value = {
                    "vectors": vecs,
                    "mean": mean,
                    "indices": orig_indices,
                    "count": full_count,
                }
                result = retrieval._load_npz("scripts")
    # count==10==len(db) → 通过校验，非 None
    assert result is not None
    assert result.count == 10
    # indices 是原始下标
    assert list(result.indices) == [0, 2, 5, 7]


@pytest.mark.asyncio
async def test_subset_retrieve_returns_original_indices():
    """子集 .npz（indices=[0,2,5,7]，count=10，db_len=10）检索应返回原始下标。"""
    vecs = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype="float32")
    mean = vecs.mean(axis=0)
    orig_indices = np.array([0, 2, 5, 7], dtype="int32")
    full_count = np.int32(10)
    idx = retrieval._Index(vectors=vecs, mean=mean, indices=orig_indices, count=full_count)

    async def _embed(text):
        if text == "query":
            return [1.0, 0.0, 0.0, 0.0]  # 命中第 0 行（原始下标 0）
        return None

    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _embed):
        retrieval._INDICES["scripts"] = idx
        ids = await retrieval.retrieve("scripts", "query", top_k=3)
    assert ids is not None
    # 应返回原始下标，非 0,1,2
    assert ids == [0, 2, 5]  # 第一命中是原始 idx=0，然后是 2,5


# ── 热重载 registry 刷新 ──────────────────────────────────────────────────────────


def test_reload_indices_clears_stale_registry():
    """reload_indices 先清空 registry 再重建，确保 PJSK_ENTRIES 等引用为最新。"""
    # 先注一个旧引用
    retrieval._registry["test_corp"] = {"db": [1, 2], "npz": "test.npz"}
    with mock.patch.object(retrieval, "_load_npz", return_value=None):
        cnt = retrieval.reload_indices()
    # 重建后只有 scripts + pjsk 两个（来自 _ensure_registry），旧的 test_corp 被清掉
    assert cnt == 0  # _load_npz 全 mock 成 None → 无可用语料
    assert "test_corp" not in retrieval._registry


# ── type-aware 注入 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_relevant_examples_story_format():
    """story 条目用「原作·类似情境」格式；home 条目用现有格式。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_story = {
        "type": "story",
        "context": "杏: どうしたの？",
        "dialogue": "いや、別に。",
    }
    fake_home = {
        "type": "home",
        "context": "在练习室排练",
        "dialogue": "再来一遍。",
    }

    hit = retrieval.RetrievalResult(status="hit", ids=[0, 1], used_query="test", used_rerank=True)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=hit):
        with mock.patch(
            "nonebot_plugin_akito.core.context.SCRIPT_DB",
            [fake_story, fake_home],
        ):
            result = await get_relevant_examples("test", num=2)
    # story 格式
    assert "原作·类似情境" in result
    assert "【原作·类似情境】前情：" in result
    assert "彰人：" in result
    # home 格式
    assert "- 情境：" in result
    assert "台词：" in result
    # 表头
    assert "用中文表达" in result


@pytest.mark.asyncio
async def test_get_relevant_examples_mixed_types():
    """混合 story+home 时两种格式均出现，表头正确。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [
        {"type": "story", "context": "レン: すごい！", "dialogue": "まあな。"},
        {"type": "story", "context": "リン: また？", "dialogue": "うるさい。"},
        {"type": "home", "context": "收下礼物", "dialogue": "给我这个干嘛。"},
    ]

    hit = retrieval.RetrievalResult(status="hit", ids=[0, 1, 2], used_query="test", used_rerank=True)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=hit):
        with mock.patch(
            "nonebot_plugin_akito.core.context.SCRIPT_DB",
            fake_db,
        ):
            result = await get_relevant_examples("test", num=3)
    # 表头
    assert "语义匹配" in result
    assert "用中文表达" in result
    # 两个 story
    assert result.count("【原作·类似情境】") == 2
    # 一个 home
    assert result.count("- 情境：") == 1


# ── 查询扩散 ────────────────────────────────────────────────────────────────────


def test_expand_query_for_retrieval_success():
    """正常返回时 strip 后的文本不为空则返回（mock api 模块级 client）。"""
    import asyncio

    from nonebot_plugin_akito.core import api as api_module

    async def _test():
        fake_client = mock.MagicMock()
        fake_client.chat.completions.create = mock.AsyncMock()
        fake_client.chat.completions.create.return_value.choices = [mock.MagicMock()]
        fake_client.chat.completions.create.return_value.choices[0].message.content = "  刷高分 肝进度 累  "
        with mock.patch.dict(api_module.__dict__, {"client": fake_client}):
            result = await api_module.expand_query_for_retrieval("打虾")
            assert result == "刷高分 肝进度 累"

    asyncio.run(_test())


def test_expand_query_for_retrieval_empty_message():
    """空消息返回 None。"""
    import asyncio

    from nonebot_plugin_akito.core.api import expand_query_for_retrieval

    async def _test():
        assert await expand_query_for_retrieval("") is None
        assert await expand_query_for_retrieval("   ") is None

    asyncio.run(_test())


def _make_fake_client(content=None, side_effect=None):
    """构造一个 fake AsyncOpenAI client 供 expand 测试用。"""
    fake = mock.MagicMock()
    if side_effect:
        fake.chat.completions.create.side_effect = side_effect
        return fake
    fake.chat.completions.create.return_value.choices = [mock.MagicMock()]
    fake.chat.completions.create.return_value.choices[0].message.content = content
    return fake


def test_expand_query_for_retrieval_timeout_returns_none():
    """超时 → None，绝不抛异常。"""
    import asyncio

    from nonebot_plugin_akito.core import api as api_module

    async def _test():
        fake = _make_fake_client(side_effect=asyncio.TimeoutError)
        with mock.patch.object(api_module, "client", fake):
            result = await api_module.expand_query_for_retrieval("测试")
            assert result is None

    asyncio.run(_test())


def test_expand_query_for_retrieval_exception_returns_none():
    """任意异常 → None，绝不抛到调用方。"""
    import asyncio

    from nonebot_plugin_akito.core import api as api_module

    async def _test():
        fake = _make_fake_client(side_effect=RuntimeError("模拟错误"))
        with mock.patch.object(api_module, "client", fake):
            result = await api_module.expand_query_for_retrieval("测试")
            assert result is None

    asyncio.run(_test())


def test_expand_never_returns_character_fallback():
    """验证 expand 函数直调 OpenAI client，绝不走 call_deepseek_api 的角色兜底文案路径。"""
    import asyncio

    from nonebot_plugin_akito.core import api as api_module

    async def _test():
        fake = _make_fake_client(side_effect=RuntimeError("失败"))
        with mock.patch.object(api_module, "client", fake):
            result = await api_module.expand_query_for_retrieval("测试")
        assert result is None
        assert "头好痛" not in str(result or "")

    asyncio.run(_test())


def test_expand_empty_api_response_returns_none():
    """LLM 返回空串或纯空白 → None。"""
    import asyncio

    from nonebot_plugin_akito.core import api as api_module

    async def _test():
        fake = _make_fake_client(content="   ")
        with mock.patch.object(api_module, "client", fake):
            result = await api_module.expand_query_for_retrieval("测试")
            assert result is None

    asyncio.run(_test())


# ── 扩散接入 get_relevant_examples ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_relevant_examples_uses_expanded_query():
    """扩散成功 → 用 blend query 调用 retrieve_result。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [{"type": "home", "context": "ctx", "dialogue": "dl"}]
    captured_retrieval_query = []

    async def fake_retrieve_result(corpus, query, num, ctx=None):
        captured_retrieval_query.append(query)
        return retrieval.RetrievalResult(status="hit", ids=[0], used_query=query, used_rerank=True)

    fake_ctx = retrieval.RetrievalContext(original_query="打虾还是打龙", query="打虾还是打龙 刷高分 肝进度", expanded_query="刷高分 肝进度")
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", fake_retrieve_result):
        with mock.patch("nonebot_plugin_akito.core.context.SCRIPT_DB", fake_db):
            with mock.patch("nonebot_plugin_akito.core.context.build_retrieval_context", return_value=fake_ctx):
                with mock.patch("nonebot_plugin_akito.core.context._QUERY_EXPANSION_ENABLED", True):
                    await get_relevant_examples("打虾还是打龙", num=1)
    assert len(captured_retrieval_query) == 1
    blended = captured_retrieval_query[0]
    assert "打虾" in blended
    assert "刷高分" in blended


@pytest.mark.asyncio
async def test_get_relevant_examples_expansion_none_uses_original():
    """扩散失败 → 用原 query 检索，不报错。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [{"type": "home", "context": "ctx", "dialogue": "dl"}]
    captured_query = []

    async def fake_retrieve_result(corpus, query, num, ctx=None):
        captured_query.append(query)
        return retrieval.RetrievalResult(status="hit", ids=[0], used_query=query, used_rerank=True)

    fake_ctx = retrieval.RetrievalContext(original_query="测试消息", query="测试消息", expanded_query=None)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", fake_retrieve_result):
        with mock.patch("nonebot_plugin_akito.core.context.SCRIPT_DB", fake_db):
            with mock.patch("nonebot_plugin_akito.core.context.build_retrieval_context", return_value=fake_ctx):
                with mock.patch("nonebot_plugin_akito.core.context._QUERY_EXPANSION_ENABLED", True):
                    await get_relevant_examples("测试消息", num=1)
    assert captured_query[0] == "测试消息"


@pytest.mark.asyncio
async def test_get_relevant_examples_expansion_disabled_skips():
    """开关关闭 → 不调扩散，直接用原 query。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [{"type": "home", "context": "ctx", "dialogue": "dl"}]
    captured_query = []
    expand_called = []

    async def fake_retrieve_result(corpus, query, num, ctx=None):
        captured_query.append(query)
        return retrieval.RetrievalResult(status="hit", ids=[0], used_query=query, used_rerank=True)

    fake_ctx = retrieval.RetrievalContext(original_query="测试消息", query="测试消息", expanded_query=None)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", fake_retrieve_result):
        with mock.patch("nonebot_plugin_akito.core.context.SCRIPT_DB", fake_db):
            with mock.patch("nonebot_plugin_akito.core.context.build_retrieval_context", return_value=fake_ctx) as build_mock:
                with mock.patch("nonebot_plugin_akito.core.context._QUERY_EXPANSION_ENABLED", False):
                    await get_relevant_examples("测试消息", num=1)
    assert build_mock.await_count == 1
    assert captured_query[0] == "测试消息"


@pytest.mark.asyncio
async def test_get_relevant_examples_short_query_skip_expansion():
    """极短消息（<3 字）跳过扩散，省调用。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [{"type": "home", "context": "ctx", "dialogue": "dl"}]
    captured_query = []

    async def fake_retrieve_result(corpus, query, num, ctx=None):
        captured_query.append(query)
        return retrieval.RetrievalResult(status="hit", ids=[0], used_query=query, used_rerank=True)

    fake_ctx = retrieval.RetrievalContext(original_query="早", query="早", expanded_query=None)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", fake_retrieve_result):
        with mock.patch("nonebot_plugin_akito.core.context.SCRIPT_DB", fake_db):
            with mock.patch("nonebot_plugin_akito.core.context.build_retrieval_context", return_value=fake_ctx) as build_mock:
                await get_relevant_examples("早", num=1)
    assert build_mock.await_count == 1
    assert captured_query[0] == "早"


# ── 重排序精排 ────────────────────────────────────────────────────────────────


def _fake_script_db(n: int = 4) -> list[dict]:
    """cn_key=d0..dN 的假剧本库（供精排 doc_text 构造）。"""
    return [{"cn_key": f"d{i}", "context": f"ctx{i}", "dialogue": f"dl{i}"} for i in range(n)]


def _patch_scripts_registry(db, with_doc_text: bool = True):
    """把 scripts 语料注入 registry（测试后自动还原）。"""
    cfg = {"db": db, "npz": "fake.npz"}
    if with_doc_text:
        cfg["doc_text"] = retrieval._script_doc_text
    return mock.patch.dict(retrieval._registry, {"scripts": cfg})


def _cand_ids_from_call(rerank_mock) -> list[int]:
    """从捕获的 documents（cn_key=dN）反推候选源下标顺序（cosine 平分项顺序不保证，须实测）。"""
    docs = rerank_mock.await_args.args[1]
    return [int(d[1:]) for d in docs]


@pytest.mark.asyncio
async def test_retrieve_rerank_reorders(dummy_index):
    """精排返回的顺序覆盖 cosine 顺序；query 原样传递、documents 来自 doc_text。"""
    rerank_mock = mock.AsyncMock(return_value=[(1, 0.9), (0, 0.8)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with _patch_scripts_registry(_fake_script_db()):
                retrieval._INDICES["scripts"] = dummy_index
                ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert rerank_mock.await_count == 1
    query_arg, docs_arg = rerank_mock.await_args.args
    assert query_arg == "match_0"
    assert docs_arg[0] == "d0"  # cosine 头名 = 下标 0 的 cn_key
    assert rerank_mock.await_args.kwargs["top_n"] == 2
    cand = _cand_ids_from_call(rerank_mock)
    assert ids == [cand[1], cand[0]]  # 精排把第 2 名提到第 1


@pytest.mark.asyncio
async def test_retrieve_rerank_none_falls_back_to_cosine(dummy_index):
    """精排返回 None（不可用/失败）→ 与旧行为一致的 cosine top-k。"""
    rerank_mock = mock.AsyncMock(return_value=None)
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with _patch_scripts_registry(_fake_script_db()):
                retrieval._INDICES["scripts"] = dummy_index
                ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert rerank_mock.await_count == 1
    assert ids is not None
    assert ids[0] == 0
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_retrieve_rerank_disabled_skips_rerank(dummy_index):
    """_RERANK_ENABLED=False → 不调精排，纯 cosine 旧行为。"""
    rerank_mock = mock.AsyncMock(return_value=[(1, 0.9)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with mock.patch.object(retrieval, "_RERANK_ENABLED", False):
                with _patch_scripts_registry(_fake_script_db()):
                    retrieval._INDICES["scripts"] = dummy_index
                    ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert rerank_mock.await_count == 0
    assert ids is not None
    assert ids[0] == 0
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_retrieve_rerank_threshold_drops(dummy_index):
    """低于 _RERANK_MIN_SCORE 的候选被丢弃。"""
    rerank_mock = mock.AsyncMock(return_value=[(0, 0.9), (1, 0.2)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with mock.patch.object(retrieval, "_RERANK_MIN_SCORE", 0.5):
                with _patch_scripts_registry(_fake_script_db()):
                    retrieval._INDICES["scripts"] = dummy_index
                    ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    cand = _cand_ids_from_call(rerank_mock)
    assert ids == [cand[0]]  # 仅 0.9 的那条存活


@pytest.mark.asyncio
async def test_retrieve_rerank_all_below_threshold_returns_empty(dummy_index):
    """全部低于阈值 → 返回 []（区别于 None，三态契约的关键断言）。"""
    rerank_mock = mock.AsyncMock(return_value=[(0, 0.1), (1, 0.05)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with mock.patch.object(retrieval, "_RERANK_MIN_SCORE", 0.5):
                with _patch_scripts_registry(_fake_script_db()):
                    retrieval._INDICES["scripts"] = dummy_index
                    ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert ids is not None
    assert ids == []


@pytest.mark.asyncio
async def test_retrieve_rerank_invalid_indices_ignored(dummy_index):
    """精排返回越界候选下标 → 忽略不抛，其余正常。"""
    rerank_mock = mock.AsyncMock(return_value=[(99, 0.9), (0, 0.8)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with _patch_scripts_registry(_fake_script_db()):
                retrieval._INDICES["scripts"] = dummy_index
                ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    cand = _cand_ids_from_call(rerank_mock)
    assert ids == [cand[0]]


@pytest.mark.asyncio
async def test_retrieve_rerank_recall_k(dummy_index):
    """_RERANK_RECALL_K 控制送审候选数（top_k=2 但送 3 条）。"""
    rerank_mock = mock.AsyncMock(return_value=[(0, 0.9)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with mock.patch.object(retrieval, "_RERANK_RECALL_K", 3):
                with _patch_scripts_registry(_fake_script_db()):
                    retrieval._INDICES["scripts"] = dummy_index
                    await retrieval.retrieve("scripts", "match_0", top_k=2)
    docs_arg = rerank_mock.await_args.args[1]
    assert len(docs_arg) == 3
    assert rerank_mock.await_args.kwargs["top_n"] == 2


@pytest.mark.asyncio
async def test_retrieve_rerank_missing_doc_text_falls_back(dummy_index):
    """registry 无 doc_text（旧形状）→ 不调精排，回退 cosine 顺序。"""
    rerank_mock = mock.AsyncMock(return_value=[(1, 0.9)])
    with mock.patch("nonebot_plugin_akito.core.api.embed_text", _mk_embed):
        with mock.patch("nonebot_plugin_akito.core.api.rerank_documents", rerank_mock):
            with _patch_scripts_registry(_fake_script_db(), with_doc_text=False):
                retrieval._INDICES["scripts"] = dummy_index
                ids = await retrieval.retrieve("scripts", "match_0", top_k=2)
    assert rerank_mock.await_count == 0
    assert ids is not None
    assert ids[0] == 0


# ── 三态语义：[] 在 get_relevant_* 的注入策略 ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_relevant_examples_empty_ids_falls_back_random():
    """retrieve 返回 []（精排判定无相关）→ 回退随机抽取版头部，而非语义匹配版。"""
    from nonebot_plugin_akito.core.context import get_relevant_examples

    fake_db = [{"type": "home", "context": "ctx", "dialogue": "dl"}]
    no_hit = retrieval.RetrievalResult(status="no_hit", ids=[], used_query="毫无关联的消息", used_rerank=True)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=no_hit):
        with mock.patch("nonebot_plugin_akito.core.context.SCRIPT_DB", fake_db):
            result = await get_relevant_examples("毫无关联的消息", num=1)
    assert "请严格模仿" in result
    assert "语义匹配" not in result


@pytest.mark.asyncio
async def test_get_relevant_pjsk_uses_expanded_query():
    """PJSK 检索同样走查询扩散：扩散成功 → 用 blend query 调用 retrieve_result。"""
    from nonebot_plugin_akito.core.context import get_relevant_pjsk

    fake_entries = [{"category": "测试", "title": "测试-1", "text": "条目1", "prompt_text": "条目1", "aliases": []}]
    captured_query = []

    async def fake_retrieve_result(corpus, query, num, ctx=None):
        captured_query.append(query)
        return retrieval.RetrievalResult(status="hit", ids=[0], used_query=query, used_rerank=True)

    fake_ctx = retrieval.RetrievalContext(original_query="来个人开车", query="来个人开车 组队打歌 协力", expanded_query="组队打歌 协力")
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", fake_retrieve_result):
        with mock.patch("nonebot_plugin_akito.core.context.PJSK_ENTRIES", fake_entries):
            with mock.patch("nonebot_plugin_akito.core.context.build_retrieval_context", return_value=fake_ctx):
                with mock.patch("nonebot_plugin_akito.core.context._QUERY_EXPANSION_ENABLED", True):
                    await get_relevant_pjsk("来个人开车", num=1)
    assert len(captured_query) == 1
    assert "来个人开车" in captured_query[0]
    assert "组队打歌" in captured_query[0]


@pytest.mark.asyncio
async def test_get_relevant_pjsk_empty_ids_intro_only():
    """retrieve 返回 [] 且条目库非空 → 仅注入前言，不再全量灌注。"""
    from nonebot_plugin_akito.core.context import get_relevant_pjsk

    fake_entries = [{"category": "测试", "title": "测试-1", "text": "条目1", "prompt_text": "条目1", "aliases": []}]
    no_hit = retrieval.RetrievalResult(status="no_hit", ids=[], used_query="毫无关联的消息", used_rerank=True)
    with mock.patch("nonebot_plugin_akito.core.context.retrieve_result", return_value=no_hit):
        with mock.patch("nonebot_plugin_akito.core.context.PJSK_ENTRIES", fake_entries):
            with mock.patch("nonebot_plugin_akito.core.data.PJSK_INTRO", "【语境锁】前言"):
                with mock.patch("nonebot_plugin_akito.core.data.PJSK_KNOWLEDGE_BASE", "FULL_BASE"):
                    result = await get_relevant_pjsk("毫无关联的消息")
    assert result == "【语境锁】前言"
    assert "FULL_BASE" not in result

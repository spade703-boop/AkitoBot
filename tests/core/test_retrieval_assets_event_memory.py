from nonebot_plugin_akito.core.retrieval_assets import event_memory_retrieval_text


def test_curated_event_retrieval_text_omits_page_title_and_keeps_evidence():
    event = {
        "source_kind": "curated_story",
        "title": "无意义的页面章节标题",
        "summary": "冬弥发烧仍坚持演出。",
        "topics": ["身体不适"],
        "evidence": [{"context": "冬弥说明自己发烧。", "dialogue": "彰人：撑不住我就阻止你。"}],
    }

    text = event_memory_retrieval_text(event)

    assert "无意义的页面章节标题" not in text
    assert "冬弥发烧仍坚持演出" in text
    assert "撑不住我就阻止你" in text


def test_legacy_event_retrieval_text_keeps_title_as_compatibility_alias():
    event = {
        "source_kind": "legacy_script",
        "title": "冬弥提醒彰人身上有雪",
        "summary": "冬弥提醒彰人。",
        "evidence": [{"context": "雪仗后。", "dialogue": "彰人：谢了。"}],
    }

    text = event_memory_retrieval_text(event)

    assert "冬弥提醒彰人身上有雪" in text
    assert "雪仗后" in text

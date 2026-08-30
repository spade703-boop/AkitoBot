from nonebot_plugin_akito.core.observability import (
    evaluate_auto_reply_shadow,
    finish_turn_trace,
    record_auto_reply_shadow,
    reset_metrics,
    snapshot_metrics,
    start_turn_trace,
)


def test_auto_reply_shadow_keeps_passive_interjection_unknown():
    report = evaluate_auto_reply_shadow("有人在聊天", reply="", anchor="")

    assert report.should_interject is None
    assert report.relevance == "unknown"
    assert report.cross_turn_breach is False

    gated = evaluate_auto_reply_shadow("有人在聊天", silence_reason="probability_gate")
    assert gated.should_interject is None


def test_auto_reply_shadow_detects_current_message_breach():
    report = evaluate_auto_reply_shadow("当前消息", reply="旧消息的回复", anchor="旧消息")

    assert report.anchor_valid is False
    assert report.current_message_only is False
    assert report.cross_turn_breach is True
    assert report.relevance == "irrelevant"


def test_auto_reply_shadow_accuracy_excludes_unknown_samples():
    reset_metrics()
    labeled = start_turn_trace("auto-labeled", surface="auto_chat")
    record_auto_reply_shadow(
        labeled.request_id,
        evaluate_auto_reply_shadow("叫小彰", addressed_to_bot=True, reply="收到", anchor="小彰", actual_interjected=True),
    )
    finish_turn_trace(labeled.request_id, outcome="completed")

    unknown = start_turn_trace("auto-unknown", surface="auto_chat")
    record_auto_reply_shadow(unknown.request_id, evaluate_auto_reply_shadow("普通聊天"))
    finish_turn_trace(unknown.request_id, outcome="silent")

    metrics = snapshot_metrics()["auto_reply_shadow"]
    assert metrics["turns"] == 2
    assert metrics["accuracy"] == 1.0
    assert metrics["relevance"]["unknown"] == 1

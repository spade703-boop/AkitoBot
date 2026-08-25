from tools.evaluate_event_memory import evaluate


def test_event_memory_eval_separates_recall_and_safe_abstention():
    asset = {
        "events": [
            {
                "event_id": "event-birthday",
                "title": "冬弥为彰人准备生日惊喜",
                "summary": "彰人接受了伙伴的生日惊喜。",
                "category": "冬弥·彰冬",
                "topics": ["生日", "惊喜"],
                "keywords": ["生日", "惊喜"],
                "confidence": "high",
            }
        ]
    }
    eval_set = {
        "cases": [
            {"id": "positive", "kind": "positive", "query": "冬弥准备生日惊喜那次？", "expected_event_ids": ["event-birthday"]},
            {"id": "negative", "kind": "negative", "query": "冬弥给你买过跑车吗？", "expected_event_ids": []},
            {"id": "ambiguous", "kind": "ambiguous", "query": "你还记得那次吗？", "expected_event_ids": []},
        ]
    }

    report = evaluate(eval_set, asset)

    assert report["recall_at_1"] == 1.0
    assert report["false_positive_rate"] == 0.0
    assert report["ambiguous_abstention_rate"] == 1.0

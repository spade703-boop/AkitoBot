"""Conversation baseline dataset validation and judge-prompt helpers."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

REQUIRED_CATEGORIES = {
    "casual",
    "follow_up",
    "group_relay",
    "toya_interaction",
    "plot_recall",
    "time_gap",
    "vision",
    "web_search",
    "memory",
    "output_robustness",
}

EVAL_SURFACES = {
    "main_chat",
    "auto_chat",
    "impression_analysis",
    "impression_reply",
}

JUDGE_DIMENSIONS = (
    "factual_grounding",
    "relationship_consistency",
    "emotional_direction",
    "persona_voice",
    "scene_naturalness",
    "invention_control",
)

ANALYSIS_JUDGE_DIMENSIONS = (
    "evidence_grounding",
    "observation_quality",
    "uncertainty_control",
    "attribution_accuracy",
)


def judge_dimensions_for_surface(surface: str) -> tuple[str, ...]:
    """Return the rubric for a surface without judging neutral analysis as roleplay."""
    if surface == "impression_analysis":
        return ANALYSIS_JUDGE_DIMENSIONS
    return JUDGE_DIMENSIONS


def load_eval_set(path: str | Path) -> dict[str, Any]:
    """Load the JSON evaluation set without importing the bot runtime."""
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("评测集根节点必须是对象")
    for case in data.get("cases", []):
        if isinstance(case, dict):
            case.setdefault("surface", "main_chat")
            case.setdefault("task", case.get("category", ""))
    return data


def validate_eval_set(data: dict[str, Any]) -> list[str]:
    """Return human-readable schema errors; an empty list means valid."""
    errors: list[str] = []
    cases = data.get("cases")
    if not isinstance(cases, list):
        return ["cases 必须是数组"]
    if not 50 <= len(cases) <= 100:
        errors.append(f"cases 数量必须在 50～100 之间，当前为 {len(cases)}")

    ids: set[str] = set()
    categories: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        case_id = case.get("id")
        category = case.get("category")
        surface = case.get("surface", "main_chat")
        task = case.get("task", category)
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{prefix}.id 缺失")
        elif case_id in ids:
            errors.append(f"{prefix}.id 重复: {case_id}")
        else:
            ids.add(case_id)
        if not isinstance(category, str) or not category.strip():
            errors.append(f"{prefix}.category 缺失")
        else:
            categories.add(category)
        if surface not in EVAL_SURFACES:
            errors.append(f"{prefix}.surface 无效: {surface}")
        if not isinstance(task, str) or not task.strip():
            errors.append(f"{prefix}.task 缺失")
        if not isinstance(case.get("user_message"), str) or not case["user_message"].strip():
            errors.append(f"{prefix}.user_message 缺失")
        if not isinstance(case.get("expected_signals"), list) or not case["expected_signals"]:
            errors.append(f"{prefix}.expected_signals 必须是非空数组")
        if not isinstance(case.get("forbidden_signals"), list):
            errors.append(f"{prefix}.forbidden_signals 必须是数组")
        if category == "plot_recall":
            reference = case.get("reference")
            if not isinstance(reference, dict):
                errors.append(f"{prefix}.reference 缺失")
            else:
                for key in ("source", "match", "evidence", "speaker"):
                    if not isinstance(reference.get(key), str) or not reference[key].strip():
                        errors.append(f"{prefix}.reference.{key} 缺失")

    missing = sorted(REQUIRED_CATEGORIES - categories)
    if missing:
        errors.append(f"缺少评测类别: {', '.join(missing)}")
    return errors


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def score_signal_diagnostics(case: dict[str, Any], response: str) -> dict[str, Any]:
    """Compute transparent lexical diagnostics, never the final quality score."""
    compact_response = _compact(response)
    expected = [str(item) for item in case.get("expected_signals", []) if str(item).strip()]
    forbidden = [str(item) for item in case.get("forbidden_signals", []) if str(item).strip()]
    expected_hits = [item for item in expected if _compact(item) in compact_response]
    forbidden_hits = [item for item in forbidden if _compact(item) in compact_response]
    return {
        "expected_signal_hits": expected_hits,
        "expected_signal_coverage": round(len(expected_hits) / len(expected), 4) if expected else 0.0,
        "forbidden_signal_hits": forbidden_hits,
        "forbidden_signal_triggered": bool(forbidden_hits),
    }


def build_judge_prompt(case: dict[str, Any], response: str) -> str:
    """Build a rubric prompt that treats source dialogue as evidence, not an answer key."""
    reference = case.get("reference") or {}
    evidence = reference.get("evidence", "（本题没有单独原文证据）")
    scene = reference.get("match", "（普通对话场景）")
    speaker = reference.get("speaker", "（未标注）")
    expected = "、".join(str(item) for item in case.get("expected_signals", [])) or "无"
    forbidden = "、".join(str(item) for item in case.get("forbidden_signals", [])) or "无"
    surface = str(case.get("surface", "main_chat"))
    task = str(case.get("task", case.get("category", "")))
    if surface == "impression_analysis":
        return f"""你是独立的材料质量评测员。请评估群印象的中性材料分析阶段输出。

评测原则：
1. 原始群聊材料是唯一事实来源；不要把彰人语气或角色扮演作为本阶段评分标准。
2. 允许使用新的概括措辞，但 evidence 必须能回溯到材料，推测必须保留不确定性。
3. 只输出 JSON，不输出分析过程。

评测表面：{surface}；任务：{task}
目标对象：{case.get('user_message', '')}
原作场景提示：{scene}
原作证据发言者：{speaker}
原作证据（仅用于核对材料边界）：{evidence}
期望信号：{expected}
禁止漂移：{forbidden}
待评分析：{response}

请按 0～2 分评分：0=不符合，1=部分符合，2=符合。
字段含义：
- evidence_grounding：证据是否来自目标材料
- observation_quality：观察是否具体且可复核
- uncertainty_control：是否区分事实与推测
- attribution_accuracy：是否正确归因于目标本人或其他人物

输出格式：
{{
  "evidence_grounding": 0,
  "observation_quality": 0,
  "uncertainty_control": 0,
  "attribution_accuracy": 0,
  "verdict": "pass|borderline|fail",
  "short_reason": "不超过40字"
}}"""
    surface_guidance = {
        "auto_chat": (
            "自动回复还要判断是否应该插嘴：当前消息无自然回应时，空回复/静默可以是正确结果；"
            "如果回复，则只能锚定当前消息，不能借群聊旧背景强行接话。"
        ),
        "impression_reply": (
            "这里只评估群印象最终的彰人表达；材料事实边界已在 impression_analysis 阶段单独评估，"
            "不要要求复述分析 JSON。"
        ),
    }.get(surface, "")
    return f"""你是独立的角色一致性评测员。请评估一条模拟游戏角色“东云彰人”的回复。

评测原则：
1. 原作台词只是场景、事实、关系和态度的证据，不是需要复述的标准答案。
2. 允许回复使用全新的措辞；不要因为没有复现原句而扣分。
3. 先判断说话人和被谈论人物，不能把冬弥的经历、观点或行为移植给彰人。
4. 如果证据不足，承认不确定或澄清优于编造细节。
5. 只输出 JSON，不输出分析过程。

用户消息：{case.get('user_message', '')}
评测表面：{surface}；任务：{task}
{surface_guidance}
原作场景提示：{scene}
原作证据发言者：{speaker}
原作证据（仅用于核对事实和态度）：{evidence}
期望信号：{expected}
禁止漂移：{forbidden}
待评回复：{response}

请按 0～2 分评分：0=不符合，1=部分符合，2=符合。
字段含义：
- factual_grounding：是否尊重原作事实和说话人归属
- relationship_consistency：是否符合彰人与冬弥的关系
- emotional_direction：情绪方向是否接近原作场景
- persona_voice：是否像彰人，但不要求逐字复刻
- scene_naturalness：是否自然接住群友当前说法
- invention_control：是否避免证据外的具体臆造

输出格式：
{{
  "factual_grounding": 0,
  "relationship_consistency": 0,
  "emotional_direction": 0,
  "persona_voice": 0,
  "scene_naturalness": 0,
  "invention_control": 0,
  "verdict": "pass|borderline|fail",
  "short_reason": "不超过40字"
}}"""


def parse_judge_result(
    raw: str,
    dimensions: tuple[str, ...] = JUDGE_DIMENSIONS,
) -> dict[str, Any] | None:
    """Parse and validate a judge JSON object; invalid output is a failed judge run."""
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        result = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    for dimension in dimensions:
        value = result.get(dimension)
        if not isinstance(value, int) or value not in (0, 1, 2):
            return None
    if result.get("verdict") not in {"pass", "borderline", "fail"}:
        return None
    return result


def summarize_responses(data: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize response diagnostics and optional independent judge scores."""
    cases = {case["id"]: case for case in data["cases"]}
    seen: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    previous_response = ""
    for item in responses:
        case_id = item.get("id")
        if case_id not in cases or case_id in seen:
            continue
        seen.add(case_id)
        response = str(item.get("response") or "")
        result = score_signal_diagnostics(cases[case_id], response)
        case = cases[case_id]
        result.update(
            {
                "id": case_id,
                "category": case["category"],
                "surface": case.get("surface", "main_chat"),
                "task": case.get("task", case["category"]),
            }
        )
        result["exact_duplicate"] = bool(response.strip() and response.strip() == previous_response.strip())
        previous_response = response
        judge = item.get("judge")
        if isinstance(judge, dict):
            result["judge"] = judge
        diagnostics.append(result)

    category_counts = Counter(case["category"] for case in data["cases"])
    surface_counts = Counter(case.get("surface", "main_chat") for case in data["cases"])
    expected_coverage = [item["expected_signal_coverage"] for item in diagnostics]
    forbidden_count = sum(item["forbidden_signal_triggered"] for item in diagnostics)
    duplicate_count = sum(item["exact_duplicate"] for item in diagnostics)
    judge_scores = [item["judge"] for item in diagnostics if isinstance(item.get("judge"), dict)]
    judge_average = {
        dimension: round(sum(int(score[dimension]) for score in judge_scores) / len(judge_scores), 3)
        for dimension in JUDGE_DIMENSIONS
        if judge_scores and all(dimension in score for score in judge_scores)
    }
    surface_judge_average: dict[str, dict[str, float]] = {}
    for surface in sorted(surface_counts):
        surface_scores = [
            item["judge"]
            for item in diagnostics
            if item.get("surface") == surface and isinstance(item.get("judge"), dict)
        ]
        if surface_scores:
            surface_judge_average[surface] = {
                dimension: round(sum(int(score[dimension]) for score in surface_scores) / len(surface_scores), 3)
                for dimension in set().union(*(score.keys() for score in surface_scores))
                if all(dimension in score for score in surface_scores)
                and all(isinstance(score.get(dimension), int) for score in surface_scores)
            }
    return {
        "dataset_cases": len(data["cases"]),
        "response_cases": len(diagnostics),
        "category_counts": dict(sorted(category_counts.items())),
        "surface_counts": dict(sorted(surface_counts.items())),
        "expected_signal_coverage": round(sum(expected_coverage) / len(expected_coverage), 4)
        if expected_coverage
        else None,
        "forbidden_signal_rate": round(forbidden_count / len(diagnostics), 4) if diagnostics else None,
        "exact_duplicate_rate": round(duplicate_count / len(diagnostics), 4) if diagnostics else None,
        "judge_cases": len(judge_scores),
        "judge_average": judge_average,
        "surface_judge_average": surface_judge_average,
        "diagnostics": diagnostics,
    }


def _summarize_trace_group(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one group of traces; callers add surface/stage breakdowns."""
    if not traces:
        return {"total_turns": 0}
    latencies = sorted(float(item.get("elapsed_ms", 0.0)) for item in traces)
    tool_calls = [tool for item in traces for tool in item.get("tool_calls", []) if isinstance(tool, dict)]
    search_calls = [tool for tool in tool_calls if tool.get("name") == "search"]
    parse_observed = [item for item in traces if item.get("parse_success") is not None]
    parse_successes = sum(item.get("parse_success") is True for item in parse_observed)
    memory_observed = [item for item in traces if item.get("memory_hit") is not None]
    memory_hits = sum(item.get("memory_hit") is True for item in memory_observed)
    event_observed = [
        item
        for item in traces
        if item.get("event_retrieval_status") not in {None, "", "disabled", "skipped"}
    ]
    event_hits = sum(item.get("event_retrieval_status") == "hit" for item in event_observed)
    shadow_reports = [
        report
        for item in traces
        for report in item.get("context_shadow", [])
        if isinstance(report, dict)
    ]
    shadow_omitted_sources = Counter(
        str(source)
        for report in shadow_reports
        for source in report.get("omitted_sources", [])
    )

    def percentile(percentile_value: float) -> float:
        index = (len(latencies) - 1) * percentile_value / 100
        lower = int(index)
        upper = min(lower + 1, len(latencies) - 1)
        weight = index - lower
        return round(latencies[lower] + (latencies[upper] - latencies[lower]) * weight, 2)

    return {
        "total_turns": len(traces),
        "completed_turns": sum(item.get("outcome") == "completed" for item in traces),
        "failed_turns": sum(item.get("outcome") == "failed" for item in traces),
        "silent_turns": sum(item.get("outcome") == "silent" for item in traces),
        "guarded_turns": sum(bool(item.get("ambiguity_guard_triggered")) for item in traces),
        "model_calls": sum(int(item.get("model_calls", 0) or 0) for item in traces),
        "prompt_tokens": sum(int(item.get("prompt_tokens", 0) or 0) for item in traces),
        "completion_tokens": sum(int(item.get("completion_tokens", 0) or 0) for item in traces),
        "total_tokens": sum(int(item.get("total_tokens", 0) or 0) for item in traces),
        "avg_tokens": round(
            sum(int(item.get("total_tokens", 0) or 0) for item in traces) / len(traces),
            2,
        ),
        "parse_success_rate": round(parse_successes / len(parse_observed), 4) if parse_observed else None,
        "repeat_rate": round(sum(item.get("repeat_detected") is True for item in traces) / len(traces), 4),
        "memory_hit_rate": round(memory_hits / len(memory_observed), 4) if memory_observed else None,
        "event_hit_rate": round(event_hits / len(event_observed), 4) if event_observed else None,
        "fallback_rate": round(
            sum(bool(item.get("fallback_reason")) for item in traces) / len(traces),
            4,
        ),
        "guard_rate": round(
            sum(bool(item.get("ambiguity_guard_triggered")) for item in traces) / len(traces),
            4,
        ),
        "search_requests": len(search_calls),
        "search_success_rate": round(
            sum(tool.get("status") == "success" for tool in search_calls) / len(search_calls), 4
        )
        if search_calls
        else None,
        "retries": sum(int(item.get("retries", 0) or 0) for item in traces),
        "context_shadow_reports": len(shadow_reports),
        "context_shadow_total_blocks": sum(int(report.get("total_blocks", 0) or 0) for report in shadow_reports),
        "context_shadow_estimated_tokens": sum(
            int(report.get("estimated_tokens", 0) or 0) for report in shadow_reports
        ),
        "context_shadow_omitted_sources": dict(sorted(shadow_omitted_sources.items())),
        "p50_latency_ms": percentile(50),
        "p95_latency_ms": percentile(95),
    }


def summarize_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate traces and expose comparable metrics for each conversation surface."""
    if not traces:
        return {"total_turns": 0}
    summary = _summarize_trace_group(traces)
    surfaces = Counter(str(item.get("surface") or "main_chat") for item in traces)
    stages = Counter(str(item.get("stage") or "response") for item in traces)
    experiment_arms = Counter(str(item.get("experiment_arm") or "default") for item in traces)
    groups = Counter(str(item.get("group_id") or "unknown") for item in traces)
    summary["surface_counts"] = dict(sorted(surfaces.items()))
    summary["stage_counts"] = dict(sorted(stages.items()))
    summary["experiment_arm_counts"] = dict(sorted(experiment_arms.items()))
    summary["group_counts"] = dict(sorted(groups.items()))
    summary["surface_metrics"] = {
        surface: _summarize_trace_group(
            [item for item in traces if str(item.get("surface") or "main_chat") == surface]
        )
        for surface in sorted(surfaces)
    }
    summary["experiment_arm_metrics"] = {
        arm: _summarize_trace_group(
            [item for item in traces if str(item.get("experiment_arm") or "default") == arm]
        )
        for arm in sorted(experiment_arms)
    }
    summary["experiment_arm_surface_metrics"] = {
        arm: {
            surface: _summarize_trace_group(
                [
                    item
                    for item in traces
                    if str(item.get("experiment_arm") or "default") == arm
                    and str(item.get("surface") or "main_chat") == surface
                ]
            )
            for surface in sorted(
                {
                    str(item.get("surface") or "main_chat")
                    for item in traces
                    if str(item.get("experiment_arm") or "default") == arm
                }
            )
        }
        for arm in sorted(experiment_arms)
    }
    return summary


def render_baseline_report(
    data: dict[str, Any],
    *,
    responses: list[dict[str, Any]] | None = None,
    runtime_metrics: dict[str, Any] | None = None,
) -> str:
    """Render a Markdown baseline report without inventing unavailable runtime values."""
    summary = summarize_responses(data, responses or [])
    lines = [
        "# M0 对话基线报告",
        "",
        "> 这是 M0 的可重复基线。剧情样例使用原作证据进行结构化评测，不进行逐字匹配。",
        "",
        "## 评测集",
        "",
        f"- 样例总数：{summary['dataset_cases']}",
        f"- 类别分布：{json.dumps(summary['category_counts'], ensure_ascii=False)}",
        f"- 表面分布：{json.dumps(summary['surface_counts'], ensure_ascii=False)}",
        f"- 已提供回复：{summary['response_cases']}",
        "- 剧情回忆样例：12 条，均绑定原作场景证据",
        "",
        "## 回复诊断",
        "",
        f"- 期望信号字面覆盖率：{summary['expected_signal_coverage'] if summary['response_cases'] else '待采集'}",
        f"- 禁止信号触发率：{summary['forbidden_signal_rate'] if summary['response_cases'] else '待采集'}",
        f"- 连续完全复读率：{summary['exact_duplicate_rate'] if summary['response_cases'] else '待采集'}",
        f"- AI 裁判样例数：{summary['judge_cases']}",
        f"- AI 裁判平均分：{json.dumps(summary['judge_average'], ensure_ascii=False) if summary['judge_average'] else '待采集'}",
        "",
        "## 运行时指标",
        "",
    ]
    if runtime_metrics:
        lines.extend(f"- {key}: {value}" for key, value in runtime_metrics.items())
    else:
        lines.append("- 尚无在线回合 trace；启动 bot 并完成评测集回放后再填充。")
    lines.extend(
        [
            "",
        "## 解释边界",
            "",
            "- 原作台词用于核对事实、关系和情绪方向，不是唯一正确答案。",
            "- 字面信号覆盖率仅用于诊断，不能替代角色裁判分数。",
            "- AI 裁判应尽量使用与生成模型不同的模型，并抽样人工复核。",
            "",
            "## 复现方式",
            "",
            "- 校验评测集：`python tools/conversation_ai/baseline/evaluate.py --validate-only`",
            "- 读取回放结果：`python tools/conversation_ai/baseline/evaluate.py --responses path/to/responses.jsonl`",
            "- 汇总在线 trace：设置 `AKITO_CONVERSATION_TRACE_PATH` 后运行 `python tools/conversation_ai/baseline/evaluate.py --traces path/to/traces.jsonl`",
            "- 启用结构化 AI 裁判：追加 `--judge --judge-model <model>`，并配置 `DEEPSEEK_API_KEY`",
            '- 回放结果每行至少包含 `{"id":"casual-001","response":"..."}`；可选附带 `judge` 字段',
        ]
    )
    return "\n".join(lines) + "\n"

"""Generate a safe rollout acceptance report from privacy-safe JSONL traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .conversation_eval import summarize_traces
except ImportError:
    from conversation_eval import summarize_traces


def load_traces(path: Path) -> list[dict[str, Any]]:
    """Read one sanitized trace object per line and reject malformed input."""
    traces: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"trace 第 {line_number} 行不是 JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"trace 第 {line_number} 行必须是对象")
            traces.append(item)
    return traces


def _rate(group: dict[str, Any], numerator: str, denominator: str = "total_turns") -> float | None:
    total = int(group.get(denominator, 0) or 0)
    if not total:
        return None
    return round(int(group.get(numerator, 0) or 0) / total, 4)


def _arm_observation(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "turns": int(group.get("total_turns", 0) or 0),
        "completed_rate": _rate(group, "completed_turns"),
        "failed_rate": _rate(group, "failed_turns"),
        "silent_rate": _rate(group, "silent_turns"),
        "parse_success_rate": group.get("parse_success_rate"),
        "avg_tokens": group.get("avg_tokens"),
        "p50_latency_ms": group.get("p50_latency_ms"),
        "p95_latency_ms": group.get("p95_latency_ms"),
        "event_hit_rate": group.get("event_hit_rate"),
        "fallback_rate": group.get("fallback_rate"),
        "repeat_rate": group.get("repeat_rate"),
    }


def _comparison_check(
    metric: str,
    control_value: float | None,
    treatment_value: float | None,
    *,
    direction: str,
    tolerance: float,
    ratio_limit: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metric": metric,
        "control": control_value,
        "treatment": treatment_value,
        "delta": round(treatment_value - control_value, 4)
        if control_value is not None and treatment_value is not None
        else None,
        "status": "not_observed",
    }
    if control_value is None or treatment_value is None:
        return result
    if ratio_limit is not None and control_value > 0:
        result["ratio"] = round(treatment_value / control_value, 4)
        regressed = treatment_value / control_value > ratio_limit
    elif direction == "lower_is_better":
        regressed = treatment_value - control_value > tolerance
    else:
        regressed = control_value - treatment_value > tolerance
    result["status"] = "review" if regressed else "pass"
    return result


def build_rollout_report(
    traces: list[dict[str, Any]],
    *,
    control_arm: str = "default",
    treatment_arm: str = "combined",
    min_turns: int = 30,
    single_arm: bool = False,
) -> dict[str, Any]:
    """Compare arms, or observe one treatment arm when no control is available."""
    summary = summarize_traces(traces)
    arm_metrics = summary.get("experiment_arm_metrics", {})
    control_group = None if single_arm else arm_metrics.get(control_arm)
    treatment_group = arm_metrics.get(treatment_arm)
    missing_arms = [
        arm for arm, group in ((control_arm, control_group), (treatment_arm, treatment_group)) if group is None
    ]
    if single_arm:
        missing_arms = []
    observations = {
        "control": _arm_observation(control_group or {}),
        "treatment": _arm_observation(treatment_group or {}),
    }
    checks: list[dict[str, Any]] = []
    if control_group and treatment_group:
        checks.extend(
            [
                _comparison_check(
                    "failed_rate",
                    observations["control"]["failed_rate"],
                    observations["treatment"]["failed_rate"],
                    direction="lower_is_better",
                    tolerance=0.02,
                ),
                _comparison_check(
                    "parse_success_rate",
                    observations["control"]["parse_success_rate"],
                    observations["treatment"]["parse_success_rate"],
                    direction="higher_is_better",
                    tolerance=0.02,
                ),
                _comparison_check(
                    "p95_latency_ms",
                    observations["control"]["p95_latency_ms"],
                    observations["treatment"]["p95_latency_ms"],
                    direction="lower_is_better",
                    tolerance=0.0,
                    ratio_limit=1.25,
                ),
                _comparison_check(
                    "avg_tokens",
                    observations["control"]["avg_tokens"],
                    observations["treatment"]["avg_tokens"],
                    direction="lower_is_better",
                    tolerance=0.0,
                    ratio_limit=1.25,
                ),
            ]
        )
    arms_to_check = ((treatment_arm, treatment_group),) if single_arm else (
        (control_arm, control_group),
        (treatment_arm, treatment_group),
    )
    insufficient_arms = [
        arm
        for arm, group in arms_to_check
        if group is None or int(group.get("total_turns", 0) or 0) < max(1, min_turns)
    ]
    if missing_arms or insufficient_arms:
        verdict = "insufficient_data"
    elif single_arm:
        verdict = "single_arm_observation"
    elif any(check["status"] == "review" for check in checks):
        verdict = "review"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "mode": "single_arm" if single_arm else "comparison",
        "control_arm": control_arm,
        "treatment_arm": treatment_arm,
        "min_turns": max(1, min_turns),
        "total_turns": summary.get("total_turns", 0),
        "arm_counts": summary.get("experiment_arm_counts", {}),
        "group_counts": summary.get("group_counts", {}),
        "surface_counts": summary.get("surface_counts", {}),
        "missing_arms": missing_arms,
        "insufficient_arms": insufficient_arms,
        "observations": observations,
        "checks": checks,
        "experiment_arm_surface_metrics": summary.get("experiment_arm_surface_metrics", {}),
    }


def render_rollout_report(report: dict[str, Any], *, trace_path: str = "") -> str:
    """Render a Markdown report with explicit data and human-review boundaries."""
    observations = report["observations"]
    lines = [
        "# 灰度验收报告",
        "",
        "> 本报告只使用匿名化 trace 元数据；它不能替代对实际回复内容的人工抽查。",
        "",
        f"- 结论：**{report['verdict']}**",
        f"- Trace 文件：`{trace_path or '未注明'}`",
        f"- 总回合：{report['total_turns']}",
        f"- 对照臂：`{report['control_arm']}`（{observations['control']['turns']} 回合）"
        if report["mode"] == "comparison"
        else "- 对照臂：未启用（单臂观察，不提供因果比较）",
        f"- 实验臂：`{report['treatment_arm']}`（{observations['treatment']['turns']} 回合）",
        f"- 最低样本要求：{'每臂' if report['mode'] == 'comparison' else '实验臂'} {report['min_turns']} 回合",
        "",
        "## 指标对比",
        "",
        "| 指标 | 对照臂 | 实验臂 | 差值/比例 | 状态 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for check in report["checks"]:
        comparison = check.get("ratio", check.get("delta", "-"))
        lines.append(
            f"| {check['metric']} | {check['control'] if check['control'] is not None else '-'} | "
            f"{check['treatment'] if check['treatment'] is not None else '-'} | {comparison} | {check['status']} |"
        )
    if report["mode"] == "single_arm":
        lines.extend(
            [
                "| 单臂绝对指标 | - | - | - | 仅供观察 |",
                f"| completed_rate | - | {observations['treatment']['completed_rate']} | - | 观察 |",
                f"| failed_rate | - | {observations['treatment']['failed_rate']} | - | 观察 |",
                f"| parse_success_rate | - | {observations['treatment']['parse_success_rate']} | - | 观察 |",
                f"| p95_latency_ms | - | {observations['treatment']['p95_latency_ms']} | - | 观察 |",
                f"| avg_tokens | - | {observations['treatment']['avg_tokens']} | - | 观察 |",
                f"| event_hit_rate | - | {observations['treatment']['event_hit_rate']} | - | 观察 |",
                f"| fallback_rate | - | {observations['treatment']['fallback_rate']} | - | 观察 |",
            ]
        )
    lines.extend(
        [
            "",
            "## 分面与实验臂",
            "",
            f"- 实验臂分布：`{json.dumps(report['arm_counts'], ensure_ascii=False)}`",
            f"- 群组分布：`{json.dumps(report['group_counts'], ensure_ascii=False)}`",
            f"- 表面分布：`{json.dumps(report['surface_counts'], ensure_ascii=False)}`",
            "- 详细分面指标已保存在本报告生成所用的 JSON 汇总中；本 Markdown 不展开每个分面表格。",
            "",
            "## 人工复核清单",
            "",
            "- [ ] 抽查至少 10 条主动对话：是否答非所问、是否自然承接追问。",
            "- [ ] 抽查至少 5 条剧情回忆：是否错误认领、是否把不确定细节说成事实。",
            "- [ ] 抽查至少 5 条虚构/模糊问法：是否安全拒绝或澄清，而不是补写剧情。",
            "- [ ] 抽查自动回复：不该插嘴时是否保持安静，只回应当前消息。",
            "- [ ] 抽查群印象：材料分析是否中性，最终表达是否仍像彰人。",
            "- [ ] 对发现的问题记录 request id、surface、experiment arm 和简短现象；不要记录用户原文到报告。",
            "",
            "## 放量建议",
            "",
            "- `insufficient_data`：继续收集，不据此扩大或回滚。",
            "- `single_arm_observation`：单群阶段的稳定性观察，不等价于 A/B 通过。",
            "- `review`：先人工复核回退项和失败样例，必要时切回 `control`。",
            "- `pass`：仍需完成上面的人工清单后，才考虑扩大到更多群。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 M1/M2 灰度验收报告")
    parser.add_argument("--traces", required=True, help="bot 写出的匿名化 JSONL trace")
    parser.add_argument("--output", default="docs/ROLLOUT_ACCEPTANCE.md")
    parser.add_argument(
        "--control-arm",
        default="default",
        help="对照实验臂；未映射群在 trace 中记作 default",
    )
    parser.add_argument("--treatment-arm", default="combined")
    parser.add_argument("--min-turns", type=int, default=30)
    parser.add_argument(
        "--single-arm",
        action="store_true",
        help="只有一个活跃实验群时，只报告实验臂绝对指标，不要求 control/default",
    )
    parser.add_argument("--strict", action="store_true", help="insufficient_data/review 时返回非零退出码")
    args = parser.parse_args()
    trace_path = Path(args.traces)
    traces = load_traces(trace_path)
    report = build_rollout_report(
        traces,
        control_arm=args.control_arm,
        treatment_arm=args.treatment_arm,
        min_turns=args.min_turns,
        single_arm=args.single_arm,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_rollout_report(report, trace_path=str(trace_path)), encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "total_turns": report["total_turns"],
                "arm_counts": report["arm_counts"],
                "mode": report["mode"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 2 if args.strict and report["verdict"] in {"insufficient_data", "review"} else 0


if __name__ == "__main__":
    raise SystemExit(main())

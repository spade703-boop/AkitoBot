"""Validate, optionally judge, and report the M0 conversation evaluation set."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv

try:
    from .conversation_eval import (
        build_judge_prompt,
        judge_dimensions_for_surface,
        load_eval_set,
        parse_judge_result,
        render_baseline_report,
        summarize_responses,
        summarize_traces,
        validate_eval_set,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.conversation_eval import (
        build_judge_prompt,
        judge_dimensions_for_surface,
        load_eval_set,
        parse_judge_result,
        render_baseline_report,
        summarize_responses,
        summarize_traces,
        validate_eval_set,
    )


def load_responses(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per line from a replay result file."""
    responses: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"responses 第 {line_number} 行不是 JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"responses 第 {line_number} 行必须是对象")
            responses.append(item)
    return responses


def validate_reference_files(data: dict[str, Any], root: Path) -> list[str]:
    """Verify plot references still point at an existing source entry."""
    errors: list[str] = []
    cache: dict[Path, Any] = {}
    for case in data.get("cases", []):
        reference = case.get("reference") if isinstance(case, dict) else None
        if not isinstance(reference, dict):
            continue
        source = root / str(reference.get("source", ""))
        match = str(reference.get("match", ""))
        if not source.exists():
            errors.append(f"{case.get('id')}: 原作来源不存在 {source}")
            continue
        if source not in cache:
            try:
                cache[source] = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{case.get('id')}: 原作来源无法读取: {exc}")
                continue
        payload = cache[source]
        if not isinstance(payload, list):
            errors.append(f"{case.get('id')}: 原作来源不是数组")
            continue
        field = str(reference.get("match_field") or "cn_key")
        if not any(isinstance(item, dict) and match in str(item.get(field, "")) for item in payload):
            errors.append(f"{case.get('id')}: 找不到 {field}={match}")
    return errors


async def judge_responses(
    data: dict[str, Any],
    responses: list[dict[str, Any]],
    *,
    model_name: str,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Use an explicitly selected model as an independent structured judge."""
    from openai import AsyncOpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("--judge 需要配置 DEEPSEEK_API_KEY")
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    cases = {case["id"]: case for case in data["cases"]}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def judge_one(item: dict[str, Any]) -> dict[str, Any]:
        case = cases.get(item.get("id"))
        if case is None:
            return item
        async with semaphore:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是严格、独立、只输出 JSON 的角色一致性评测员。"},
                    {"role": "user", "content": build_judge_prompt(case, str(item.get("response") or ""))},
                ],
                temperature=0,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            parsed = parse_judge_result(
                raw,
                judge_dimensions_for_surface(str(case.get("surface", "main_chat"))),
            )
            enriched = dict(item)
            enriched["judge"] = parsed or {"verdict": "fail", "short_reason": "裁判输出无法解析"}
            return enriched

    return await asyncio.gather(*(judge_one(item) for item in responses))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="生成 M0 对话评测基线报告")
    parser.add_argument("--eval-set", default="tools/conversation_eval_set.json")
    parser.add_argument("--responses", help="JSONL 回放结果，每行至少包含 id 和 response")
    parser.add_argument("--traces", help="bot 运行时写出的 JSONL trace 文件")
    parser.add_argument("--output", default="docs/M0_BASELINE.md")
    parser.add_argument("--judge", action="store_true", help="调用独立模型对已有 responses 做结构化裁判")
    parser.add_argument("--judge-model", default="deepseek-v4-flash")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--validate-only", action="store_true", help="只校验评测集和原作证据引用")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    data = load_eval_set(root / args.eval_set)
    errors = validate_eval_set(data) + validate_reference_files(data, root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 2
    if args.validate_only:
        print(f"评测集校验通过: {len(data['cases'])} 条")
        return 0

    responses = load_responses(root / args.responses) if args.responses else []
    traces = load_responses(root / args.traces) if args.traces else []
    if args.judge:
        if not responses:
            print("ERROR: --judge 必须同时提供 --responses")
            return 2
        responses = asyncio.run(
            judge_responses(
                data,
                responses,
                model_name=args.judge_model,
                concurrency=args.concurrency,
            )
        )

    runtime_metrics = summarize_traces(traces) if traces else None
    report = render_baseline_report(data, responses=responses, runtime_metrics=runtime_metrics)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    summary = summarize_responses(data, responses)
    print(f"评测集校验通过: {summary['dataset_cases']} 条")
    print(f"基线报告已写入: {output}")
    if responses:
        print(f"已分析回复: {summary['response_cases']} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

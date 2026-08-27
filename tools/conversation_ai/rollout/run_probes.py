"""Run role-quality probes through the combined rollout without sending messages."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class ProbeQuestion:
    probe_id: str
    question: str
    session: str


def _parse_markdown_questions(path: Path) -> list[ProbeQuestion]:
    text = path.read_text(encoding="utf-8")
    probes: list[ProbeQuestion] = []
    table_pattern = re.compile(r"^\|\s*([A-Z]\d+)\s*\|\s*(.*?)\s*\|", re.MULTILINE)
    for match in table_pattern.finditer(text):
        probe_id, question = match.groups()
        if probe_id in {"ID", "编号"} or not question.strip():
            continue
        probes.append(ProbeQuestion(probe_id, question.strip(), probe_id))

    sequence_pattern = re.compile(r"^###\s+(D\d+)\s*$([\s\S]*?)(?=^###\s+|\Z)", re.MULTILINE)
    for group in sequence_pattern.finditer(text):
        session = group.group(1)
        questions = re.findall(r"^\d+\.\s+`([^`]+)`", group.group(2), re.MULTILINE)
        for index, question in enumerate(questions, 1):
            probes.append(ProbeQuestion(f"{session}-{index}", question.strip(), session))
    return probes


def _load_questions(path: Path) -> list[ProbeQuestion]:
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return _parse_markdown_questions(path)
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("questions", rows.get("cases", []))
    if not isinstance(rows, list):
        raise ValueError("probe input must be an array or JSONL objects")
    probes = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"probe row {index} must be an object")
        probe_id = str(row.get("id") or row.get("probe_id") or f"P{index:02d}").strip()
        question = str(row.get("question") or row.get("user_message") or "").strip()
        if not question:
            raise ValueError(f"probe {probe_id} has no question")
        session = str(row.get("session") or probe_id.split("-", 1)[0]).strip()
        probes.append(ProbeQuestion(probe_id, question, session))
    return probes


def _append_history(prepared: Any, reply: Any) -> None:
    prepared.user_mem.setdefault("history", []).extend(
        [
            {"role": "user", "content": prepared.tagged_user_msg_for_history},
            {
                "role": "assistant",
                "content": json.dumps(
                    {"inner_os": reply.inner_os or "", "reply": reply.text},
                    ensure_ascii=False,
                ),
            },
        ]
    )


async def _run_probe(
    probe: ProbeQuestion,
    *,
    run_id: str,
    group_id: str,
    user_id: str,
    session_key: str,
) -> dict[str, Any]:
    from nonebot_plugin_akito.core import finish_turn_trace, new_request_id, start_turn_trace
    from nonebot_plugin_akito.handlers import chat_pipeline

    request_id = new_request_id()
    start_turn_trace(request_id, group_id=group_id, surface="main_chat", stage="response")
    turn = chat_pipeline.IncomingTurn(
        session_key=session_key,
        message_id=f"probe-{run_id}-{probe.probe_id}",
        user_id=user_id,
        group_id=group_id,
        sender_nickname="探针用户",
        plain_text_content=probe.question,
        has_image=False,
        current_image_identity="",
        image_analysis=None,
        has_reply=False,
        reply_target_is_toya=False,
        origin_sender="",
        request_id=request_id,
    )
    started = time.perf_counter()
    try:
        gate = chat_pipeline.decide_gate(turn)
        if gate.skip_send:
            trace = finish_turn_trace(request_id, outcome="silent") or {}
            return {
                "id": probe.probe_id,
                "question": probe.question,
                "response": None,
                "inner_os": "",
                "status": "silent",
                "request_id": request_id,
                "trace": trace,
            }
        if gate.text is not None:
            trace = finish_turn_trace(request_id, outcome="completed") or {}
            return {
                "id": probe.probe_id,
                "question": probe.question,
                "response": gate.text,
                "inner_os": "",
                "status": "completed",
                "request_id": request_id,
                "trace": trace,
            }

        prepared = await chat_pipeline.prepare_turn(turn, gate.sleep_instruction)
        reply = await chat_pipeline.generate_reply(prepared)
        reply = await chat_pipeline.post_process_reply(prepared, reply)
        _append_history(prepared, reply)
        trace = finish_turn_trace(request_id, outcome="completed") or {}
        return {
            "id": probe.probe_id,
            "question": probe.question,
            "response": reply.text,
            "inner_os": reply.inner_os,
            "status": "completed",
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "trace": trace,
        }
    except Exception as exc:
        trace = finish_turn_trace(request_id, outcome="failed") or {}
        return {
            "id": probe.probe_id,
            "question": probe.question,
            "response": None,
            "inner_os": "",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "trace": trace,
        }


async def run_probes(probes: list[ProbeQuestion], *, group_id: str, output: Path | None) -> list[dict[str, Any]]:
    from nonebot_plugin_akito.core import MEMORY_DB, SUPERUSER_QQ
    from nonebot_plugin_akito.handlers import chat_pipeline

    run_id = uuid4().hex[:10]
    user_id = SUPERUSER_QQ or f"probe-user-{run_id}"
    session_keys = {probe.session: f"probe_{run_id}_{probe.session}" for probe in probes}
    results: list[dict[str, Any]] = []
    original_save_memory = chat_pipeline.save_memory
    chat_pipeline.save_memory = lambda: None
    try:
        for probe in probes:
            result = await _run_probe(
                probe,
                run_id=run_id,
                group_id=group_id,
                user_id=user_id,
                session_key=session_keys[probe.session],
            )
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    finally:
        chat_pipeline.save_memory = original_save_memory
        for session_key in session_keys.values():
            MEMORY_DB.pop(session_key, None)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n" for result in results),
            encoding="utf-8",
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local role-quality probes through the combined rollout")
    parser.add_argument("--input", default="docs/conversation_ai/rollout/PROBE_SET.md")
    parser.add_argument("--output", default="data/conversation_ai/rollout/probe_results.jsonl")
    parser.add_argument("--trace-output", default="data/conversation_ai/rollout/probe_traces.jsonl")
    parser.add_argument("--group-id", default="probe-combined")
    args = parser.parse_args()

    group_id = str(args.group_id)
    os.environ["AKITO_EXPERIMENT_GROUPS"] = json.dumps({group_id: "combined"}, ensure_ascii=False)
    trace_output = Path(args.trace_output)
    os.environ["AKITO_CONVERSATION_TRACE_PATH"] = str(trace_output)
    probes = _load_questions(Path(args.input))
    if not probes:
        raise SystemExit("no probes found")
    import nonebot

    nonebot.init()
    results = asyncio.run(run_probes(probes, group_id=group_id, output=Path(args.output) if args.output else None))
    failed = sum(result["status"] == "failed" for result in results)
    print(json.dumps({"probes": len(results), "failed": failed, "output": args.output, "trace_output": args.trace_output}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

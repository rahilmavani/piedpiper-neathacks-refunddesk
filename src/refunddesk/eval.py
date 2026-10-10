"""Run one eval task: ``python -m refunddesk.eval --task <id> --seed <n>``.

Prints one JSON object: passed, cost, latency, tokens, trace_id (plus details).
Each run is exactly one neatlogs trace whose root WORKFLOW span carries the
harness metadata (CULPRIT_RUN_ID, COMMIT_SHA, TASK_ID, REPETITION).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

# neatlogs must be initialised before provider clients are created, so the
# tracing module is imported (and init_tracing called) before llm/agent run.
from . import tracing


def _trace_id(span: Any) -> str | None:
    try:
        ctx = span.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.trace_id else None
    except Exception:
        return None


def run_task(task_id: str, seed: int, include_tool_log: bool = False) -> dict[str, Any]:
    from .tasks import load_task

    task = load_task(task_id)
    meta = tracing.run_metadata(task.id, seed)
    tracing.init_tracing(meta)

    import neatlogs

    from .agent import RefundDeskAgent
    from .checks import run_checks
    from .config import load_settings
    from .faults import FaultPlan
    from .llm import LLMClient
    from .store import JsonStore
    from .tools import ToolRuntime, activate

    settings = load_settings()
    result: dict[str, Any] = {"task_id": task.id, "seed": seed, "heldout": task.heldout}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"refunddesk-{task.id}-") as tmp:
        store = JsonStore.create(Path(tmp) / "store.json", task.store)
        runtime = ToolRuntime(store=store, faults=FaultPlan.from_task(task.id, seed, task.faults))
        activate(runtime)
        root_attrs = {
            "neatlogs.metadata": json.dumps(meta, sort_keys=True),
            "input.value": json.dumps({"task_id": task.id, "seed": seed, "turns": task.turns, "metadata": meta}),
            "input.mime_type": "application/json",
            **{f"neatlogs.workflow.{k}": v for k, v in meta.items()},
        }
        with neatlogs.trace(
            "refunddesk.run_task",
            kind="WORKFLOW",
            session_id=f"{meta['culprit_run_id']}:{task.id}",
            **root_attrs,
        ) as root:
            result["trace_id"] = _trace_id(root)
            llm = LLMClient(settings)
            agent = RefundDeskAgent(llm=llm, history_window=settings.history_window)
            replies = []
            error = None
            try:
                for turn in task.turns:
                    replies.append(agent.handle_turn(turn))
            except Exception as exc:  # the run fails; the error is reported, not hidden
                error = f"{type(exc).__name__}: {exc}"
            final_state = store.load()
            checks = run_checks(task.checks, final_state, runtime.log)
            passed = error is None and all(c["passed"] for c in checks)
            root.set_attribute(
                "output.value",
                json.dumps({"passed": passed, "replies": replies, "error": error, "checks": checks}),
            )
            root.set_attribute("output.mime_type", "application/json")
        tool_log = runtime.log
    latency = time.perf_counter() - started

    responses = agent.responses
    in_tok = [r.input_tokens for r in responses]
    out_tok = [r.output_tokens for r in responses]
    costs = [r.cost_usd for r in responses]
    tokens = {
        "input": sum(in_tok) if all(t is not None for t in in_tok) else None,
        "output": sum(out_tok) if all(t is not None for t in out_tok) else None,
    }
    tokens["total"] = (
        tokens["input"] + tokens["output"] if None not in (tokens["input"], tokens["output"]) else None
    )
    result.update(
        {
            "passed": passed,
            "cost": round(sum(costs), 6) if costs and all(c is not None for c in costs) else None,
            "latency": round(latency, 3),
            "tokens": tokens,
            "llm_calls": len(responses),
            "llm_requests": llm.requests_sent,
            "tool_executions": sum(1 for e in tool_log if e["outcome"] not in ("blocked", "invalid_arguments")),
            "tool_outcomes": dict(Counter(e["outcome"] for e in tool_log)),
            "turns": len(task.turns),
            "model": settings.model,
            "provider": settings.provider,
            "temperature_sent": all(r.temperature_sent for r in responses) if responses else None,
            "checks": checks,
            "error": error,
            "metadata": meta,
        }
    )
    if include_tool_log:
        result["tool_log"] = tool_log
    tracing.shutdown_tracing()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m refunddesk.eval", description=__doc__)
    parser.add_argument("--task", required=True, help="task id (file name in evals/ or evals/heldout/)")
    parser.add_argument("--seed", type=int, default=0, help="seed for deterministic fault injection")
    parser.add_argument("--tool-log", action="store_true", help="include the tool-call log in the output")
    args = parser.parse_args(argv)
    try:
        result = run_task(args.task, args.seed, include_tool_log=args.tool_log)
    except Exception as exc:
        print(json.dumps({"task_id": args.task, "seed": args.seed, "passed": False, "cost": None,
                          "latency": None, "tokens": None, "trace_id": None,
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

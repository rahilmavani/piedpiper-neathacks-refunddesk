"""Verify persisted neatlogs traces for RefundDesk eval runs.

Usage:
    uv run python -m refunddesk.eval --task refund_small_order --seed 1 > run.json
    uv run python scripts/neatlogs_readback.py run.json [more.jsonl ...]

Input is the JSON printed by the eval runner (one object per line). For each run
the script reads the trace back through the neatlogs CLI (`neatlogs traces get`,
`traces spans list`, `traces raw-spans`, `traces payload`) and checks:

* the trace is finalized and carries the run metadata as tags;
* exactly one root (WORKFLOW ``refunddesk.run_task``), one AGENT span per turn;
* every LLM request and every mock tool execution is a span whose parent is an
  AGENT span under that root, in both the raw and the canonical projections;
* span counts match what the application executed (including failed requests
  and timed-out tool attempts, which must be ERROR spans);
* the root payload contains the same metadata.

Credentials: readback uses the CLI's saved OAuth login, or NEATLOGS_TOKEN when
set. The application's ingest key (NEATLOGS_API_KEY) is removed from the CLI's
environment because the CLI would otherwise treat it as a read credential.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MAX_PAGES = 20
FINALIZE_POLLS = 12
POLL_SECONDS = 5


def _public_cli() -> str:
    """Resolve the Public API CLI (npm `neatlogs-cli`), not the Python SDK's `neatlogs`.

    Both install an executable called `neatlogs`; inside `uv run` the SDK's copy in
    the virtualenv comes first on PATH. NEATLOGS_CLI overrides the lookup.
    """
    if os.environ.get("NEATLOGS_CLI"):
        return os.environ["NEATLOGS_CLI"]
    venv_bin = os.path.realpath(os.path.dirname(sys.executable))
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if os.path.realpath(d) != venv_bin]
    found = shutil.which("neatlogs", path=os.pathsep.join(dirs))
    if not found:
        raise RuntimeError("neatlogs Public CLI not found; install neatlogs-cli or set NEATLOGS_CLI")
    return found


NEATLOGS_CLI = _public_cli()


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("NEATLOGS_API_KEY", None)  # ingest key: never a read credential here
    return env


TRANSIENT = ("REQUEST_TIMEOUT", "RATE_LIMIT", "NETWORK", "UNAVAILABLE", "INTERNAL")
CLI_ATTEMPTS = 5
# "full": also read every LLM/TOOL payload (request vs execution audit; many calls).
# "root": only the root payload (metadata); use for large batches under the rate limit.
PAYLOADS = os.environ.get("READBACK_PAYLOADS", "full")


def cli(*args: str) -> dict[str, Any]:
    """Run one Public CLI read; transient failures are retried with backoff (bounded)."""
    for attempt in range(1, CLI_ATTEMPTS + 1):
        proc = subprocess.run([NEATLOGS_CLI, *args, "--json"], env=_cli_env(), capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        output = proc.stdout + proc.stderr
        if attempt == CLI_ATTEMPTS or not any(code in output for code in TRANSIENT):
            raise RuntimeError(f"neatlogs {' '.join(args)} failed ({proc.returncode}): {output[-300:]}")
        # The public API is rate limited per credential; back off much longer for that.
        time.sleep(15 * attempt if "RATE_LIMIT" in output else 2 ** attempt)
    raise AssertionError("unreachable")


def paged(*args: str) -> tuple[list[dict[str, Any]], bool]:
    """Collect every page; returns (items, complete)."""
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(MAX_PAGES):
        extra = ["--cursor", cursor] if cursor else []
        data = cli(*args, "--limit", "50", *extra)["data"]
        items += data["spans"]
        cursor = data["page"]["nextCursor"]
        if not cursor:
            return items, True
    return items, False


def payload_field(trace_id: str, span_id: str, field: str) -> dict[str, Any]:
    return cli("traces", "payload", trace_id, span_id, "--fields", field)["data"]["fields"][field]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def tool_requests(llm_input: str) -> set[tuple[str, str]]:
    """Extract (tool name, arguments) of tool_use blocks replayed in an LLM input."""
    found: set[tuple[str, str]] = set()
    decoder = json.JSONDecoder()
    idx = llm_input.find("assistant: [")
    while idx != -1:
        try:
            blocks, _ = decoder.raw_decode(llm_input, idx + len("assistant: "))
        except ValueError:
            blocks = []
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                found.add((block["name"], canonical(block.get("input", {}))))
        idx = llm_input.find("assistant: [", idx + 1)
    return found


def wait_finalized(trace_id: str) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(FINALIZE_POLLS):
        try:
            last = cli("traces", "get", trace_id)["data"]
        except RuntimeError as exc:  # not ingested yet
            last = {"error": str(exc)}
        if last.get("finalizationStatus") in ("finalized", "dlq"):
            return last
        time.sleep(POLL_SECONDS)
    return last


def verify(run: dict[str, Any]) -> dict[str, Any]:
    trace_id = run["trace_id"]
    meta = run["metadata"]
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    trace = wait_finalized(trace_id)
    check("finalized", trace.get("finalizationStatus") == "finalized", str(trace.get("finalizationStatus")))
    if trace.get("finalizationStatus") != "finalized":
        return {"trace_id": trace_id, "task_id": run.get("task_id"), "passed": False,
                "checks": [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks]}
    check("workflow_name", trace["workflowName"] == "refunddesk-eval", trace["workflowName"])
    check("trace_name", trace["traceName"] == "refunddesk.run_task", str(trace["traceName"]))
    tags = set(trace.get("tags") or [])
    for key in ("culprit_run_id", "commit_sha", "task_id", "repetition"):
        check(f"tag:{key}", f"{key}:{meta[key]}" in tags, f"{key}:{meta[key]}")

    raw, raw_complete = paged("traces", "raw-spans", trace_id)
    canon, canon_complete = paged("traces", "spans", "list", trace_id)
    check("pagination_complete", raw_complete and canon_complete, f"raw={len(raw)} canonical={len(canon)}")
    check("span_count_matches_summary", len(canon) == trace["spansCount"], f"{len(canon)} vs {trace['spansCount']}")

    for label, spans in (("raw", raw), ("canonical", canon)):
        by_id = {s["spanId"]: s for s in spans}
        roots = [s for s in spans if not s["parentSpanId"]]
        check(f"{label}:single_root", len(roots) == 1 and roots[0]["spanType"] == "WORKFLOW",
              f"{[(r['spanType'], r['spanName']) for r in roots]}")
        root_id = roots[0]["spanId"] if roots else None
        agents = [s for s in spans if s["spanType"] == "AGENT"]
        check(f"{label}:agent_per_turn", len(agents) == run["turns"]
              and all(a["parentSpanId"] == root_id for a in agents), f"{len(agents)} AGENT spans, {run['turns']} turns")
        agent_ids = {a["spanId"] for a in agents}
        llm = [s for s in spans if s["spanType"] == "LLM"]
        tool = [s for s in spans if s["spanType"] == "TOOL"]
        check(f"{label}:llm_spans_parented", all(s["parentSpanId"] in agent_ids for s in llm),
              f"{len(llm)} LLM spans")
        check(f"{label}:tool_spans_parented", all(s["parentSpanId"] in agent_ids for s in tool),
              f"{len(tool)} TOOL spans")
        check(f"{label}:no_dangling_parents",
              all(s["parentSpanId"] in by_id for s in spans if s["parentSpanId"]), "")
        check(f"{label}:llm_count", len(llm) == run["llm_requests"], f"{len(llm)} spans vs {run['llm_requests']} requests")
        check(f"{label}:tool_count", len(tool) == run["tool_executions"],
              f"{len(tool)} spans vs {run['tool_executions']} executions")
        llm_errors = sum(1 for s in llm if s["status"] == "ERROR")
        check(f"{label}:llm_error_spans", llm_errors == run["llm_requests"] - run["llm_calls"],
              f"{llm_errors} ERROR LLM spans, {run['llm_requests'] - run['llm_calls']} rejected requests")
        timeouts = run.get("tool_outcomes", {}).get("timeout", 0)
        tool_errors = sum(1 for s in tool if s["status"] == "ERROR")
        check(f"{label}:tool_timeouts_are_errors", tool_errors == timeouts,
              f"{tool_errors} ERROR TOOL spans, {timeouts} injected timeouts")

    ok_llm = [s for s in canon if s["spanType"] == "LLM" and s["status"] != "ERROR"]
    check("llm_model_provider", all(s.get("model") == run["model"] and s.get("provider") == run["provider"] for s in ok_llm),
          f"{sorted({(s.get('model'), s.get('provider')) for s in ok_llm})}")
    check("llm_tokens_reported", all(s["promptTokens"] > 0 and s["completionTokens"] > 0 for s in ok_llm), "")

    root = next(s for s in raw if not s["parentSpanId"])
    payload = cli("traces", "payload", trace_id, root["spanId"], "--fields", "input,output")["data"]["fields"]
    root_in = payload["input"]
    try:
        stored_meta = json.loads(root_in["value"])["metadata"]
    except (TypeError, ValueError, KeyError):
        stored_meta = None
    check("root_payload_metadata",
          stored_meta is not None and all(stored_meta.get(k) == meta[k] for k in ("culprit_run_id", "commit_sha", "task_id", "repetition")),
          f"mime={root_in.get('mimeType')} truncated={root_in.get('truncated')}")
    try:
        stored_passed = json.loads(payload["output"]["value"])["passed"]
    except (TypeError, ValueError, KeyError):
        stored_passed = None
    check("root_payload_result", stored_passed == run["passed"], f"stored passed={stored_passed}")

    if PAYLOADS != "full":
        return _report(trace_id, run, meta, raw, checks)

    # Model tool requests vs actual executions. The public payload of an LLM span
    # whose response is only tool_use blocks has no output text, so the request is
    # read from the next LLM span's input, which replays the assistant tool_use turn.
    requested: set[tuple[str, str]] = set()
    llm_inputs_ok = True
    for span in (s for s in raw if s["spanType"] == "LLM" and s["status"] != "ERROR"):
        field = payload_field(trace_id, span["spanId"], "input")
        llm_inputs_ok &= field["available"] and not field["truncated"]
        requested |= tool_requests(field["value"] or "")
    check("llm_inputs_available", llm_inputs_ok, "")
    executed: list[tuple[str, str]] = []
    for span in (s for s in raw if s["spanType"] == "TOOL"):
        field = payload_field(trace_id, span["spanId"], "input")
        executed.append((span["spanName"], canonical(json.loads(field["value"] or "{}"))))
    unmatched = [e for e in executed if e not in requested]
    check("tool_executions_match_model_requests", not unmatched,
          f"{len(executed)} executions, {len(requested)} distinct requests, unmatched={unmatched}")
    finals_ok = True
    for agent in (s for s in raw if s["spanType"] == "AGENT"):
        children = [s for s in raw if s["parentSpanId"] == agent["spanId"] and s["spanType"] == "LLM"]
        last = max(children, key=lambda s: s["startedAt"], default=None)
        finals_ok &= bool(last) and payload_field(trace_id, last["spanId"], "output")["available"]
    check("final_reply_output_available", finals_ok, "")
    return _report(trace_id, run, meta, raw, checks)


def _report(trace_id, run, meta, raw, checks) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "task_id": run.get("task_id"),
        "repetition": meta.get("repetition"),
        "passed": all(ok for _, ok, _ in checks),
        "spans": len(raw),
        "checks": [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks],
    }


def _runs(paths: list[str]) -> list[dict[str, Any]]:
    lines: list[str] = []
    for p in paths or ["-"]:
        text = sys.stdin.read() if p == "-" else Path(p).read_text()
        lines += [ln for ln in text.splitlines() if ln.strip().startswith("{")]
    return [json.loads(ln) for ln in lines]


def verify_safely(run: dict[str, Any]) -> dict[str, Any]:
    try:
        return verify(run)
    except Exception as exc:  # report the failing layer instead of aborting the batch
        return {"trace_id": run.get("trace_id"), "task_id": run.get("task_id"), "passed": False,
                "checks": [{"check": "readback_error", "passed": False, "detail": str(exc)[:500]}]}


def main(argv: list[str]) -> int:
    from concurrent.futures import ThreadPoolExecutor

    runs = [run for run in _runs(argv) if run.get("trace_id")]
    with ThreadPoolExecutor(max_workers=int(os.environ.get("READBACK_WORKERS", "4"))) as pool:
        reports = list(pool.map(verify_safely, runs))
    for rep in reports:
        failed = [c for c in rep["checks"] if not c["passed"]]
        status = "PASS" if rep["passed"] else "FAIL"
        print(f"{status} {rep['trace_id']} task={rep['task_id']} rep={rep.get('repetition')} spans={rep.get('spans')}"
              + (f" failed={[(c['check'], c['detail']) for c in failed]}" if failed else ""))
    out = os.environ.get("READBACK_REPORT")
    if out:
        Path(out).write_text(json.dumps(reports, indent=2))
    print(f"{sum(r['passed'] for r in reports)}/{len(reports)} traces verified")
    return 0 if reports and all(r["passed"] for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

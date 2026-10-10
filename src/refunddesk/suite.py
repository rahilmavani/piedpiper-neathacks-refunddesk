"""Run the eval suite: ``python -m refunddesk.suite --reps 3``.

Each (task, repetition) runs ``python -m refunddesk.eval`` in its own process,
exactly as a test harness would, with CULPRIT_RUN_ID / COMMIT_SHA / TASK_ID /
REPETITION set in the environment. Results go to a JSONL file and a summary is
printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import PROJECT_ROOT
from .tasks import list_tasks


def _git_head() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def _run_one(task_id: str, rep: int, seed: int, run_id: str, commit: str) -> dict:
    env = dict(os.environ, CULPRIT_RUN_ID=run_id, COMMIT_SHA=commit, TASK_ID=task_id, REPETITION=str(rep))
    proc = subprocess.run(
        [sys.executable, "-m", "refunddesk.eval", "--task", task_id, "--seed", str(seed)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        result = {"task_id": task_id, "passed": False, "error": f"no JSON output; stderr: {proc.stderr[-500:]}"}
    result["repetition"] = rep
    return result


def _print_summary(results: list[dict], reps: int, run_id: str, commit: str, out: Path) -> list[dict]:
    print(f"run_id={run_id} commit={commit} results={out}")
    for rep in range(1, reps + 1):
        rows = [r for r in results if r["repetition"] == rep]
        passed = sum(1 for r in rows if r.get("passed"))
        print(f"repetition {rep}: {passed}/{len(rows)} passed")
    failures = [r for r in results if not r.get("passed")]
    for r in failures:
        failed_checks = [c["detail"] for c in r.get("checks", []) if not c["passed"]]
        print(f"FAIL {r['task_id']} rep={r['repetition']} trace={r.get('trace_id')} {failed_checks or r.get('error')}")
    total_cost = sum(r.get("cost") or 0 for r in results)
    print(f"total: {len(results) - len(failures)}/{len(results)} passed, cost ${total_cost:.4f}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m refunddesk.suite", description=__doc__)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-heldout", action="store_true")
    parser.add_argument("--task", action="append", help="limit to these task ids (repeatable)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    tasks = [t for t in list_tasks(include_heldout=not args.no_heldout) if not args.task or t.id in args.task]
    run_id = args.run_id or f"suite-{time.strftime('%Y%m%dT%H%M%S')}"
    commit = os.environ.get("COMMIT_SHA") or _git_head()
    out = args.out or PROJECT_ROOT / ".cache" / "refunddesk" / "runs" / f"{run_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    jobs = [(t.id, rep) for rep in range(1, args.reps + 1) for t in tasks]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda j: _run_one(j[0], j[1], args.seed, run_id, commit), jobs))
    with out.open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    failures = _print_summary(results, args.reps, run_id, commit, out)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

"""neatlogs setup: one SDK init per process and the run metadata attached to traces."""

from __future__ import annotations

import os
import subprocess

import neatlogs

from .config import PROJECT_ROOT

WORKFLOW_NAME = "refunddesk-eval"
METADATA_ENV_VARS = ("CULPRIT_RUN_ID", "COMMIT_SHA", "TASK_ID", "REPETITION")

_initialized = False


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def run_metadata(task_id: str, seed: int) -> dict[str, str]:
    """Harness-provided metadata; local fallbacks are marked so they are never mistaken for it."""
    meta = {
        "culprit_run_id": os.environ.get("CULPRIT_RUN_ID") or "local",
        "commit_sha": os.environ.get("COMMIT_SHA") or _git_head() or "unknown",
        "task_id": os.environ.get("TASK_ID") or task_id,
        "repetition": os.environ.get("REPETITION") or "0",
        "seed": str(seed),
    }
    meta["metadata_source"] = ",".join(
        f"{name.lower()}={'env' if os.environ.get(name) else 'fallback'}" for name in METADATA_ENV_VARS
    )
    return meta


def metadata_tags(meta: dict[str, str]) -> list[str]:
    return [f"{key}:{meta[key]}" for key in ("culprit_run_id", "commit_sha", "task_id", "repetition", "seed")]


def init_tracing(meta: dict[str, str]) -> None:
    """Initialise neatlogs once; must run before the provider clients are created."""
    global _initialized
    if _initialized:
        return
    neatlogs.init(workflow_name=WORKFLOW_NAME, tags=metadata_tags(meta), log_level="WARNING")
    _initialized = True


def shutdown_tracing() -> None:
    if _initialized:
        neatlogs.flush()
        neatlogs.shutdown()

"""Loading eval task definitions from ``evals/`` and ``evals/heldout/``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import CHECKS
from .config import PROJECT_ROOT

EVALS_DIR = PROJECT_ROOT / "evals"
HELDOUT_DIR = EVALS_DIR / "heldout"


@dataclass(frozen=True)
class Task:
    id: str
    description: str
    turns: list[str]
    store: dict[str, Any]
    faults: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    heldout: bool
    path: Path


def _from_file(path: Path, heldout: bool) -> Task:
    raw = json.loads(path.read_text())
    task = Task(
        id=raw["id"],
        description=raw["description"],
        turns=list(raw["turns"]),
        store=raw["store"],
        faults=list(raw.get("faults", [])),
        checks=list(raw["checks"]),
        heldout=heldout,
        path=path,
    )
    if task.id != path.stem:
        raise ValueError(f"{path}: id {task.id!r} must match the file name")
    if not task.turns or not task.checks:
        raise ValueError(f"{path}: a task needs at least one turn and one check")
    unknown = [c["check"] for c in task.checks if c["check"] not in CHECKS]
    if unknown:
        raise ValueError(f"{path}: unknown checks {unknown}")
    return task


def list_tasks(include_heldout: bool = True) -> list[Task]:
    """Return every task, sorted by file name, regular tasks before held-out ones."""
    tasks = [_from_file(p, False) for p in sorted(EVALS_DIR.glob("*.json"))]
    if include_heldout:
        tasks += [_from_file(p, True) for p in sorted(HELDOUT_DIR.glob("*.json"))]
    return tasks


def load_task(task_id: str) -> Task:
    """Load one task by id, looking in ``evals/`` first and then ``evals/heldout/``."""
    for directory, heldout in ((EVALS_DIR, False), (HELDOUT_DIR, True)):
        path = directory / f"{task_id}.json"
        if path.exists():
            return _from_file(path, heldout)
    raise FileNotFoundError(f"no task named {task_id!r} in {EVALS_DIR} or {HELDOUT_DIR}")

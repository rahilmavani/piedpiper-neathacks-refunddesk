"""Deterministic failure injection for the mock tools.

A task lists fault rules. Each rule targets one tool and injects either a
``timeout`` (the call raises :class:`ToolTimeout` before it has any effect) or a
``failed`` payload (``{"status": "failed", ...}``, also with no effect).

Rules select calls in one of three ways:

* ``"calls": [1, 3]`` - fail exactly these 1-based calls of the tool;
* ``"always": true``  - fail every call;
* ``"rate": 0.5, "max": 2`` - fail each call with probability ``rate``, at most
  ``max`` times. The draw is a hash of (task id, seed, tool, call number), so the
  same task and seed always fail the same calls.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

FAULT_KINDS = ("timeout", "failed")


class ToolTimeout(Exception):
    """Raised by a mock tool when an injected timeout fires."""


@dataclass
class FaultRule:
    tool: str
    kind: str
    calls: tuple[int, ...] = ()
    always: bool = False
    rate: float = 0.0
    max: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FaultRule":
        kind = raw["kind"]
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}")
        return cls(
            tool=raw["tool"],
            kind=kind,
            calls=tuple(raw.get("calls", ())),
            always=bool(raw.get("always", False)),
            rate=float(raw.get("rate", 0.0)),
            max=raw.get("max"),
        )


def _seeded_draw(task_id: str, seed: int, tool: str, call_no: int) -> float:
    digest = hashlib.sha256(f"{task_id}:{seed}:{tool}:{call_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


@dataclass
class FaultPlan:
    task_id: str
    seed: int
    rules: list[FaultRule] = field(default_factory=list)
    _calls: dict[str, int] = field(default_factory=dict)
    _fired: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_task(cls, task_id: str, seed: int, raw_rules: list[dict[str, Any]]) -> "FaultPlan":
        return cls(task_id=task_id, seed=seed, rules=[FaultRule.from_dict(r) for r in raw_rules])

    def next_fault(self, tool: str) -> str | None:
        """Count one call of ``tool`` and return the fault kind to inject, if any."""
        call_no = self._calls.get(tool, 0) + 1
        self._calls[tool] = call_no
        for idx, rule in enumerate(self.rules):
            if rule.tool != tool:
                continue
            fired = self._fired.get(idx, 0)
            if rule.max is not None and fired >= rule.max:
                continue
            hit = (
                rule.always
                or call_no in rule.calls
                or (rule.rate > 0 and _seeded_draw(self.task_id, self.seed, tool, call_no) < rule.rate)
            )
            if hit:
                self._fired[idx] = fired + 1
                return rule.kind
        return None

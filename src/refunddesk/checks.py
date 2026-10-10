"""Deterministic checkers that grade a run from the final store and the tool-call log.

A task lists checks as ``{"check": <name>, **params}``; ``run_checks`` calls the
function of that name with the final state, the tool-call log and the params.
"""

from __future__ import annotations

from typing import Any, Callable

State = dict[str, Any]
Log = list[dict[str, Any]]
CheckResult = tuple[bool, str]


def _refunds(state: State, order_id: str | None = None) -> list[dict[str, Any]]:
    return [r for r in state["refunds"] if order_id is None or r["order_id"] == order_id]


def refund_count(state: State, log: Log, order_id: str, count: int) -> CheckResult:
    n = len(_refunds(state, order_id))
    return n == count, f"{n} refund(s) for {order_id}, expected {count}"


def refund_amount(state: State, log: Log, order_id: str, amount: float) -> CheckResult:
    total = round(sum(r["amount"] for r in _refunds(state, order_id)), 2)
    return abs(total - amount) < 0.005, f"refunded {total:.2f} for {order_id}, expected {amount:.2f}"


def no_refund(state: State, log: Log, order_id: str | None = None) -> CheckResult:
    n = len(_refunds(state, order_id))
    target = order_id or "any order"
    return n == 0, f"{n} refund(s) for {target}, expected none"


def no_refund_over(state: State, log: Log, limit: float) -> CheckResult:
    big = [r for r in state["refunds"] if r["amount"] > limit]
    return not big, f"{len(big)} refund(s) over {limit:.2f}"


def escalated(state: State, log: Log, order_id: str | None = None) -> CheckResult:
    hits = [e for e in state["escalations"] if order_id is None or e["order_id"] == order_id]
    return bool(hits), f"{len(hits)} escalation(s){' for ' + order_id if order_id else ''}"


def not_escalated(state: State, log: Log) -> CheckResult:
    n = len(state["escalations"])
    return n == 0, f"{n} escalation(s), expected none"


def email_sent(state: State, log: Log, to: str | None = None) -> CheckResult:
    hits = [m for m in state["emails"] if to is None or m["to"].lower() == to.lower()]
    return bool(hits), f"{len(hits)} email(s){' to ' + to if to else ''}"


def tool_called(state: State, log: Log, tool: str, min: int = 1, max: int | None = None) -> CheckResult:
    n = sum(1 for entry in log if entry["tool"] == tool and entry["outcome"] not in ("blocked", "invalid_arguments"))
    ok = n >= min and (max is None or n <= max)
    return ok, f"{tool} executed {n} time(s), expected [{min}, {max if max is not None else 'inf'}]"


def tool_not_called(state: State, log: Log, tool: str) -> CheckResult:
    return tool_called(state, log, tool, min=0, max=0)


def refunds_follow_successful_lookup(state: State, log: Log) -> CheckResult:
    """Every executed refund attempt comes after an ok lookup of the same order."""
    looked_up: set[str] = set()
    for entry in log:
        if entry["tool"] == "lookup_order" and entry["outcome"] == "ok":
            looked_up.add(entry["args"]["order_id"])
        if entry["tool"] == "issue_refund" and entry["outcome"] not in ("blocked", "invalid_arguments"):
            if entry["args"]["order_id"] not in looked_up:
                return False, f"refund attempt for {entry['args']['order_id']} without a successful lookup"
    return True, "all refund attempts follow a successful lookup"


def injected_fault_seen(state: State, log: Log, tool: str, kind: str) -> CheckResult:
    """Confirms the scenario actually exercised the injected failure."""
    n = sum(1 for e in log if e["tool"] == tool and e["outcome"] == kind)
    return n >= 1, f"{n} injected {kind} on {tool}"


CHECKS: dict[str, Callable[..., CheckResult]] = {
    fn.__name__: fn
    for fn in (
        refund_count,
        refund_amount,
        no_refund,
        no_refund_over,
        escalated,
        not_escalated,
        email_sent,
        tool_called,
        tool_not_called,
        refunds_follow_successful_lookup,
        injected_fault_seen,
    )
}


def run_checks(specs: list[dict[str, Any]], state: State, log: Log) -> list[dict[str, Any]]:
    results = []
    for spec in specs:
        params = {k: v for k, v in spec.items() if k != "check"}
        ok, detail = CHECKS[spec["check"]](state, log, **params)
        results.append({"check": spec["check"], "params": params, "passed": ok, "detail": detail})
    return results

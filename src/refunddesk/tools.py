"""Mock support tools backed by the local JSON store.

Each tool execution is one neatlogs TOOL span. Injected faults happen before the
tool touches the store, so a timed-out or failed call never has side effects.
"""

from __future__ import annotations

import contextvars
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

import neatlogs

from .faults import FaultPlan, ToolTimeout
from .store import JsonStore

REFUND_POLICY: dict[str, Any] = {
    "max_agent_refund_usd": 500.00,
    "over_limit_action": "escalate_to_human",
    "refund_window_days": 30,
    "partial_refunds": "allowed up to the unrefunded order amount",
    "requires_successful_order_lookup": True,
    "duplicate_refunds": "never refund the same order twice",
}


@dataclass
class ToolRuntime:
    """Per-run state shared by the tools: store, fault plan and call log.

    Every attempt is logged, including injected timeouts and failures, so the
    eval checks can see retries as well as outcomes.
    """

    store: JsonStore
    faults: FaultPlan
    log: list[dict[str, Any]] = field(default_factory=list)
    _seq: Any = field(default_factory=lambda: itertools.count(1))
    _ids: Any = field(default_factory=lambda: itertools.count(1))

    def record(self, tool: str, args: dict[str, Any], outcome: str, result: Any = None) -> None:
        self.log.append(
            {"seq": next(self._seq), "tool": tool, "args": args, "outcome": outcome, "result": result}
        )

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):04d}"


_runtime: contextvars.ContextVar[ToolRuntime] = contextvars.ContextVar("refunddesk_tool_runtime")


def activate(runtime: ToolRuntime) -> contextvars.Token:
    return _runtime.set(runtime)


def current_runtime() -> ToolRuntime:
    return _runtime.get()


def _inject(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    rt = current_runtime()
    fault = rt.faults.next_fault(tool)
    if fault == "timeout":
        rt.record(tool, args, "timeout")
        raise ToolTimeout(f"{tool} timed out")
    if fault == "failed":
        result = {"status": "failed", "error": f"{tool}: upstream service error"}
        rt.record(tool, args, "failed", result)
        return result
    return None


def _finish(tool: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    current_runtime().record(tool, args, result.get("status", "unknown"), result)
    return result


@neatlogs.span(kind="TOOL", tool_name="lookup_order")
def lookup_order(order_id: str) -> dict[str, Any]:
    args = {"order_id": order_id}
    if (failure := _inject("lookup_order", args)) is not None:
        return failure
    order = current_runtime().store.load()["orders"].get(order_id)
    if order is None:
        return _finish("lookup_order", args, {"status": "not_found", "order_id": order_id})
    return _finish("lookup_order", args, {"status": "ok", "order": order})


@neatlogs.span(kind="TOOL", tool_name="get_refund_policy")
def get_refund_policy() -> dict[str, Any]:
    if (failure := _inject("get_refund_policy", {})) is not None:
        return failure
    return _finish("get_refund_policy", {}, {"status": "ok", "policy": dict(REFUND_POLICY)})


@neatlogs.span(kind="TOOL", tool_name="issue_refund")
def issue_refund(order_id: str, amount: float, reason: str) -> dict[str, Any]:
    args = {"order_id": order_id, "amount": amount, "reason": reason}
    if (failure := _inject("issue_refund", args)) is not None:
        return failure
    rt = current_runtime()
    state = rt.store.load()
    order = state["orders"].get(order_id)
    if order is None:
        return _finish("issue_refund", args, {"status": "rejected", "error": "unknown order"})
    remaining = round(order["amount"] - order.get("refunded_amount", 0.0), 2)
    if amount <= 0 or amount > remaining:
        return _finish(
            "issue_refund",
            args,
            {"status": "rejected", "error": f"amount must be > 0 and <= {remaining:.2f}"},
        )
    refund = {"refund_id": rt.new_id("RF"), "order_id": order_id, "amount": round(amount, 2), "reason": reason}
    state["refunds"].append(refund)
    order["refunded_amount"] = round(order.get("refunded_amount", 0.0) + amount, 2)
    order["status"] = "refunded" if order["refunded_amount"] >= order["amount"] else order["status"]
    rt.store.save(state)
    return _finish("issue_refund", args, {"status": "ok", "refund": refund})


@neatlogs.span(kind="TOOL", tool_name="escalate_to_human")
def escalate_to_human(order_id: str, reason: str) -> dict[str, Any]:
    args = {"order_id": order_id, "reason": reason}
    if (failure := _inject("escalate_to_human", args)) is not None:
        return failure
    rt = current_runtime()
    state = rt.store.load()
    ticket = {"ticket_id": rt.new_id("ESC"), "order_id": order_id, "reason": reason}
    state["escalations"].append(ticket)
    rt.store.save(state)
    return _finish("escalate_to_human", args, {"status": "ok", "ticket": ticket})


@neatlogs.span(kind="TOOL", tool_name="send_customer_email")
def send_customer_email(to: str, subject: str, body: str) -> dict[str, Any]:
    args = {"to": to, "subject": subject, "body": body}
    if (failure := _inject("send_customer_email", args)) is not None:
        return failure
    rt = current_runtime()
    state = rt.store.load()
    email = {"message_id": rt.new_id("MSG"), "to": to, "subject": subject, "body": body}
    state["emails"].append(email)
    rt.store.save(state)
    return _finish("send_customer_email", args, {"status": "ok", "message_id": email["message_id"]})


TOOL_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "lookup_order": lookup_order,
    "get_refund_policy": get_refund_policy,
    "issue_refund": issue_refund,
    "escalate_to_human": escalate_to_human,
    "send_customer_email": send_customer_email,
}


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up an order by its id. Returns status ok with the order, not_found, or failed.",
        "parameters": _schema({"order_id": {"type": "string", "description": "Order id, e.g. A1001"}}),
    },
    {
        "name": "get_refund_policy",
        "description": "Return the current refund policy.",
        "parameters": _schema({}),
    },
    {
        "name": "issue_refund",
        "description": "Refund an amount in USD for an order that was successfully looked up.",
        "parameters": _schema(
            {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "description": "Refund amount in USD"},
                "reason": {"type": "string"},
            }
        ),
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the case to a human agent. Use order_id 'unknown' when there is no valid order.",
        "parameters": _schema({"order_id": {"type": "string"}, "reason": {"type": "string"}}),
    },
    {
        "name": "send_customer_email",
        "description": "Send an email to the customer.",
        "parameters": _schema(
            {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}
        ),
    },
]

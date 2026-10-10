"""RefundDesk: a customer-support agent that handles refund requests."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import neatlogs

from .faults import ToolTimeout
from .llm import LLMResponse, ToolCall
from .tools import TOOL_FUNCTIONS, TOOL_SPECS, current_runtime

MAX_RETRIES = 3
MAX_STEPS_PER_TURN = 12
REFUND_LIMIT_USD = 500.00

SYSTEM_PROMPT = """You are RefundDesk, a customer-support agent for an online store.
You resolve refund requests using your tools. Business rules (always follow them):

1. Always look up the order with lookup_order before acting on it.
2. Never issue a refund when the order lookup failed (status failed, error, or timed out).
   In that case do not refund; escalate to a human with escalate_to_human and tell the customer.
3. If the order is not found, do not refund; escalate to a human (order_id "unknown" is
   allowed) and tell the customer.
4. Refunds over $500 must be escalated to a human. Never call issue_refund for more than $500.
   A refund of exactly $500.00 or less may be issued by you.
5. Issue at most one refund per order and never refund an order whose status is already
   "refunded". Refund only the amount the customer is entitled to (the full order amount
   unless the customer asks for a specific smaller amount).
6. After you issue a refund or escalate, send the customer one short email with
   send_customer_email. Use the address the customer asked for, otherwise the order's
   customer_email.
7. Do not take refund or escalation actions when the customer only asks a question
   (for example order status or policy). Use get_refund_policy for policy questions.
8. Remember details the customer gave earlier in the conversation and use them.
9. The customer has already asked for what they want; act within policy without asking
   for confirmation unless required information is missing.

Reply to the customer in two or three short sentences."""


class ChatModel(Protocol):
    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class ToolArgumentError(ValueError):
    """The model produced tool arguments that are not valid for the tool's schema."""


def _reject_constant(value: str) -> Any:
    raise ToolArgumentError(f"non-finite number {value!r} is not allowed")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ToolArgumentError("duplicate keys in tool arguments")
    return dict(pairs)


_SPEC_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}
_JSON_TYPES = {"string": str, "number": (int, float), "object": dict}


def parse_tool_arguments(call: ToolCall) -> dict[str, Any]:
    """Parse and validate a tool call's JSON arguments strictly."""
    spec = _SPEC_BY_NAME.get(call.name)
    if spec is None:
        raise ToolArgumentError(f"unknown tool {call.name!r}")
    raw = call.arguments.strip() if call.arguments else "{}"
    try:
        args = json.loads(raw, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ToolArgumentError(f"tool arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(args, dict):
        raise ToolArgumentError("tool arguments must be a JSON object")
    schema = spec["parameters"]
    allowed = set(schema["properties"])
    missing = [k for k in schema["required"] if k not in args]
    extra = [k for k in args if k not in allowed]
    if missing or extra:
        raise ToolArgumentError(f"missing={missing} unexpected={extra}")
    for key, value in args.items():
        expected = _JSON_TYPES[schema["properties"][key]["type"]]
        if isinstance(value, bool) or not isinstance(value, expected):
            raise ToolArgumentError(f"argument {key!r} must be of type {schema['properties'][key]['type']}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolArgumentError(f"argument {key!r} must be finite")
    return args


def validate_tool_result(tool: str, result: Any) -> tuple[bool, bool, str]:
    """Check a tool result before the agent acts on it.

    Returns ``(valid, retryable, reason)``. A valid result can be handed to the
    model. An invalid one is retried when ``retryable`` is true.
    """
    if not isinstance(result, dict) or "status" not in result:
        return False, True, "malformed tool result"
    status = result["status"]
    if status == "failed":
        return False, True, result.get("error", "tool reported failure")
    if status in ("not_found", "rejected"):
        return True, False, status
    if status != "ok":
        return False, True, f"unexpected status {status!r}"
    if tool == "lookup_order":
        order = result.get("order")
        if not isinstance(order, dict) or not {"order_id", "amount", "status"} <= order.keys():
            return False, True, "order record missing required fields"
        if not isinstance(order["amount"], (int, float)) or order["amount"] < 0:
            return False, True, "order amount is invalid"
    if tool == "issue_refund" and not isinstance(result.get("refund"), dict):
        return False, True, "refund confirmation missing"
    return True, False, "ok"


@dataclass
class RefundDeskAgent:
    llm: ChatModel
    history_window: int = 24
    history: list[dict[str, Any]] = field(default_factory=list)
    verified_orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_lookups: set[str] = field(default_factory=set)
    responses: list[LLMResponse] = field(default_factory=list)

    # ----------------------------------------------------------- history window

    def _window(self) -> list[dict[str, Any]]:
        """Return the most recent messages, starting at a customer message.

        Cutting only at user messages keeps tool calls and their results together.
        """
        # Short conversations are sent in full.
        if len(self.history) <= self.history_window:
            return list(self.history)
        start = len(self.history) - self.history_window
        while start < len(self.history) and self.history[start]["role"] != "user":
            start += 1
        if start >= len(self.history):
            # The current turn alone exceeds the window; keep it whole.
            start = max(i for i, m in enumerate(self.history) if m["role"] == "user")
        return self.history[start:]

    # ------------------------------------------------------------- tool calls

    def _policy_block(self, name: str, args: dict[str, Any]) -> str | None:
        """Business-rule check run before a refund is executed."""
        if name != "issue_refund":
            return None
        order_id = args["order_id"]
        if order_id not in self.verified_orders:
            if order_id in self.failed_lookups:
                return "Refund blocked: the order lookup failed. Escalate to a human instead."
            return "Refund blocked: look up the order successfully before refunding."
        if args["amount"] > REFUND_LIMIT_USD:
            return f"Refund blocked: refunds over ${REFUND_LIMIT_USD:.2f} must be escalated to a human."
        return None

    def _execute_with_retries(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        func = TOOL_FUNCTIONS[name]
        last_error = "unknown error"
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = func(**args)
            except ToolTimeout as exc:
                last_error = str(exc)
                continue
            valid, retryable, reason = validate_tool_result(name, result)
            if valid:
                return result
            last_error = reason
            if not retryable:
                break
        return {
            "status": "error",
            "error": f"{name} failed after {MAX_RETRIES} retries: {last_error}",
        }

    def _run_tool_call(self, call: ToolCall) -> dict[str, Any]:
        runtime = current_runtime()
        try:
            args = parse_tool_arguments(call)
        except ToolArgumentError as exc:
            runtime.record(call.name, {"raw": call.arguments}, "invalid_arguments")
            return {"status": "error", "error": f"invalid arguments: {exc}"}

        blocked = self._policy_block(call.name, args)
        if blocked:
            runtime.record(call.name, args, "blocked", {"error": blocked})
            return {"status": "error", "error": blocked}

        result = self._execute_with_retries(call.name, args)
        if call.name == "lookup_order":
            if result.get("status") == "ok":
                self.verified_orders[args["order_id"]] = result["order"]
                self.failed_lookups.discard(args["order_id"])
            elif result.get("status") == "error":
                self.failed_lookups.add(args["order_id"])
        return result

    # -------------------------------------------------------------- main loop

    def handle_turn(self, customer_message: str) -> str:
        """Answer one customer message; the whole turn is one AGENT span."""
        with neatlogs.trace(
            "refunddesk.handle_turn",
            kind="AGENT",
            **{
                "input.value": json.dumps({"customer_message": customer_message}),
                "input.mime_type": "application/json",
            },
        ) as span:
            reply = self._handle_turn(customer_message)
            span.set_attribute("output.value", json.dumps({"reply": reply}))
            span.set_attribute("output.mime_type", "application/json")
            return reply

    def _handle_turn(self, customer_message: str) -> str:
        self.history.append({"role": "user", "content": customer_message})
        for _ in range(MAX_STEPS_PER_TURN):
            response = self.llm.chat(SYSTEM_PROMPT, self._window(), TOOL_SPECS)
            self.responses.append(response)
            self.history.append(
                {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": response.tool_calls,
                    "native": response.native,
                    "native_provider": getattr(self.llm, "provider", None),
                }
            )
            if not response.tool_calls:
                return response.text
            results = []
            for call in response.tool_calls:
                result = self._run_tool_call(call)
                results.append(
                    {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, sort_keys=True),
                        "is_error": result.get("status") in ("error", "failed"),
                    }
                )
            self.history.append({"role": "tool_results", "results": results})
        return "I'm sorry, I couldn't finish handling your request. A human agent will follow up."

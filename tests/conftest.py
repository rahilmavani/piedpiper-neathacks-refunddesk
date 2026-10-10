import itertools
from pathlib import Path

import pytest

from refunddesk.faults import FaultPlan
from refunddesk.llm import LLMResponse, ToolCall
from refunddesk.store import JsonStore
from refunddesk.tools import ToolRuntime, activate

ORDERS = {
    "A1": {"order_id": "A1", "customer_name": "T", "customer_email": "t@example.com",
           "item": "Mug", "amount": 20.0, "status": "delivered", "order_date": "2026-10-01"},
    "BIG": {"order_id": "BIG", "customer_name": "B", "customer_email": "b@example.com",
            "item": "TV", "amount": 900.0, "status": "delivered", "order_date": "2026-10-01"},
}


@pytest.fixture
def make_runtime(tmp_path: Path):
    def _make(faults=(), orders=None, seed=0):
        store = JsonStore.create(tmp_path / "store.json", {"orders": orders or ORDERS})
        rt = ToolRuntime(store=store, faults=FaultPlan.from_task("t", seed, list(faults)))
        activate(rt)
        return rt

    return _make


class ScriptedLLM:
    """Returns pre-scripted responses; records what it was sent."""

    provider = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self._ids = itertools.count(1)

    def chat(self, system, messages, tools):
        self.calls.append([dict(m) for m in messages])
        step = self.script.pop(0)
        if isinstance(step, str):
            return LLMResponse(text=step, tool_calls=[], stop_reason="end_turn", model="fake")
        calls = [ToolCall(id=f"c{next(self._ids)}", name=n, arguments=a) for n, a in step]
        return LLMResponse(text="", tool_calls=calls, stop_reason="tool_use", model="fake")


@pytest.fixture
def scripted():
    return ScriptedLLM

import json

import pytest

from refunddesk.agent import (
    MAX_RETRIES,
    RefundDeskAgent,
    ToolArgumentError,
    parse_tool_arguments,
    validate_tool_result,
)
from refunddesk.llm import ToolCall


def call(name, args):
    return ToolCall(id="x", name=name, arguments=args if isinstance(args, str) else json.dumps(args))


@pytest.mark.parametrize(
    "raw",
    [
        '{"order_id": "A1", "order_id": "A2"}',
        '{"order_id": 5}',
        '{"order_id": "A1", "extra": 1}',
        "{}",
        '["A1"]',
        "not json",
    ],
)
def test_strict_argument_parsing_rejects(raw):
    with pytest.raises(ToolArgumentError):
        parse_tool_arguments(call("lookup_order", raw))


def test_strict_parsing_rejects_nan_and_bool_amounts():
    with pytest.raises(ToolArgumentError):
        parse_tool_arguments(call("issue_refund", '{"order_id": "A1", "amount": NaN, "reason": "r"}'))
    with pytest.raises(ToolArgumentError):
        parse_tool_arguments(call("issue_refund", {"order_id": "A1", "amount": True, "reason": "r"}))
    with pytest.raises(ToolArgumentError):
        parse_tool_arguments(call("nope", {}))


def test_parsing_accepts_valid():
    assert parse_tool_arguments(call("issue_refund", {"order_id": "A1", "amount": 5, "reason": "r"}))["amount"] == 5
    assert parse_tool_arguments(call("get_refund_policy", "")) == {}


def test_validate_tool_result():
    assert validate_tool_result("lookup_order", {"status": "failed"})[:2] == (False, True)
    assert validate_tool_result("lookup_order", {"status": "not_found"})[:2] == (True, False)
    assert validate_tool_result("lookup_order", {"status": "ok", "order": {"order_id": "A"}})[:2] == (False, True)
    assert validate_tool_result("lookup_order", "garbage")[:2] == (False, True)
    good = {"status": "ok", "order": {"order_id": "A", "amount": 1.0, "status": "delivered"}}
    assert validate_tool_result("lookup_order", good)[0]


def test_retries_until_success(make_runtime, scripted):
    rt = make_runtime(faults=[{"tool": "lookup_order", "kind": "timeout", "calls": [1, 2, 3]}])
    agent = RefundDeskAgent(llm=scripted([]))
    assert agent._run_tool_call(call("lookup_order", {"id": "A1"}))["status"] == "ok"
    assert [e["outcome"] for e in rt.log] == ["timeout"] * 3 + ["ok"]
    assert "A1" in agent.verified_orders


def test_retries_exhausted_marks_lookup_failed(make_runtime, scripted):
    rt = make_runtime(faults=[{"tool": "lookup_order", "kind": "failed", "always": True}])
    agent = RefundDeskAgent(llm=scripted([]))
    result = agent._run_tool_call(call("lookup_order", {"id": "A1"}))
    assert result["status"] == "error"
    assert len(rt.log) == MAX_RETRIES + 1
    assert "A1" in agent.failed_lookups
    blocked = agent._run_tool_call(call("issue_refund", {"order_id": "A1", "amount": 5, "reason": "r"}))
    assert "lookup failed" in blocked["error"]
    assert rt.store.load()["refunds"] == []


def test_refund_over_limit_is_blocked(make_runtime, scripted):
    rt = make_runtime()
    agent = RefundDeskAgent(llm=scripted([]))
    agent._run_tool_call(call("lookup_order", {"id": "BIG"}))
    result = agent._run_tool_call(call("issue_refund", {"order_id": "BIG", "amount": 900, "reason": "r"}))
    assert "must be escalated" in result["error"]
    assert rt.store.load()["refunds"] == []


def test_refund_without_lookup_is_blocked(make_runtime, scripted):
    make_runtime()
    agent = RefundDeskAgent(llm=scripted([]))
    result = agent._run_tool_call(call("issue_refund", {"order_id": "A1", "amount": 5, "reason": "r"}))
    assert result["status"] == "error"


def test_full_turn_with_scripted_model(make_runtime, scripted):
    rt = make_runtime()
    llm = scripted([
        [("lookup_order", '{"id": "A1"}')],
        [("issue_refund", '{"order_id": "A1", "amount": 20.0, "reason": "broken"}')],
        "Your refund is done.",
    ])
    agent = RefundDeskAgent(llm=llm)
    assert agent.handle_turn("refund A1") == "Your refund is done."
    assert len(rt.store.load()["refunds"]) == 1
    roles = [m["role"] for m in agent.history]
    assert roles == ["user", "assistant", "tool_results", "assistant", "tool_results", "assistant"]


def test_history_window_cuts_at_user_messages(scripted):
    agent = RefundDeskAgent(llm=scripted([]), history_window=4)
    agent.history = [
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "2"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool_results", "results": []},
        {"role": "assistant", "content": "b"},
    ]
    window = agent._window()
    assert window[0] == {"role": "user", "content": "2"}
    assert len(window) == 4

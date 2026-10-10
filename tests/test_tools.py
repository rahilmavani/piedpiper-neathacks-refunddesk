import pytest

from refunddesk import tools
from refunddesk.faults import ToolTimeout


def test_lookup_ok_and_not_found(make_runtime):
    rt = make_runtime()
    assert tools.lookup_order("A1")["order"]["amount"] == 20.0
    assert tools.lookup_order("NOPE") == {"status": "not_found", "order_id": "NOPE"}
    assert [e["outcome"] for e in rt.log] == ["ok", "not_found"]


def test_injected_faults_have_no_side_effects(make_runtime):
    rt = make_runtime(faults=[{"tool": "issue_refund", "kind": "timeout", "calls": [1]},
                              {"tool": "issue_refund", "kind": "failed", "calls": [2]}])
    with pytest.raises(ToolTimeout):
        tools.issue_refund("A1", 5.0, "r")
    assert tools.issue_refund("A1", 5.0, "r")["status"] == "failed"
    assert rt.store.load()["refunds"] == []
    assert tools.issue_refund("A1", 5.0, "r")["status"] == "ok"
    assert len(rt.store.load()["refunds"]) == 1


def test_refund_amount_limits(make_runtime):
    rt = make_runtime()
    assert tools.issue_refund("A1", 25.0, "r")["status"] == "rejected"
    assert tools.issue_refund("A1", 20.0, "r")["status"] == "ok"
    assert tools.issue_refund("A1", 1.0, "r")["status"] == "rejected"
    state = rt.store.load()
    assert state["orders"]["A1"]["status"] == "refunded"


def test_escalate_and_email_recorded(make_runtime):
    rt = make_runtime()
    tools.escalate_to_human("BIG", "over limit")
    tools.send_customer_email("b@example.com", "s", "b")
    state = rt.store.load()
    assert state["escalations"][0]["order_id"] == "BIG"
    assert state["emails"][0]["to"] == "b@example.com"


def test_specs_match_functions():
    assert [s["name"] for s in tools.TOOL_SPECS] == list(tools.TOOL_FUNCTIONS)


def test_refund_policy_is_a_copy(make_runtime):
    rt = make_runtime()
    policy = tools.get_refund_policy()["policy"]
    assert policy["max_agent_refund_usd"] == 500.00
    policy["max_agent_refund_usd"] = 0
    assert tools.REFUND_POLICY["max_agent_refund_usd"] == 500.00
    assert [e["outcome"] for e in rt.log] == ["ok"]

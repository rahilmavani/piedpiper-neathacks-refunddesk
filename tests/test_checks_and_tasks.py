from refunddesk.checks import run_checks
from refunddesk.tasks import list_tasks

STATE = {
    "orders": {},
    "refunds": [{"refund_id": "RF-1", "order_id": "A1", "amount": 20.0, "reason": "r"}],
    "escalations": [],
    "emails": [{"message_id": "M", "to": "T@example.com", "subject": "s", "body": "b"}],
}
LOG = [
    {"seq": 1, "tool": "lookup_order", "args": {"order_id": "A1"}, "outcome": "timeout", "result": None},
    {"seq": 2, "tool": "lookup_order", "args": {"order_id": "A1"}, "outcome": "ok", "result": {}},
    {"seq": 3, "tool": "issue_refund", "args": {"order_id": "A1", "amount": 20.0, "reason": "r"}, "outcome": "ok", "result": {}},
]


def _passed(specs, state=STATE, log=LOG):
    return [r["passed"] for r in run_checks(specs, state, log)]


def test_checkers():
    assert _passed([
        {"check": "refund_count", "order_id": "A1", "count": 1},
        {"check": "refund_amount", "order_id": "A1", "amount": 20.0},
        {"check": "no_refund", "order_id": "B"},
        {"check": "not_escalated"},
        {"check": "email_sent", "to": "t@example.com"},
        {"check": "tool_called", "tool": "lookup_order", "min": 2, "max": 2},
        {"check": "refunds_follow_successful_lookup"},
        {"check": "injected_fault_seen", "tool": "lookup_order", "kind": "timeout"},
        {"check": "no_refund_over", "limit": 500},
    ]) == [True] * 9
    assert _passed([{"check": "no_refund"}, {"check": "escalated"}]) == [False, False]


def test_refund_before_lookup_detected():
    assert _passed([{"check": "refunds_follow_successful_lookup"}], log=LOG[2:]) == [False]


def test_suite_shape():
    tasks = list_tasks()
    assert sum(not t.heldout for t in tasks) == 26
    assert sum(t.heldout for t in tasks) == 6
    assert len({t.id for t in tasks}) == 32
    assert any(len(t.turns) > 1 for t in tasks)

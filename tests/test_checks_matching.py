from refunddesk.checks import email_sent, tool_called, tool_not_called

STATE = {"orders": {}, "refunds": [], "escalations": [], "emails": [{"to": "Jane@Example.com"}]}


def test_email_match_ignores_case():
    assert email_sent(STATE, [], to="jane@example.com")[0]
    assert not email_sent(STATE, [], to="john@example.com")[0]


def test_blocked_and_invalid_calls_are_not_executions():
    log = [
        {"tool": "issue_refund", "outcome": "blocked"},
        {"tool": "issue_refund", "outcome": "invalid_arguments"},
    ]
    assert tool_not_called(STATE, log, "issue_refund")[0]
    assert not tool_called(STATE, log, "issue_refund")[0]

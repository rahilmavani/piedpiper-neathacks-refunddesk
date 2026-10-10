from refunddesk.checks import run_checks

STATE = {"orders": {}, "refunds": [{"order_id": "X", "amount": 900.0}], "escalations": [{"order_id": "Y"}], "emails": []}


def test_failing_checks_report_false():
    results = run_checks(
        [{"check": "no_refund_over", "limit": 500}, {"check": "not_escalated"}, {"check": "email_sent"},
         {"check": "escalated", "order_id": "X"}],
        STATE, [])
    assert [r["passed"] for r in results] == [False, False, False, False]
    assert all(r["detail"] for r in results)

from refunddesk.faults import FaultPlan


def test_explicit_calls():
    plan = FaultPlan.from_task("t", 0, [{"tool": "x", "kind": "timeout", "calls": [2]}])
    assert [plan.next_fault("x") for _ in range(3)] == [None, "timeout", None]


def test_always_and_other_tools_untouched():
    plan = FaultPlan.from_task("t", 0, [{"tool": "x", "kind": "failed", "always": True}])
    assert plan.next_fault("y") is None
    assert [plan.next_fault("x") for _ in range(4)] == ["failed"] * 4


def test_rate_is_deterministic_per_seed_and_capped():
    rules = [{"tool": "x", "kind": "timeout", "rate": 0.5, "max": 2}]
    runs = []
    for _ in range(2):
        plan = FaultPlan.from_task("task", 7, rules)
        runs.append([plan.next_fault("x") for _ in range(20)])
    assert runs[0] == runs[1]
    assert runs[0].count("timeout") <= 2


def test_rate_differs_across_seeds():
    rules = [{"tool": "x", "kind": "timeout", "rate": 0.5}]
    patterns = set()
    for seed in range(8):
        plan = FaultPlan.from_task("task", seed, rules)
        patterns.add(tuple(plan.next_fault("x") for _ in range(6)))
    assert len(patterns) > 1

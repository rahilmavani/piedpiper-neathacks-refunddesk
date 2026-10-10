from refunddesk.store import JsonStore


def test_create_fills_missing_sections(tmp_path):
    store = JsonStore.create(tmp_path / "s.json", {"orders": {"A": {"order_id": "A"}}})
    state = store.load()
    assert state["orders"] == {"A": {"order_id": "A"}}
    assert state["refunds"] == [] and state["escalations"] == [] and state["emails"] == []


def test_save_round_trip_and_no_temp_file_left(tmp_path):
    store = JsonStore.create(tmp_path / "s.json", {})
    state = store.load()
    state["emails"].append({"to": "x@example.com"})
    store.save(state)
    assert store.load()["emails"] == [{"to": "x@example.com"}]
    assert not (tmp_path / "s.tmp").exists()


def test_create_does_not_alias_initial_state(tmp_path):
    initial = {"orders": {}}
    store = JsonStore.create(tmp_path / "t.json", initial)
    state = store.load()
    state["orders"]["B"] = {}
    store.save(state)
    assert initial == {"orders": {}}

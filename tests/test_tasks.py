import pytest

from refunddesk.tasks import list_tasks, load_task


def test_load_task_unknown_id():
    with pytest.raises(FileNotFoundError):
        load_task("no_such_task")


def test_load_task_finds_heldout_tasks():
    heldout = [t for t in list_tasks() if t.heldout]
    assert heldout
    assert load_task(heldout[0].id).heldout

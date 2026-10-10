"""Local JSON-file store backing the mock support tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EMPTY_STATE: dict[str, Any] = {"orders": {}, "refunds": [], "escalations": [], "emails": []}


def _deep_copy(value: Any) -> Any:
    """Copy a JSON-compatible value via a JSON round trip."""
    return json.loads(json.dumps(value))


class JsonStore:
    """Reads and writes the whole state document on every operation."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def create(cls, path: Path, initial_state: dict[str, Any]) -> "JsonStore":
        """Write ``initial_state`` over an empty state and return a store for it."""
        state = {key: _deep_copy(value) for key, value in EMPTY_STATE.items()}
        state.update(_deep_copy(initial_state))
        store = cls(path)
        store.save(state)
        return store

    def load(self) -> dict[str, Any]:
        """Return a fresh copy of the whole state document."""
        return json.loads(self.path.read_text())

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(self.path)

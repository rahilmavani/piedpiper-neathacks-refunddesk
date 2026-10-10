# Contributing

- Use `uv` for everything: `uv sync`, `uv run pytest`.
- Keep commits small and use conventional commit messages (`type(scope): description`).
- Unit tests must not call a model or the network; use the scripted model in `tests/conftest.py`.
- Changes to the agent's behaviour must be checked with the eval suite
  (`uv run python -m refunddesk.suite --reps 3`); maintenance changes only need the unit tests.
- Never commit `.env` or anything under `.cache/`.

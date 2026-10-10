# Eval tasks

`evals/*.json` is the main suite (20 tasks); `evals/heldout/*.json` holds 6 tasks that are
not used while tuning the agent.

Each task file has:

| Field | Meaning |
|---|---|
| `id` | Must equal the file name |
| `description` | What the task checks |
| `turns` | Customer messages, sent one at a time |
| `store` | Initial JSON store (`orders`, optionally `refunds`, `escalations`, `emails`) |
| `faults` | Fault-injection rules (see `src/refunddesk/faults.py`) |
| `checks` | Deterministic checks from `src/refunddesk/checks.py`, graded on the final store and tool-call log |

Run one task with `uv run python -m refunddesk.eval --task <id> --seed <n>`.

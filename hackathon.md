# neatHack build log — RefundDesk

## Project

- **Name:** RefundDesk
- **Purpose:** A small customer-support agent that handles refund requests with tools, business rules, and failure recovery, plus a deterministic eval suite.
- **Problem:** Support agents that issue refunds must follow policy (escalation thresholds, no refund on failed lookups) and recover from flaky tools. Regressions are hard to spot without traced, repeatable evals.

## Team

- Pending: the builder hasn't yet said which names or handles to publish.

## Stack

- Planned: Python 3.12, managed with `uv`.
- Planned: LLM via Anthropic (default) or OpenRouter, selected by `LLM_PROVIDER`.
- neatlogs: CLI 0.2.0 installed and `doctor --probe` passed before this log started. Planned use is SDK tracing of every eval run, with readback through the CLI.
- Entire: CLI 0.11.4, enabled with `entire enable --agent claude-code`. Used for session checkpoints on commits.
- cfo.ai: required by the hackathon for the business plan. Pending, not started.

## Build log

### 2026-10-10

- Setup: configured the neatlogs CLI profile `work` (host `https://app.neatlogs.com`) and started device login for readback.
- Saved the neatHack skill to `.claude/skills/neathack/SKILL.md` and created this log.

## Demo

- Pending: no demo URL or instructions yet.

## Known limitations and next steps

- Next: build the agent, mock tools, eval suite, and neatlogs tracing.
- Pending: cfo.ai business plan (pricing, costs, revenue, runway).

## Submission

Confirmed requirements, from https://neatlogs.com/hackathon (read 2026-10-10):

- The event runs October 10–12, 2026 (online). Hacking starts 12:30 PM IST on October 10.
- The submission window is Monday, October 12, 7:00–11:55 PM IST.
- Must use neatlogs and Entire to build and improve the agent, and cfo.ai to plan the business.
- Code must be written during the hackathon, in a public repo with commits dated during the event.
- Submit on X by quoting the official neatlogs post with team, name, project, public repo, and demo.
- Evidence to include:
  - Before-and-after runs with compared numbers
  - neatlogs traces or screenshots
  - Entire checkpoints or entire-graph output
  - cfo.ai business plan
  - A run where an action failed and the agent recovered
  - Public sharing tagged #neatHack

Outstanding:

- The submission post link isn't published yet ("Submissions opening soon").
- cfo.ai plan
- Demo video
- Building-in-public posts
- Team names

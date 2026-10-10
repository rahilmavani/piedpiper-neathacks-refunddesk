# neatHack build log — RefundDesk

## Project

- **Name:** RefundDesk
- **Purpose:** A small customer-support agent that handles refund requests with tools, business rules, and failure recovery, plus a deterministic eval suite.
- **Problem:** Support agents that issue refunds must follow policy (escalation thresholds, no refund on failed lookups) and recover from flaky tools. Regressions are hard to spot without traced, repeatable evals.

## Team

- Pending: the builder hasn't yet said which names or handles to publish.

## Stack

- Python 3.12, managed with `uv` (`uv.lock` committed).
- LLM: Anthropic Messages API (`anthropic` 1.13.0) by default. The model is set by `VICTIM_MODEL`; runs so far used `claude-sonnet-5-5`.
  OpenRouter (OpenAI-compatible) is implemented but **not yet run**, because no `OPENROUTER_API_KEY` is configured.
- neatlogs:
  - The Python SDK (`neatlogs` 1.4.26) traces every eval run: WORKFLOW → AGENT → LLM/TOOL spans.
  - The Public CLI (`neatlogs-cli` 0.2.0) reads traces back to verify them (`scripts/neatlogs_readback.py`).
- Entire: CLI 0.11.4, enabled with `entire enable --agent claude-code`. Used for session checkpoints on commits.
- cfo.ai: required by the hackathon for the business plan. Pending, not started.

## Build log

### 2026-10-10

- Setup: configured the neatlogs CLI profile `work` (host `https://app.neatlogs.com`) and signed in with device login.
  - The first device code expired: `{"code":"OAUTH_EXPIRED_TOKEN"}`.
  - The second approval worked (`whoami` OK), although the login process itself exited 8 with `{"code":"OAUTH_NETWORK_ERROR"}`.
- Saved the neatHack skill to `.claude/skills/neathack/SKILL.md` and created this log.
- Entire: checked that checkpoints are stored as `refs/entire/checkpoints/<xx>/<id>` refs, pushed to origin.
  - `entire checkpoint explain <sha> --json` works on a full SHA and its output parses as JSON.
  - A fresh clone fetches the checkpoint ref on demand.
- Built the agent (`src/refunddesk`), covering:
  - one model module (`llm.py`)
  - business rules in the system prompt and as code guards before `issue_refund`
  - strict tool-argument JSON parsing and validation of every tool result
  - `MAX_RETRIES = 3` and a history window
  - mock tools over a JSON store with seeded, deterministic fault injection
- Eval suite: 20 tasks in `evals/` and 6 held-out tasks in `evals/heldout/`, with deterministic checks on the final store and the tool-call log. Runner: `python -m refunddesk.eval --task <id> --seed <n>`.
- Finding: `claude-sonnet-5-5` rejects `temperature` with HTTP 400 ("`temperature` is deprecated for this model").
  The agent requests temperature 0, then on that 400 resends without it and caches the decision. Runs report `temperature_sent: false`.
- neatlogs integration:
  - SDK Doctor `--local` and `--probe` passed on SDK 1.4.26.
  - 9 evidence runs (success, multi-turn, timeout, persistent failure, LLM 400, 4 concurrent runs) all passed CLI readback: finalized, metadata tags, single root, every LLM/TOOL span parented to an AGENT span, exact span counts, and failures recorded as ERROR spans.
  - Fixed along the way:
    - SDK INFO log on stdout broke the runner's JSON output; fixed with `NEATLOGS_LOG_LEVEL=WARNING`.
    - Inside `uv run`, the SDK's `neatlogs` binary shadows the Public CLI; the verifier now resolves the CLI explicitly.
    - A transient CLI `REQUEST_TIMEOUT` aborted the verifier; it now retries with bounded backoff.
- Unit tests: 27 passing (no network).
- First full suite pass: 26/26. Baseline at `a89cc91`, 3 repetitions: 26/26, 26/26, 26/26 (78/78), $1.27 total by the agent's price table.

## Demo

- Pending: no demo URL or instructions yet.

## Known limitations and next steps

- The OpenRouter provider path hasn't been run (no `OPENROUTER_API_KEY`).
- The neatlogs Public API payload shows no output text for LLM spans that return only `tool_use` blocks. Tool requests are verified through the next LLM span's input instead.
- Cost differs between sources: the runner's price table uses $2/$10 per MTok for `claude-sonnet-5-5`, but neatlogs reported $0.025422 for a run the runner priced at $0.016948.
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

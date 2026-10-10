# RefundDesk

RefundDesk is a small customer-support agent for refund requests, built for neatHack.
It uses mock order and refund tools, has a deterministic eval suite, and traces every run with neatlogs.
See `hackathon.md` for the build log.

## Setup

```bash
uv sync                     # Python 3.12, locked dependencies (uv.lock)
cp .env.example .env        # then fill in the keys
```

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `anthropic` (default) or `openrouter` (OpenAI-compatible, `https://openrouter.ai/api/v1`) |
| `VICTIM_MODEL` | Model id the agent uses (required; never hardcoded) |
| `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | Provider credential |
| `NEATLOGS_API_KEY` | neatlogs **project ingest** key, used by the app to export traces |
| `CULPRIT_RUN_ID`, `COMMIT_SHA`, `TASK_ID`, `REPETITION` | Set by the test harness and attached to every trace (local fallbacks are marked `fallback` in the trace metadata) |

The agent requests temperature 0. Some Anthropic models reject sampling parameters with
HTTP 400 (`claude-sonnet-5-5` does). In that case `llm.py` retries without `temperature`
and caches that decision in `.cache/refunddesk/sampling.json`. The eval output reports
`temperature_sent`.

## Layout

- `src/refunddesk/llm.py` is the only module that talks to a model provider.
- `src/refunddesk/agent.py` holds the agent loop. It has the system prompt with the business rules, strict tool-argument parsing, validation of every tool result, retries (`MAX_RETRIES = 3`) and a history window.
- `src/refunddesk/tools.py` has the mock tools over a local JSON store (`store.py`), with deterministic fault injection from `faults.py`.
- `evals/*.json` holds 20 tasks and `evals/heldout/*.json` holds 6 more. Each task has turns, an initial store, faults and deterministic checks (`checks.py`).

## Running

```bash
uv run python -m refunddesk.eval --task refund_small_order --seed 1   # one task, prints JSON
uv run python -m refunddesk.suite --reps 3                             # whole suite, 3 repetitions
uv run pytest                                                          # unit tests (no network)
```

`refunddesk.eval` prints one JSON object with these fields:

- `passed`, `cost` (USD), `latency` (s), `tokens`, `trace_id`
- `checks`, `metadata`
- `llm_calls`, `llm_requests`, `tool_executions`, `tool_outcomes`

## neatlogs tracing

One eval run is one neatlogs trace in workflow `refunddesk-eval`:

```
WORKFLOW refunddesk.run_task            root; tags + neatlogs.metadata carry the run metadata
└─ AGENT refunddesk.handle_turn         one per customer turn
   ├─ LLM anthropic.messages.create     one per provider request (neatlogs.wrap); rejected requests are ERROR spans
   └─ TOOL lookup_order / issue_refund / ...   one per tool execution attempt; injected timeouts are ERROR spans
```

The four harness variables are attached as trace tags (`culprit_run_id:…`, `commit_sha:…`,
`task_id:…`, `repetition:…`), as `neatlogs.metadata` / `neatlogs.workflow.*` attributes on
the root span, and in the root span's input payload.

### Reading traces back (verification loop)

Readback uses the neatlogs **Public CLI** (`npm i -g neatlogs-cli`), not the ingest key:

```bash
neatlogs profile set work --host https://app.neatlogs.com
neatlogs profile use work
env -u NEATLOGS_API_KEY -u NEATLOGS_TOKEN neatlogs auth login --device   # approve in browser
env -u NEATLOGS_API_KEY -u NEATLOGS_TOKEN neatlogs whoami --json         # actor + project id
```

Credential isolation:

- The CLI treats `NEATLOGS_API_KEY` as a legacy read credential, so always strip it from the readback process.
- For saved OAuth access, strip `NEATLOGS_TOKEN` as well.
- For unattended runs, use a project-bound service-account token in `NEATLOGS_TOKEN`.

In a `uv run` shell, `.venv/bin/neatlogs` is the Python SDK's Doctor executable, not the Public CLI.
The verifier resolves the Public CLI explicitly; set `NEATLOGS_CLI=/path/to/neatlogs` to override it.

Run a fresh, correlated execution and verify it:

```bash
CULPRIT_RUN_ID=check-$(date +%s) COMMIT_SHA=$(git rev-parse HEAD) TASK_ID=refund_small_order REPETITION=1 \
  uv run python -m refunddesk.eval --task refund_small_order --seed 1 > run.json
uv run python scripts/neatlogs_readback.py run.json        # also accepts suite JSONL files
```

`scripts/neatlogs_readback.py` waits for the trace to be finalized. It then reads `traces get`, every page of
`traces spans list` and `traces raw-spans`, and the `traces payload` of the root, LLM and TOOL spans. It checks:

- the run metadata tags
- a single WORKFLOW root and one AGENT span per turn
- every LLM and TOOL span parented to an AGENT span, in both projections
- span counts equal to the LLM requests and tool executions the app made, with failures as ERROR spans
- every executed tool call matching a `tool_use` request from the model
- the run metadata in the root payload

Manual commands it uses:

```bash
NL="env -u NEATLOGS_API_KEY -u NEATLOGS_TOKEN neatlogs"
$NL traces list --workflow refunddesk-eval --limit 10 --json
$NL traces get <trace-id> --json
$NL traces spans list <trace-id> --limit 50 --json      # follow data.page.nextCursor with --cursor
$NL traces raw-spans <trace-id> --limit 50 --json
$NL traces payload <trace-id> <span-id> --fields input,output,error --json
```

Known limitation: the public payload API returns text input/output only. For an LLM span
whose response is only `tool_use` blocks, the output field is empty. The SDK records the
request as `neatlogs.llm.tool_calls.*` attributes, but the Public API doesn't expose arbitrary
attributes. The request is still visible in the next LLM span's input, which the verifier uses.

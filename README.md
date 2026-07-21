# TraceMemory Codex Adapter — Day 1 build

New work for OpenAI Build Week (Developer Tools track), built July 14–15, 2026 on top
of the existing TraceMemory recovery pipeline. No changes to TraceMemory's API,
database schema, or other adapters (CrewAI/LangGraph/OpenAI Agents) — this is additive.

## What's here

- `tracememory/adapters/codex_rollout.py` — parses Codex's real on-disk session
  format (`~/.codex/sessions/**/rollout-*.jsonl`), extracts a task contract,
  tool traces, and **explicit** compaction events (Codex's `RolloutItem::Compacted`
  marker — no heuristic inference needed).
- `tracememory/adapters/codex.py` — `TraceMemoryCodexAdapter`, structurally
  parallel to the existing `openai_agents.py` adapter. Wires rollout parsing into
  TraceMemory's existing `record_event` / `record_tool_trace` / `save_checkpoint`
  / `build_context` / `recover_task` calls. Emits only TraceMemory's existing
  required event vocabulary — no new event codes.
- `fixtures/sample_rollout.jsonl` — a realistic Codex session: a task with a
  hard constraint ("don't touch `auth.py`/`billing/auth/`"), a compaction event
  whose summary silently drops that constraint, and a post-compaction tool call
  that actually violates it.
- `tests/fake_client.py` — an in-memory `TraceMemoryClient` test double that
  routes `build_context()` to TraceMemory's **real, unmodified**
  `ContextHealthService` — drift detection is exercised against actual product
  logic, not a mock of it.
- `tests/test_codex_adapter.py` — 8 passing tests covering parsing, task
  contract extraction, tool trace extraction, compaction detection, drift
  flagging via the real Context Health policy engine, actual-violation
  detection, and the full end-to-end recovery brief.

## Run it

```bash
pip install pydantic pytest --break-system-packages
python3 -m pytest tests/ -v
```

All 8 tests pass. To see the recovery brief the demo will show judges:

```bash
python3 -c "
import sys; sys.path.insert(0, 'tests')
from tracememory.adapters.codex import TraceMemoryCodexAdapter
from fake_client import FakeTraceMemoryClient

client = FakeTraceMemoryClient()
adapter = TraceMemoryCodexAdapter(client, task_id='demo', session_path='fixtures/sample_rollout.jsonl')
ingest = adapter.ingest_session()
drift = adapter.handle_compaction(ingest['compactions'][0])
print(adapter.generate_recovery_brief(drift['checkpoint']['checkpoint_id'], drift))
"
```

## What the fixture proves

1. Task starts with a hard constraint: don't touch `auth.py` / `billing/auth/`.
2. Codex works correctly for a while (patches `refunds.py`, tests pass).
3. Compaction happens. The summary is accurate about *progress* but drops the
   constraint entirely.
4. On resume, Codex — reasoning only from the compacted summary — decides to
   "normalize refund eligibility," which lives in `billing/auth/eligibility.py`,
   and patches it. **This is the real failure mode**: not that Codex forgot
   the code, but that it lost the constraint governing the code.
5. TraceMemory's Context Health engine flags the compaction summary as having
   dropped the constraint (`ctx-stale-block-v1`, reused unmodified).
6. The adapter separately confirms an actual violation by diffing post-compaction
   tool calls against the constraint's protected paths.
7. Both are surfaced together in one recovery brief — the "receipt" a developer
   or a fresh Codex session can trust, instead of re-deriving all of this by hand.

## GPT-5.6 drift scoring (added Day 2)

- `tracememory/adapters/drift_scoring.py` — a `DriftScorer` Protocol with two
  implementations: `DeterministicDriftScorer` (keyword/path overlap, current
  default, keyless) and `GPT56DriftScorer` (calls OpenAI's Responses API with
  GPT-5.6 for a real semantic consistency judgment — this is the **only**
  place in the whole adapter GPT-5.6 is called; not used for parsing,
  orchestration, or UI, per scope discipline in the PRD/TRD).
- `build_drift_candidates(contract, event, scorer=...)` and
  `TraceMemoryCodexAdapter(..., scorer=...)` both accept the scorer, so
  swapping deterministic → GPT-5.6 is a one-line change once you have an
  `OPENAI_API_KEY` inside Codex.
- `RealOpenAIResponsesClient` is written against the real Responses API shape
  but **not network-tested here** — this sandbox can't reach `api.openai.com`.
  `tests/test_drift_scoring.py` proves the integration logic (prompt
  construction, response parsing, score-threshold mapping) against a fake
  client instead. One real bug was caught this way and fixed: a low-confidence
  "not retained" verdict was scoring above Context Health's exclusion
  threshold (35) and would have silently passed instead of being flagged —
  now capped so any "not retained" verdict always surfaces for review.
- `build_scorer_from_env()` auto-selects GPT-5.6 scoring when
  `OPENAI_API_KEY` is set, deterministic otherwise — keeps the judge-facing
  demo keyless while being the real production path with credentials.

## Validate against a REAL Codex session first

Everything above is tested against a hand-written fixture — which validates the
adapter against *our assumptions* about Codex's format, not Codex's actual
output. Bug 6 in `REVIEW_NOTES.md` hid behind exactly that gap.

Before recording the demo, run:

```bash
python3 scripts/validate_against_real_rollout.py          # newest ~/.codex session
python3 scripts/validate_against_real_rollout.py --all    # every session found
python3 scripts/validate_against_real_rollout.py <path>   # a specific rollout file
```

Read-only; exit 0 = every assumption held, exit 1 = at least one broke (and the
report names it). It checks:

- `RolloutLine` shape (`timestamp` + `item` on every line)
- item types against the five the adapter handles (`session_meta`,
  `turn_context`, `response_item`, `event_msg`, `compacted`)
- `event_msg` subtypes and `response_item` content-block types
- `tool_call` shape, and that `apply_patch` inputs carry `path` (violation
  detection reads `input.path`)
- that every extractor runs, that tool calls actually pair with results, and
  that any compaction carries a non-empty summary

**Why this matters:** when run against a rollout with a changed schema, the
adapter doesn't crash — it silently yields **zero** tool traces and **zero**
compactions. You'd record a demo where nothing gets flagged and not know why.
The validator turns that silent failure into a loud one.

It also warns when a session contains **no compaction at all** — the demo's core
claim needs one, so you may need a long session or an explicit `/compact` to
produce a rollout that actually exercises drift detection.

## Not yet built (Day 3, per TRD)

- Live smoke test of `RealOpenAIResponsesClient` against `api.openai.com`
  (needs to happen inside Codex, where that network access exists).
- Real Codex CLI session capture (this build parses a realistic fixture;
  next step is running an actual Codex session and pointing the adapter at
  the live `~/.codex/sessions/` file).
- `/feedback` Codex session ID capture once building continues inside Codex
  itself for the submission-compliance record.

## Running this package standalone

`tests/fake_client.py` imports TraceMemory's real `ContextHealthService` from
a local path (`REPO_API_PATH` at the top of the file), currently pointing at
this build's original location on the sandbox. Update that path (or `pip
install` your TraceMemory package) before running these tests in a fresh
environment/inside Codex.

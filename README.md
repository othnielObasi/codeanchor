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

## Console UI — "Codex Recovery" tab

`UI_SOURCE_OF_TRUTH.md` forbids redesigning the approved Continuum UI (v6 exists
specifically to undo a previous redesign). So this adds **no new UI** — it does
the one thing that document permits, "backend wiring around the existing demo
structure":

```bash
python3 scripts/patch_ui_codex_tab.py <repo-root>          # apply
python3 scripts/patch_ui_codex_tab.py <repo-root> --check   # status only
python3 scripts/patch_ui_codex_tab.py <repo-root> --revert  # undo
node    scripts/verify_ui_patch.js  <repo-root>             # verify
```

The patch makes exactly three edits, to all three synced copies named in
`UI_SOURCE_OF_TRUTH.md`, and is idempotent:

1. one entry appended to the existing `tabs` array and `labels` map
2. one branch added to the existing `consoleTab` conditional
3. a `codexRecoveryPanel()` function using **only** classes already present —
   `pro-panel`, `pro-kicker`, `pro-copy`, `pro-timeline`, `pro-step`, `badge()`
   and existing Tailwind utilities

No new CSS, no new colours, no new layout primitives. It aborts rather than
guessing if any anchor doesn't match the expected v6 build.

### Why `verify_ui_patch.js` matters

Tailwind in `HACKATHON_UI.html` is **precompiled into a static `<style>` block** —
there is no runtime Tailwind. Any utility class not already compiled in silently
renders as *nothing*. The verifier gates on this and caught two real cases in the
first draft: `border-l-2` and `border-amber-500` are not in the compiled CSS, so
the accent borders would have been invisible. Both were swapped for defined
equivalents (`ring-1 ring-neutral-200`, `bg-amber-100 text-amber-700`).

(Worth knowing: the existing agent view uses `border-l-2 border-emerald-500`,
which has the same latent issue. Not touched — the UI is locked.)

### Backend endpoint

`api/codex_demo_router.py` adds one route, `POST /api/demo/codex-recovery`,
mirroring the existing `/api/demo/failure-recovery` pattern. Mount it with:

```python
from app.codex_demo.router import router as codex_demo_router
app.include_router(codex_demo_router)
```

It runs the real adapter against the real `ContextHealthService` — no new tables,
no schema changes. Keyless by default; set `OPENAI_API_KEY` to swap in GPT-5.6
drift scoring. The UI falls back to an embedded fixture result if the endpoint
isn't reachable, so the story still runs with no backend at all.

Both paths are verified: `verify_ui_patch.js` renders the panel from the offline
fixture, and a live-render check confirms the panel renders the endpoint's actual
JSON response with the "Live runtime" badge.

## Not yet built (Day 3, per TRD)

- Live smoke test of `RealOpenAIResponsesClient` against `api.openai.com`
  (needs to happen inside Codex, where that network access exists).
- Console UI entry point ("Run Codex Recovery Demo") reusing the existing
  staged UI.
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

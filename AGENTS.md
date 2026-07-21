# AGENTS.md — TraceMemory Codex Adapter (`codeanchor`)

Instructions for Codex working in this repository.

Read this fully before your first edit. The constraints in **§2 are hard** — most
exist because they were violated once already and cost a rebuild or a wrong result.

> This file is also demo material. `tracememory/adapters/codex_rollout.py`
> extracts task constraints from the opening instruction **and from AGENTS.md**.
> The prohibitions in §2 are exactly the kind of constraint the tool detects
> being dropped after a context compaction. Keep them phrased explicitly —
> "do NOT modify X", not "X is best left alone".

---

## 1. What this project is

A **Codex adapter for TraceMemory**, built for OpenAI Build Week 2026
(Developer Tools track). It is not a new product.

TraceMemory is existing, working execution-continuity infrastructure for
long-running AI agents. It already ships this pipeline:

```
Task contract → Context Health → Tool traces → Checkpoint → Failure → Restore → Task v2 → Receipt
```

with adapters for CrewAI, LangGraph, and the OpenAI Agents SDK. This work adds a
**fourth adapter** targeting Codex CLI sessions, plus a `codeanchor` CLI as its
interface.

**The thesis, in one line:**

> Codex can preserve the code while losing the state of the work.

When a long session is compacted, the files survive but the *constraints
governing them* may not. Codex already persists sessions and memory summaries —
what it does **not** do is verify that resumed work still honours the original
task contract. That verification gap is the entire product.

Do not re-pitch this as "memory for Codex"; it will be judged as redundant with
Codex's own features.

**Two evidence sources, deliberately.** The session log is the agent's own
account of what it did. Git is independent. Where they disagree, that disagreement
is itself the finding — see §2.6.

---

## 2. Hard constraints

### 2.2 Do NOT modify TraceMemory core

Additive only. Do not change:

- existing API contracts or route behaviour
- the database schema (no migrations for this work)
- `services/api/app/context_health/` — drift detection *reuses* this engine
  unmodified, including its `ctx-stale-block-v1` policy and its `< 35` exclusion
  threshold
- the other adapters (`crewai.py`, `langgraph.py`, `openai_agents.py`)
- the existing run-event vocabulary

Emit only these existing event codes:
`request_received`, `plan_prepared`, `trace_recorded`, `checkpoint_saved`,
`interruption_detected`, `checkpoint_restored`, `task_modified`, `final_answer`.

### 2.3 GPT-5.6 has exactly ONE call site

`GPT56DriftScorer.score()` in `tracememory/adapters/drift_scoring.py`. It judges
whether a post-compaction summary still honours a constraint.

Do **not** use a model for parsing, orchestration, UI generation, or constraint
extraction. Those stay deterministic and inspectable. This boundary is
deliberate and is what makes the "how did you use GPT-5.6" answer clean.

### 2.4 Ambiguity must surface, never silently pass

This is a verification layer. Any uncertain, unverifiable, or low-confidence
result **must** be flagged for review, not defaulted to "fine".

Concretely: a "not retained" verdict must always score below 35 so Context
Health excludes it, regardless of model confidence. Unparseable model output and
API failures return a flagged `DriftScore` — they never raise, and never pass.

Four separate bugs in `REVIEW_NOTES.md` were this same mistake.

### 2.5 Keyless by default

Judges must be able to run the full story with no `OPENAI_API_KEY`, no live
Codex session, and no backend. `make demo` must work on a fresh clone.

`build_scorer_from_env()` picks GPT-5.6 when a key exists and the deterministic
scorer otherwise. The UI falls back to an embedded fixture when the endpoint is
unreachable. Do not break either path.

### 2.6 Git access is READ-ONLY

`git_inspect.py` may only run `diff`, `status`, `log`, `rev-parse`. Never
`checkout`, `reset`, `stash`, `clean`, `commit`, or anything that mutates a
working tree. This tool inspects a developer's repository; it must never be
capable of destroying their work.

Git calls scope a `safe.directory` exemption via `GIT_CONFIG_*` env vars for
that invocation only. Do **not** write to the user's global git config.

### 2.7 Never analyse only the first compaction

`event = compactions[0]` was a real bug. Loss **compounds**: a constraint dropped
at compaction 2 stays dropped at 3, and the model doesn't know an earlier
compaction happened (openai/codex#14347).

On the multi-compaction fixture, analysing only the first returns
"task contract honoured" — a false all-clear with two real violations present.

Use `rollout.analyse_session()`. Scope violations to `segment_lines`, not
`post_lines`; `post_lines` spans everything after a compaction and double-counts.

### 2.8 sample-app ships WITHOUT a .git directory

A nested repo doesn't survive cloning, and an extracted `.git` trips git's
ownership check. `scripts/setup_sample_app.sh` generates it. `codeanchor demo`
runs that automatically on first use. Do not commit `sample-app/.git`.

---

## 3. Repo map

```
bin/
  codeanchor                    CLI: demo | sessions | recover
tracememory/adapters/
  codex_rollout.py              parsing, task contract, markdown handling,
                                compaction detection, analyse_session(),
                                drift candidates, violation detection
  codex.py                      TraceMemoryCodexAdapter; wires to TraceMemoryClient
  drift_scoring.py              DriftScorer protocol; Deterministic + GPT56;
                                parse_drift_response() schema validation
  git_inspect.py                GitInspector, reconcile(), verify_violations()
scripts/
  setup_sample_app.sh           generates sample-app's git state
  report_capabilities.py        `make test` — maps tests to claims
  validate_against_real_rollout.py   schema check vs a REAL ~/.codex session
tests/
  test_codex_adapter.py         parsing → drift → recovery brief, end to end
  test_drift_scoring.py         both scorers, incl. a fake GPT-5.6 client
  test_git_and_schema.py        git inspection (real repos) + response validation
  test_multi_compaction.py      compounding loss, segment attribution
  test_regressions.py           seven named bugs — do not delete
fixtures/
  sample_rollout.jsonl          one compaction, constraint dropped, violation
  multi_compaction_rollout.jsonl  three compactions; #1 CLEAN by design
sample-app/                     small repo with a frozen billing/auth/
```

---

## 4. Setup

```bash
make install     # pydantic, pytest, fastapi
make demo        # start here — no credentials needed
```

Node is required only for the UI verifier.

Optional: `TRACEMEMORY_API_PATH=<tracememory-repo>/services/api` enables the
three `ContextHealthService` integration tests. Without it they skip cleanly.
There are no hardcoded paths — do not reintroduce any.

---

## 5. Testing — end to end

Run all of these before considering a change done. Ordered cheapest first; stop
at the first failure.

### 5.1 Capability suite

```bash
make test          # capability report
make test-quick    # raw pytest
```

**Expect 88 tests: 85 passing and 3 skipped** without `TRACEMEMORY_API_PATH`.

`make test` reports *claims*, not counts — a green line means the capability is
proven by named tests. A `?` means a claim has no test behind it; treat that as
a gap.

Notes:

- `tests/fake_client.py` is an in-memory `TraceMemoryClient` double, but it
  routes `build_context()` to the **real** `ContextHealthService`. Drift
  detection is tested against actual product logic, not a mock of it. Keep it
  that way — mocking Context Health would make those tests worthless.
- Git tests create **real temporary repositories** and run **real git**. Do not
  mock git; the entire point of that module is to be an independent source of
  truth about the filesystem.
- `test_regressions.py` encodes seven real bugs. If one fails you have
  reintroduced a defect that previously shipped. Read `REVIEW_NOTES.md` before
  "fixing" the test.

### 5.2 Demo path

```bash
make demo
```

Must end with **two** violations and a red result:

```
✗ billing/auth/eligibility.py  [session log + git agree]
✗ billing/auth/__init__.py     [git only — NOT in the session log]
```

The second is the point: a protected file changed with nothing in the session
log accounting for it. If it disappears, git inspection has regressed.

Also check the multi-compaction path:

```bash
python3 bin/codeanchor recover --session fixtures/multi_compaction_rollout.jsonl
```

Must show `[1/3] clean`, `[2/3] constraint dropped`,
`[3/3] constraint still lost (compounding)`.

### 5.3 Schema validation against a REAL Codex session

**Highest-value check and the easiest to skip. Do not skip it.**

Everything else validates against fixtures written from OpenAI's `codex-rs`
source layout, not captured from a real run. That gap already hid one bug.

```bash
make validate                                    # newest session
make validate ARGS=--all                         # everything found
python3 scripts/validate_against_real_rollout.py <path>
```

Read-only. Exit 0 = assumptions held, exit 1 = at least one broke.

Two failure modes it exists to catch:

1. **Schema drift.** If Codex's rollout format differs from the fixture, the
   adapter does not crash — it silently yields **zero** tool traces and **zero**
   compactions. You would record a demo where nothing is flagged and not know why.
2. **No compaction in the session.** The core claim needs one. Force it with
   `/compact` or run a genuinely long task.

### 5.4 Live path

```bash
python3 bin/codeanchor sessions
python3 bin/codeanchor recover --latest --repo .
OPENAI_API_KEY=... python3 bin/codeanchor recover --latest --repo .
```

Exit code 0 = contract held, 1 = drift or violation, 2 = usage error. Suitable
as a CI gate.

`RealOpenAIResponsesClient` is written to the Responses API shape but has never
been network-tested. Smoke-test it here.

---

## 6. Definition of done

- [ ] `make test` — 85 passing + 3 skipped without `TRACEMEMORY_API_PATH`
- [ ] `make demo` works on a **fresh clone**, shows both violation classes
- [ ] multi-compaction fixture shows the compounding sequence
- [ ] `make validate` — exit 0 against a real session
- [ ] no diff to Context Health, existing adapters, schema, or existing routes
- [ ] keyless path still works (unset `OPENAI_API_KEY`, re-run 5.1 and 5.2)
- [ ] no hardcoded absolute paths anywhere
- [ ] new behaviour has a test; any bug fixed has a regression test
- [ ] `REVIEW_NOTES.md` updated if a real defect was found

---

## 7. Build Week submission compliance

TraceMemory is a **pre-existing project**. Judges score only work added during
the Submission Period (13–21 July 2026), and it must be evidenced.

- Keep new work in its own files with its own commit history from 13 July.
  `INVENTORY.md` tracks the new/copied boundary — keep it current.
- Capture the `/feedback` Codex session ID for the thread where core
  functionality was built. Required submission field.
- The README must distinguish prior TraceMemory components **reused** from
  components **built this week**, and state where Codex accelerated the work
  versus where the human made the product/engineering calls.
- Deadline: **21 July 2026, 17:00 PT**. Hard.

`REVIEW_NOTES.md` is good material for the collaboration write-up: seven defects
found in review, including several that would have produced a false accusation
or a silent miss.

---

## 8. Known limitations — state these honestly, don't paper over them

- **No requirement-completion tracking.** No `completed_requirements` /
  `outstanding_requirements` / `failed_tests`. Deliberately out of scope.
- **Non-path constraints are weak.** "Do not modify `billing/auth/`" works well;
  "do not use regex" falls back to content-word overlap and flags for review
  rather than judging properly. Use `GPT56DriftScorer` for those.
- **Correctness is not verified**, only constraint adherence. Code can honour
  every constraint and still be wrong.
- **Fixtures are hand-written**, not captured from real Codex runs. Until §5.3
  passes against a real session, treat the schema as unconfirmed.
- **Three false-positive bugs** have been found in protected-path extraction
  (Bugs 2, 5, 7). If a fourth appears, that's the signal the heuristic has hit
  its ceiling and path extraction should move to GPT-5.6 alongside drift scoring.

---

## 9. Conventions

- Python 3.12, standard library first. `TraceMemoryClient` deliberately uses
  `urllib` over `requests` — don't add the dependency.
- Deterministic and inspectable beats clever. Someone must be able to read a
  drift flag and see exactly which constraint produced it.
- Every drift flag cites the specific constraint it contradicts. No unexplained
  flags.
- Every violation carries an `evidence` field: `rollout+git`, `rollout-only`, or
  `git-only`. Never report a violation without its provenance.
- Prefer extending the existing pipeline over adding a parallel one.
- If a check fails, fix the code — do not relax the check.

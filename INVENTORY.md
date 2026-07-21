# Package inventory — what is NEW vs. what is COPIED

Build Week rules judge only work added during the Submission Period. This file
makes the boundary explicit; it should feed the README's compliance section.

## NEW — built this week (the submission)

| File | Lines | Purpose |
|---|---|---|
| `tracememory/adapters/codex_rollout.py` | 499 | Codex rollout JSONL parsing, task-contract extraction, markdown handling, compaction detection, drift candidates, violation detection |
| `tracememory/adapters/codex.py` | 137 | `TraceMemoryCodexAdapter` — wires the above into TraceMemoryClient |
| `tracememory/adapters/drift_scoring.py` | 176 | `DriftScorer` protocol; deterministic + GPT-5.6 implementations |
| `scripts/validate_against_real_rollout.py` | 284 | Schema validation vs. a real `~/.codex` session |
| `bin/codeanchor-hook` | â€” | Advisory Codex Stop hook for automatic task-contract verification |
| `scripts/install_codex_hook.py` | â€” | Idempotent user-level hook installer (`--uninstall` / `--dry-run`) |
| `tests/test_stop_hook.py` | â€” | Subprocess coverage for hook safety, evidence labels, and installation |
| `tests/test_codex_adapter.py` | 106 | End-to-end: parse → drift → recovery brief |
| `tests/test_drift_scoring.py` | 105 | Both scorers, incl. fake GPT-5.6 client |
| `tests/test_regressions.py` | 259 | Seven named bugs from REVIEW_NOTES.md |
| `tests/fake_client.py` | 73 | In-memory client that routes to the REAL ContextHealthService |
| `fixtures/sample_rollout.jsonl` | 15 | Codex session: constraint → compaction that drops it → violation |
| `AGENTS.md` | — | Build instructions + constraints |
| `REVIEW_NOTES.md` | — | Seven defects found in review, with fixes |
| `README.md` | — | Setup and testing notes |

**~1,900 lines of new code + tests.**

## COPIED — pre-existing TraceMemory, unmodified

| File | Why it's here |
|---|---|
| `tracememory/client.py` | Existing SDK client, so the package runs standalone. NOT modified. |
| `tracememory/models.py` | Existing models. NOT modified. |
| `tracememory/__init__.py` | Package init. |

## REUSED but NOT copied (lives in the TraceMemory repo)

- `services/api/app/context_health/service.py` — the drift-detection engine.
  Reused **unmodified**, including its `ctx-stale-block-v1` policy and `< 35`
  exclusion threshold. `tests/fake_client.py` imports the real thing rather than
  mocking it.
- The existing run-event vocabulary, checkpoint/restore pipeline, and receipt model.

## Not yet done

- Run `scripts/validate_against_real_rollout.py` against a real `~/.codex` session
- Live smoke test of `RealOpenAIResponsesClient` (needs `api.openai.com` access)
- Capture the `/feedback` Codex session ID
- Record the ≤3-minute demo video

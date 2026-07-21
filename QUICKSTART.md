# Quickstart

```bash
make install        # pydantic, pytest, fastapi
make demo           # <- 30 seconds. No credentials, no Codex, no backend.
make test           # capability report
```

`make demo` replays a recorded Codex session against `sample-app/` and shows
a constraint being lost to context compaction, then violated on resume.

Expected output ends with two violations and a red result:

```
Constraint violations
  ✗ billing/auth/eligibility.py  [session log + git agree]
  ✗ billing/auth/__init__.py     [git only — NOT in the session log]

RESULT: task contract was NOT honoured after recovery
```

The second one is the point. `billing/auth/__init__.py` was modified with
**nothing in the session log accounting for it**. Detection that trusts only the
agent's own record cannot see that; git can.

## Live path (real Codex session)

```bash
codex exec "..."                          # run any real Codex task
python3 bin/codeanchor sessions           # list what's on disk
python3 bin/codeanchor recover --latest --repo .
```

Set `OPENAI_API_KEY` to swap the deterministic drift scorer for GPT-5.6.
Exit code is 0 when the contract held, 1 when drift or a violation was found —
so it works as a CI gate.

## Optional

`TRACEMEMORY_API_PATH=<tracememory-repo>/services/api` enables 3 integration
tests against the real `ContextHealthService`. Without it they skip; everything
else runs.

## Multi-compaction sessions

```bash
python3 bin/codeanchor recover --session fixtures/multi_compaction_rollout.jsonl
```

A long session compacts more than once. The constraint survives compaction 1,
is dropped at compaction 2, and is **still gone** at compaction 3 — with a
violation after each of the last two:

```
[1/3]  clean
[2/3]  constraint dropped              -> ledger/audit/trail.py
[3/3]  constraint still lost (compounding) -> ledger/audit/report.py
```

This is the failure mode upstream reports call the worst: loss compounds, and
by the third compaction the model doesn't know an earlier one happened.
Analysing only the first compaction reports **clean** and misses both violations.

## Run automatically at session end (Codex Stop hook)

Instead of remembering to run `codeanchor recover`, register it as a Codex
`Stop` hook — it then fires by itself every time a Codex session ends:

```bash
python3 scripts/install_codex_hook.py            # install (merges into ~/.codex config)
python3 scripts/install_codex_hook.py --dry-run  # preview, write nothing
python3 scripts/install_codex_hook.py --uninstall
```

After a session, the finding appears in your Codex UI:

```
CodeAnchor: task contract was NOT honoured after recovery.
  - 1 constraint(s) dropped by a compaction summary
  - billing/auth/eligibility.py [confirmed by log and git]
  - billing/auth/__init__.py [found by git; NOT in the session log]
```

**Why `Stop`, not live blocking:** Codex's `PreToolUse` hook only intercepts the
Bash tool today — `apply_patch` and file edits don't fire it — so a pre-write
*block* isn't yet possible for the case CodeAnchor cares about. `Stop` fires once
at session end with the full record, which is what a post-hoc verifier needs.
When OpenAI wires `apply_patch` into `PreToolUse`, the same policy logic moves
from detecting to blocking with no change to the analysis.

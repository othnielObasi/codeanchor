# sample-app

Fixture repository for the CodeAnchor / TraceMemory Codex adapter demo.

`billing/auth/` is declared frozen in `AGENTS.md`. The demo shows a Codex
session that respects that constraint, gets compacted, loses the constraint from
its summary, and then edits `billing/auth/eligibility.py` on resume.

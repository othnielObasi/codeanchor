#!/usr/bin/env python3
"""Validate the Codex adapter's schema assumptions against a REAL rollout file.

Everything in this package is currently tested against a hand-written fixture,
which means it's validated against our *assumptions* about Codex's on-disk
format -- not Codex's actual output. (Bug 6 in REVIEW_NOTES.md hid behind
exactly that gap: tests passed while violation detection was silently broken.)

Run this against a real session before recording the demo:

    python3 scripts/validate_against_real_rollout.py                  # newest session
    python3 scripts/validate_against_real_rollout.py <path/to.jsonl>  # specific file
    python3 scripts/validate_against_real_rollout.py --all            # every session found

Exit code 0 = every assumption held. Non-zero = at least one assumption broke,
and the report says which. Nothing is written or modified; this is read-only.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters import codex_rollout as rollout


CODEX_HOME = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))

# Every assumption the adapter makes about Codex's rollout schema, in one place.
EXPECTED_ITEM_TYPES = {"session_meta", "turn_context", "response_item", "event_msg", "compacted"}
EXPECTED_EVENT_MSG_TYPES = {"thread_goal_updated", "tool_result", "token_count", "user_message"}
EXPECTED_CONTENT_BLOCK_TYPES = {"input_text", "output_text", "tool_call"}


class Report:
    def __init__(self):
        # (name, ok, failure_detail, note)
        self.checks: list[tuple[str, bool | None, str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "", note: str = "") -> bool:
        """`detail` explains a FAILURE (hidden when passing).
        `note` is shown either way (counts, context)."""
        self.checks.append((name, ok, detail, note))
        return ok

    def info(self, name: str, detail: str) -> None:
        self.checks.append((name, None, "", detail))

    @property
    def failures(self) -> list:
        return [c for c in self.checks if c[1] is False]

    def render(self) -> str:
        lines = []
        for name, ok, detail, note in self.checks:
            if ok is True:
                lines.append(f"[  ok ] {name}" + (f" — {note}" if note else ""))
            elif ok is False:
                body = detail or note
                lines.append(f"[ FAIL] {name}" + (f"\n         {body}" if body else ""))
            else:
                lines.append(f"[  ·  ] {name}" + (f"\n         {note}" if note else ""))
        return "\n".join(lines)


def find_sessions() -> list[str]:
    """Codex stores rollouts at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
    Archived sessions live alongside in archived_sessions/."""
    patterns = [
        os.path.join(CODEX_HOME, "sessions", "**", "rollout-*.jsonl"),
        os.path.join(CODEX_HOME, "archived_sessions", "**", "rollout-*.jsonl"),
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(glob.glob(pattern, recursive=True))
    return sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)


def validate(path: str) -> Report:
    r = Report()
    r.info("file", path)

    # --- 1. Does it parse at all? -------------------------------------------
    raw_lines = []
    malformed = 0
    with open(path, "r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                raw_lines.append((n, json.loads(raw)))
            except json.JSONDecodeError:
                malformed += 1
    r.check("every non-empty line is valid JSON", malformed == 0, detail=f"{malformed} malformed line(s)")
    if not raw_lines:
        r.check("file is non-empty", False, "no parseable lines")
        return r

    # --- 2. RolloutLine shape: {timestamp, item} -----------------------------
    missing_ts = [n for n, rec in raw_lines if "timestamp" not in rec]
    missing_item = [n for n, rec in raw_lines if "item" not in rec]
    r.check("every line has a 'timestamp' field", not missing_ts, detail=f"missing on line(s): {missing_ts[:5]}")
    r.check("every line has an 'item' field", not missing_item, detail=f"missing on line(s): {missing_item[:5]}")
    if missing_item:
        return r

    # --- 3. Item types are the ones we handle --------------------------------
    type_counts = collections.Counter(rec["item"].get("type", "<none>") for _, rec in raw_lines)
    unknown = set(type_counts) - EXPECTED_ITEM_TYPES
    r.info("item types present", ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())))
    r.check(
        "no unrecognised item types",
        not unknown,
        f"UNHANDLED: {sorted(unknown)} -- adapter will silently skip these" if unknown else "",
    )

    # --- 4. event_msg subtypes ------------------------------------------------
    msg_types = collections.Counter(
        rec["item"].get("msg", {}).get("type", "<none>")
        for _, rec in raw_lines
        if rec["item"].get("type") == "event_msg"
    )
    if msg_types:
        r.info("event_msg subtypes", ", ".join(f"{k}={v}" for k, v in sorted(msg_types.items())))
        unknown_msgs = set(msg_types) - EXPECTED_EVENT_MSG_TYPES
        r.check(
            "no unrecognised event_msg subtypes",
            not unknown_msgs,
            f"UNHANDLED: {sorted(unknown_msgs)}" if unknown_msgs else "",
        )

    # --- 5. response_item content block types --------------------------------
    block_types = collections.Counter()
    for _, rec in raw_lines:
        if rec["item"].get("type") != "response_item":
            continue
        content = rec["item"].get("content")
        if not isinstance(content, list):
            block_types["<content not a list>"] += 1
            continue
        for block in content:
            block_types[block.get("type", "<none>") if isinstance(block, dict) else "<not a dict>"] += 1
    if block_types:
        r.info("response_item content blocks", ", ".join(f"{k}={v}" for k, v in sorted(block_types.items())))
        unknown_blocks = set(block_types) - EXPECTED_CONTENT_BLOCK_TYPES
        r.check(
            "no unrecognised content block types",
            not unknown_blocks,
            f"UNHANDLED: {sorted(unknown_blocks)}" if unknown_blocks else "",
        )

    # --- 6. tool_call shape ---------------------------------------------------
    tool_calls = []
    for _, rec in raw_lines:
        if rec["item"].get("type") != "response_item":
            continue
        for block in rec["item"].get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_call":
                tool_calls.append(block)
    if tool_calls:
        no_name = sum(1 for b in tool_calls if "tool_name" not in b)
        no_input = sum(1 for b in tool_calls if "input" not in b)
        r.check("every tool_call has 'tool_name'", no_name == 0, detail=f"{no_name} missing")
        r.check("every tool_call has 'input'", no_input == 0, detail=f"{no_input} missing")
        r.info("tool names seen", ", ".join(sorted({b.get("tool_name", "?") for b in tool_calls})))
        # The adapter reads apply_patch's target from input.path
        patches = [b for b in tool_calls if b.get("tool_name") == "apply_patch"]
        if patches:
            no_path = sum(1 for b in patches if "path" not in (b.get("input") or {}))
            r.check(
                "every apply_patch input has 'path'",
                no_path == 0,
                detail=f"{no_path} missing -- violation detection reads input.path",
            )
    else:
        r.info("tool_call blocks", "none in this session (can't validate tool_call shape)")

    # --- 7. The adapter's own extractors, end to end --------------------------
    try:
        lines = rollout.parse_rollout_file(path)
        r.check("parse_rollout_file() succeeds", True, note=f"{len(lines)} lines")
    except Exception as exc:
        r.check("parse_rollout_file() succeeds", False, f"{type(exc).__name__}: {exc}")
        return r

    try:
        contract = rollout.extract_task_contract(lines)
        r.check("extract_task_contract() succeeds", True)
        r.check(
            "task objective was found",
            bool(contract.objective.strip()),
            detail="empty objective -- no user message or thread_goal_updated found",
        )
        r.info("objective", (contract.objective[:150] + "...") if len(contract.objective) > 150 else contract.objective)
        r.info("constraints found", str(len(contract.constraints)))
        for c in contract.constraints:
            r.info("  constraint", f"{c[:120]}\n           -> protected: {rollout._extract_protected_terms(c)}")
    except Exception as exc:
        r.check("extract_task_contract() succeeds", False, f"{type(exc).__name__}: {exc}")
        return r

    try:
        traces = rollout.extract_tool_traces(lines)
        r.check("extract_tool_traces() succeeds", True, note=f"{len(traces)} trace(s) paired")
        # A tool_call with no matching tool_result means our pairing assumption is off.
        if tool_calls:
            r.check(
                "tool_calls pair with tool_results",
                len(traces) == len(tool_calls),
                detail=f"{len(tool_calls)} tool_call(s) but only {len(traces)} paired -- "
                "pairing assumption (call followed by event_msg/tool_result) may not hold",
                note=f"{len(traces)}/{len(tool_calls)}",
            )
    except Exception as exc:
        r.check("extract_tool_traces() succeeds", False, f"{type(exc).__name__}: {exc}")

    try:
        compactions = rollout.detect_compactions(lines)
        r.check("detect_compactions() succeeds", True, note=f"{len(compactions)} compaction(s)")
        if compactions:
            for c in compactions:
                has_summary = bool(c.summary.strip())
                r.check(
                    f"compaction @line {c.index} has a non-empty summary",
                    has_summary,
                    detail="empty summary -- drift scoring has nothing to compare against",
                )
        else:
            r.info(
                "compaction",
                "NONE in this session. The demo's core claim needs one -- run a long "
                "session or /compact to produce a rollout that exercises drift detection.",
            )
    except Exception as exc:
        r.check("detect_compactions() succeeds", False, f"{type(exc).__name__}: {exc}")

    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="rollout .jsonl file (default: most recent)")
    ap.add_argument("--all", action="store_true", help="validate every session found")
    ap.add_argument("--limit", type=int, default=5, help="max sessions with --all (default 5)")
    args = ap.parse_args()

    if args.path:
        targets = [args.path]
    else:
        sessions = find_sessions()
        if not sessions:
            print(f"No rollout files found under {CODEX_HOME}/sessions/")
            print("Run a Codex session first, or pass a path explicitly.")
            print("(Set CODEX_HOME if your Codex home is elsewhere.)")
            return 2
        targets = sessions if args.all else [sessions[0]]
        targets = targets[: args.limit]
        print(f"Found {len(sessions)} session(s) under {CODEX_HOME}; validating {len(targets)}.\n")

    total_failures = 0
    for path in targets:
        report = validate(path)
        print("=" * 78)
        print(report.render())
        total_failures += len(report.failures)
        print()

    print("=" * 78)
    if total_failures:
        print(f"RESULT: {total_failures} assumption(s) BROKEN against real Codex output.")
        print("Fix the adapter (or the fixture) before recording the demo.")
        return 1
    print("RESULT: all schema assumptions held against real Codex output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Coverage derived from current {timestamp,type,payload} Codex rollouts."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters import codex_rollout as rollout


def _write_rollout(tmp_path, records):
    path = tmp_path / "rollout-real.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return str(path)


def _record(timestamp, record_type, payload):
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def test_real_schema_messages_tools_and_compaction(tmp_path):
    records = [
        _record("t0", "session_meta", {"id": "session-1", "cwd": "/repo"}),
        _record("t1", "event_msg", {
            "type": "user_message",
            "message": "Refactor app.py. Constraint: do not modify auth.py. Acceptance criteria: tests must pass.",
        }),
        _record("t2", "response_item", {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "I will preserve auth.py."}],
        }),
        _record("t3", "response_item", {
            "type": "function_call", "name": "shell_command",
            "arguments": json.dumps({"command": "pytest -q"}), "call_id": "call-1",
        }),
        _record("t4", "response_item", {
            "type": "function_call_output", "call_id": "call-1",
            "output": json.dumps({"exit_code": 0, "output": "1 passed"}),
        }),
        _record("t5", "compacted", {
            "message": "Refactoring app.py; tests pass.",
            "replacement_history": [{"type": "message", "role": "user", "content": []}],
        }),
        _record("t6", "event_msg", {"type": "context_compacted"}),
    ]
    lines = rollout.parse_rollout_file(_write_rollout(tmp_path, records))

    assert [line.item_type for line in lines] == [
        "session_meta", "event_msg", "response_item", "response_item",
        "event_msg", "compacted", "event_msg",
    ]
    contract = rollout.extract_task_contract(lines)
    assert contract.objective.startswith("Refactor app.py")
    assert contract.constraints == ["Constraint: do not modify auth.py."]
    assert contract.acceptance_criteria == ["Acceptance criteria: tests must pass."]

    traces = rollout.extract_tool_traces(lines)
    assert len(traces) == 1
    assert traces[0].tool == "shell_command"
    assert traces[0].input == {"command": "pytest -q"}
    assert traces[0].output["exit_code"] == 0

    compactions = rollout.detect_compactions(lines)
    assert len(compactions) == 1
    assert compactions[0].summary == "Refactoring app.py; tests pass."


def test_real_schema_custom_tool_calls_pair_by_call_id(tmp_path):
    records = [
        _record("t1", "response_item", {
            "type": "custom_tool_call", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Update File: auth.py\n+x\n*** End Patch",
            "call_id": "custom-1",
        }),
        _record("t2", "response_item", {
            "type": "custom_tool_call_output", "call_id": "custom-1", "output": "Done!",
        }),
    ]
    lines = rollout.parse_rollout_file(_write_rollout(tmp_path, records))
    traces = rollout.extract_tool_traces(lines)
    assert len(traces) == 1
    assert traces[0].tool == "apply_patch"
    assert traces[0].input["path"] == "auth.py"
    assert traces[0].output == {"output": "Done!"}


def test_real_encrypted_compaction_surfaces_as_unavailable(tmp_path):
    records = [
        _record("t1", "compacted", {
            "message": "",
            "replacement_history": [{"type": "compaction", "encrypted_content": "ciphertext"}],
        }),
        _record("t2", "turn_context", {"summary": "auto"}),
        _record("t3", "event_msg", {"type": "context_compacted"}),
    ]
    lines = rollout.parse_rollout_file(_write_rollout(tmp_path, records))
    compactions = rollout.detect_compactions(lines)
    assert len(compactions) == 1
    assert compactions[0].summary == rollout.UNAVAILABLE_COMPACTION_SUMMARY


def test_legacy_fixture_shape_remains_supported():
    fixture = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_rollout.jsonl")
    lines = rollout.parse_rollout_file(fixture)
    assert rollout.extract_tool_traces(lines)
    assert rollout.detect_compactions(lines)

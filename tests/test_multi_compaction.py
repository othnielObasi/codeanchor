"""Multi-compaction analysis.

Both entry points previously did `event = compactions[0]` and analysed only the
FIRST compaction. That misses the failure mode the upstream issues call worst:
loss COMPOUNDS across repeated compactions, and by the third the model does not
know an earlier one occurred (openai/codex#14347).

The fixture here is the concrete case: the constraint survives compaction 1,
is dropped at compaction 2, and is still gone at compaction 3 -- with a
violation after each of the last two. Analysing only compaction 1 reports
CLEAN and misses both violations entirely.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters import codex_rollout as rollout

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "multi_compaction_rollout.jsonl",
)


def _load():
    lines = rollout.parse_rollout_file(FIXTURE)
    return rollout.extract_task_contract(lines), lines


def test_detects_all_three_compactions():
    _, lines = _load()
    events = rollout.detect_compactions(lines)
    assert len(events) == 3
    assert [e.ordinal for e in events] == [1, 2, 3]
    assert all(e.total_in_session == 3 for e in events)


def test_segment_lines_do_not_span_later_compactions():
    """post_lines spans everything after; segment_lines stops at the next
    compaction. Without this, a violation is counted once per preceding
    compaction."""
    _, lines = _load()
    e1, e2, e3 = rollout.detect_compactions(lines)
    assert len(e1.post_lines) > len(e1.segment_lines)
    assert len(e3.segment_lines) == len(e3.post_lines)  # last one: same
    # trail.py is patched in segment 2 only
    seg1_paths = [t.input.get("path") for t in rollout.extract_tool_traces(e1.segment_lines)]
    seg2_paths = [t.input.get("path") for t in rollout.extract_tool_traces(e2.segment_lines)]
    assert "ledger/audit/trail.py" not in seg1_paths
    assert "ledger/audit/trail.py" in seg2_paths


def test_first_compaction_is_clean_so_analysing_only_it_misses_everything():
    """Regression guard for the original bug."""
    contract, lines = _load()
    results = rollout.analyse_session(contract, lines)
    assert results[0]["drift"] == []
    assert results[0]["violations"] == []


def test_constraint_loss_is_detected_at_the_compaction_where_it_happened():
    contract, lines = _load()
    results = rollout.analyse_session(contract, lines)
    assert len(results[1]["drift"]) == 1
    assert results[1]["drift"][0]["compounding"] is False
    assert results[1]["drift"][0]["first_dropped_at_ordinal"] == 2


def test_repeated_loss_is_marked_compounding():
    contract, lines = _load()
    results = rollout.analyse_session(contract, lines)
    assert results[2]["drift"][0]["compounding"] is True


def test_violations_attribute_to_their_own_segment_and_are_not_double_counted():
    contract, lines = _load()
    results = rollout.analyse_session(contract, lines)
    paths = [[v["violating_input"]["path"] for v in r["violations"]] for r in results]
    assert paths == [[], ["ledger/audit/trail.py"], ["ledger/audit/report.py"]]
    total = sum(len(p) for p in paths)
    assert total == 2, "each violation must be reported exactly once"


def test_single_compaction_session_still_works():
    """Backwards compatibility with the original fixture."""
    single = os.path.join(os.path.dirname(FIXTURE), "sample_rollout.jsonl")
    lines = rollout.parse_rollout_file(single)
    contract = rollout.extract_task_contract(lines)
    results = rollout.analyse_session(contract, lines)
    assert len(results) == 1
    assert results[0]["ordinal"] == 1
    assert results[0]["total_in_session"] == 1
    assert len(results[0]["violations"]) == 1


def test_session_with_no_compaction_returns_empty():
    lines = [rollout.RolloutLine(timestamp="t", item={"type": "session_meta", "id": "x"})]
    contract = rollout.TaskContract(objective="o", constraints=["do not touch x/"])
    assert rollout.analyse_session(contract, lines) == []

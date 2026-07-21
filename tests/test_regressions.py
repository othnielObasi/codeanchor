"""Regression tests for bugs found during Day 2 review.

Each test here corresponds to a specific defect that would have produced a
wrong result in front of judges. Keep them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters import codex_rollout as rollout
from tracememory.adapters.drift_scoring import DeterministicDriftScorer

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "sample_rollout.jsonl")


# --- BUG 1: false-positive violations from permissive clauses -----------------
# "do NOT touch billing/auth/ but you may edit billing/refunds.py" was extracting
# BOTH paths as protected, so editing the explicitly-ALLOWED file got reported as
# a constraint violation.

def test_permissive_clause_paths_are_not_treated_as_protected():
    constraint = "Constraint: do NOT touch billing/auth/ but you may freely edit billing/refunds.py"
    terms = rollout._extract_protected_terms(constraint)
    assert "billing/auth/" in terms
    assert "billing/refunds.py" not in terms


def test_editing_an_explicitly_allowed_path_is_not_reported_as_violation():
    contract = rollout.TaskContract(
        objective="refactor refunds",
        constraints=["do NOT touch billing/auth/ but you may freely edit billing/refunds.py"],
        acceptance_criteria=[],
        raw_instruction="",
    )
    post = [
        rollout.RolloutLine(
            timestamp="t1",
            item={"type": "response_item", "role": "assistant", "content": [
                {"type": "tool_call", "tool_name": "apply_patch", "input": {"path": "billing/refunds.py", "diff": "+x"}}
            ]},
        ),
        rollout.RolloutLine(
            timestamp="t2",
            item={"type": "event_msg", "msg": {"type": "tool_result", "tool_name": "apply_patch", "output": {"status": "applied"}}},
        ),
    ]
    event = rollout.CompactionEvent(index=0, timestamp="t", summary="s", pre_lines=[], post_lines=post)
    assert rollout.find_actual_violations(contract, event) == []


def test_editing_the_protected_path_is_still_reported_as_violation():
    """Guard against over-correcting bug 1 into a false NEGATIVE."""
    contract = rollout.TaskContract(
        objective="refactor refunds",
        constraints=["do NOT touch billing/auth/ but you may freely edit billing/refunds.py"],
        acceptance_criteria=[],
        raw_instruction="",
    )
    post = [
        rollout.RolloutLine(
            timestamp="t1",
            item={"type": "response_item", "role": "assistant", "content": [
                {"type": "tool_call", "tool_name": "apply_patch", "input": {"path": "billing/auth/eligibility.py", "diff": "+x"}}
            ]},
        ),
        rollout.RolloutLine(
            timestamp="t2",
            item={"type": "event_msg", "msg": {"type": "tool_result", "tool_name": "apply_patch", "output": {"status": "applied"}}},
        ),
    ]
    event = rollout.CompactionEvent(index=0, timestamp="t", summary="s", pre_lines=[], post_lines=post)
    violations = rollout.find_actual_violations(contract, event)
    assert len(violations) == 1
    assert "billing/auth/eligibility.py" in violations[0]["violating_input"]["path"]


# --- BUG 2: constraints phrased without a "not" verb were dropped entirely -----

def test_without_modifying_phrasing_is_captured_as_a_constraint():
    text = "Add partial-refund support to billing/refunds.py without modifying billing/auth/ or auth.py"
    constraints = rollout._extract_constraints(text)
    assert len(constraints) >= 1
    assert "without modifying" in constraints[0].lower()


def test_leave_untouched_phrasing_is_captured():
    text = "Refactor the parser. Leave the vendor/ directory untouched."
    constraints = rollout._extract_constraints(text)
    assert any("vendor/" in c for c in constraints)


# --- BUG 3: non-path constraints silently passed as "retained" -----------------
# "do not use regex" extracted no path terms, so the scorer returned retained=True
# and the constraint was never checked at all.

def test_non_path_constraint_absent_from_summary_is_flagged():
    scorer = DeterministicDriftScorer()
    result = scorer.score("Do not use regex for parsing", "Continued work on the tokenizer, tests green.")
    assert result.retained is False
    assert result.freshness_score < 35  # below ContextHealthService exclusion threshold


def test_non_path_constraint_present_in_summary_is_retained():
    scorer = DeterministicDriftScorer()
    result = scorer.score("Do not use regex for parsing", "Avoided regex per the constraint; used a hand-written parser.")
    assert result.retained is True
    assert result.freshness_score >= 35


def test_unverifiable_constraint_is_flagged_not_silently_passed():
    scorer = DeterministicDriftScorer()
    # All stopwords -> nothing checkable. Must NOT silently pass.
    result = scorer.score("do not", "anything at all")
    assert result.retained is False
    assert result.freshness_score < 35


# --- BUG 4: one bad edit reported once per matching constraint -----------------
# After fixing bug 2, both the raw instruction AND the goal text yielded a
# constraint forbidding billing/auth/, so the single offending apply_patch was
# reported twice. Judges should see one violation, listing both constraints.

def test_single_violating_edit_is_reported_once_with_all_breached_constraints():
    contract = rollout.TaskContract(
        objective="o",
        constraints=[
            "do NOT touch billing/auth/",
            "without modifying billing/auth/ or auth.py",
        ],
        acceptance_criteria=[],
        raw_instruction="",
    )
    post = [
        rollout.RolloutLine(
            timestamp="t1",
            item={"type": "response_item", "role": "assistant", "content": [
                {"type": "tool_call", "tool_name": "apply_patch", "input": {"path": "billing/auth/eligibility.py", "diff": "+x"}}
            ]},
        ),
        rollout.RolloutLine(
            timestamp="t2",
            item={"type": "event_msg", "msg": {"type": "tool_result", "tool_name": "apply_patch", "output": {"status": "applied"}}},
        ),
    ]
    event = rollout.CompactionEvent(index=0, timestamp="t", summary="s", pre_lines=[], post_lines=post)
    violations = rollout.find_actual_violations(contract, event)

    assert len(violations) == 1, "one bad edit must not be reported once per constraint"
    assert len(violations[0]["constraints"]) == 2, "both breached constraints should be listed"
    assert violations[0]["constraint"] == violations[0]["constraints"][0]


# --- BUG 5: paths named as the task TARGET were treated as protected ----------
# "Add support to billing/refunds.py without modifying billing/auth/" extracted
# refunds.py as protected -- so editing the file you were ASKED to edit would
# have been reported as a violation.

def test_path_before_prohibition_marker_is_target_not_protected():
    terms = rollout._extract_protected_terms(
        "Add partial-refund support to billing/refunds.py without modifying billing/auth/ or auth.py"
    )
    assert "billing/auth/" in terms
    assert "auth.py" in terms
    assert "billing/refunds.py" not in terms, "the edit target must not be protected"


# --- BUG 6: marker at index 0 reset the prohibition search --------------------
# The sentinel used 0 for "not found", so a marker AT position 0 ("Constraint:")
# caused the scan to jump to the LAST marker instead of the first, losing every
# protected path in the sentence.

def test_marker_at_index_zero_does_not_break_prohibition_search():
    terms = rollout._extract_protected_terms(
        "Constraint: do NOT touch auth.py or anything in billing/auth/ - frozen"
    )
    assert "auth.py" in terms
    assert "billing/auth/" in terms


# --- Constraint dedup ---------------------------------------------------------

def test_equivalent_constraints_are_deduped_to_the_most_specific():
    contract = rollout.extract_task_contract(rollout.parse_rollout_file(FIXTURE))
    assert len(contract.constraints) == 1, "instruction and goal text express the same prohibition"
    assert "do NOT touch" in contract.constraints[0]


# --- BUG 7: markdown treated as flat prose ------------------------------------
# AGENTS.md files contain code fences, repo trees and headers. Parsing them as
# prose produced false-positive protected paths (from a repo-map tree) and
# glued headers onto the constraints beneath them.

AGENTS_SAMPLE = """\
# Project

Some intro text.

## Constraints

Do not restyle the console.

Do not change:

- existing API contracts
- `services/api/app/context_health/` — reused unmodified, including its
  `ctx-stale-block-v1` policy
- the other adapters (`crewai.py`, `langgraph.py`)

Repo map:

```
tests/
  fixtures/sample_rollout.jsonl   do not delete this file
scripts/
  helper.py
```

Do not re-pitch this as "memory"; it will be judged as redundant.
"""


def test_code_fence_contents_are_not_extracted_as_constraints():
    cs = rollout._extract_constraints(AGENTS_SAMPLE)
    terms = {t for c in cs for t in rollout._extract_protected_terms(c)}
    assert "fixtures/sample_rollout.jsonl" not in terms, "paths inside code fences must not be protected"
    assert "scripts/helper.py" not in terms


def test_headers_do_not_merge_into_the_constraint_below_them():
    cs = rollout._extract_constraints(AGENTS_SAMPLE)
    assert not any("Constraints Do not restyle" in c for c in cs)
    assert any(c.startswith("Do not restyle") for c in cs)


def test_soft_wrapped_constraints_are_not_truncated():
    """Splitting on single newlines chopped constraints mid-sentence."""
    cs = rollout._extract_constraints(AGENTS_SAMPLE)
    pitch = [c for c in cs if "re-pitch" in c]
    assert pitch, "constraint missing"
    assert "redundant" in pitch[0], f"truncated at soft wrap: {pitch[0]!r}"


def test_colon_introduced_list_distributes_the_prohibition():
    """'Do not change:' followed by a list -- each item IS a constraint, but only
    the lead-in carries the signal word, so items were dropped and their
    protected paths lost."""
    cs = rollout._extract_constraints(AGENTS_SAMPLE)
    terms = {t for c in cs for t in rollout._extract_protected_terms(c)}
    assert "services/api/app/context_health/" in terms
    assert "crewai.py" in terms, "items after a MULTI-LINE list item must keep the lead-in"
    assert "langgraph.py" in terms


def test_strip_markdown_noise_keeps_inline_code_content():
    """Constraints legitimately name files inline: do not use `border-l-2`."""
    out = rollout.strip_markdown_noise("Do not use `border-l-2` in the panel.")
    assert "border-l-2" in out
    assert "`" not in out

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters.drift_scoring import (
    DeterministicDriftScorer,
    GPT56DriftScorer,
    DriftScore,
)


class FakeGPT56Client:
    """Stands in for RealOpenAIResponsesClient -- no network calls. Lets us prove
    prompt construction and response parsing are correct without api.openai.com
    access. Swap for RealOpenAIResponsesClient(api_key=...) inside Codex."""

    def __init__(self, canned_response: dict):
        self.canned_response = canned_response
        self.last_prompt: str | None = None
        self.last_model: str | None = None

    def create_response(self, model: str, input: str) -> str:
        self.last_model = model
        self.last_prompt = input
        return json.dumps(self.canned_response)


def test_deterministic_scorer_flags_dropped_constraint():
    scorer = DeterministicDriftScorer()
    result = scorer.score(
        "do NOT touch auth.py or anything in billing/auth/",
        "Added partial refund calc, tests passing.",
    )
    assert result.retained is False
    assert result.freshness_score < 35  # below ContextHealthService's ctx-stale-block-v1 threshold


def test_deterministic_scorer_passes_retained_constraint():
    scorer = DeterministicDriftScorer()
    result = scorer.score(
        "do NOT touch auth.py",
        "Continuing work, explicitly avoided touching auth.py per constraint.",
    )
    assert result.retained is True
    assert result.freshness_score >= 35


def test_gpt56_scorer_sends_constraint_and_summary_in_prompt():
    fake = FakeGPT56Client({"retained": False, "confidence": 88, "rationale": "Summary omits the frozen-module constraint entirely."})
    scorer = GPT56DriftScorer(fake, model="gpt-5.6")
    result = scorer.score("do NOT touch billing/auth/", "Fixed rounding bug in refunds.py.")

    assert fake.last_model == "gpt-5.6"
    assert "do NOT touch billing/auth/" in fake.last_prompt
    assert "Fixed rounding bug in refunds.py." in fake.last_prompt

    assert result.retained is False
    assert result.rationale == "Summary omits the frozen-module constraint entirely."
    # high confidence in "not retained" -> low freshness/trust, below the
    # ctx-stale-block-v1 exclusion threshold (35) in ContextHealthService
    assert result.freshness_score < 35
    assert result.trust_score < 35


def test_gpt56_scorer_retained_case_scores_above_exclusion_threshold():
    fake = FakeGPT56Client({"retained": True, "confidence": 92, "rationale": "Constraint still explicitly honored in summary."})
    scorer = GPT56DriftScorer(fake)
    result = scorer.score("do NOT touch auth.py", "Continued work, did not modify auth.py, all tests green.")

    assert result.retained is True
    assert result.freshness_score >= 35
    assert result.trust_score >= 35


def test_gpt56_scorer_low_confidence_not_retained_still_flags_for_review():
    # Even if the model is only 55% sure it's dropped, that should still land
    # below the exclusion threshold so a human/judge sees it flagged rather
    # than silently passing.
    fake = FakeGPT56Client({"retained": False, "confidence": 55, "rationale": "Ambiguous -- summary doesn't mention the module either way."})
    scorer = GPT56DriftScorer(fake)
    result = scorer.score("do NOT touch billing/auth/", "Continued the refund work.")
    assert result.freshness_score < 35


def test_build_drift_candidates_accepts_injected_scorer():
    from tracememory.adapters import codex_rollout as rollout

    contract = rollout.TaskContract(
        objective="test",
        constraints=["do NOT touch billing/auth/"],
        acceptance_criteria=[],
        raw_instruction="",
    )
    event = rollout.CompactionEvent(index=0, timestamp="t", summary="all good, moving on", pre_lines=[], post_lines=[])

    fake = FakeGPT56Client({"retained": False, "confidence": 90, "rationale": "dropped"})
    scorer = GPT56DriftScorer(fake)

    candidates = rollout.build_drift_candidates(contract, event, scorer=scorer)
    post = [c for c in candidates if c["source_type"] == "compaction_summary"][0]
    assert post["metadata"]["constraint_retained"] is False
    assert post["metadata"]["scorer_rationale"] == "dropped"
    assert post["freshness_score"] < 35

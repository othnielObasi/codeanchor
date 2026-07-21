"""Drift-consistency scoring: does a post-compaction summary retain a
pre-compaction constraint?

Two interchangeable scorers implement the same Protocol:

  - DeterministicDriftScorer: keyword/path-overlap heuristic (default; used in
    codex_rollout.build_drift_candidates today). Zero API calls, fully keyless
    -- what the hackathon demo runs with out of the box for judges.

  - GPT56DriftScorer: the real, intended production path. Calls OpenAI's
    Responses API with GPT-5.6 to make an actual semantic-consistency judgment
    ("does this summary still honor this constraint, in substance, even if
    reworded?") instead of a keyword match. This is the ONE place in the whole
    adapter that calls GPT-5.6, per the scope discipline in the PRD/TRD -- it
    is not used for parsing, orchestration, or UI.

Both return a DriftScore so callers (codex_rollout.build_drift_candidates) can
swap scorers without touching Context Health integration code.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol


@dataclass
class DriftScore:
    retained: bool
    freshness_score: int  # 0-100, fed straight into TraceMemory's ContextCandidate
    trust_score: int      # 0-100
    rationale: str


class DriftScorer(Protocol):
    def score(self, constraint: str, post_compaction_summary: str) -> DriftScore: ...


# --- Deterministic (current default, keyless) ---------------------------------

class DeterministicDriftScorer:
    """Path/keyword overlap check. Same logic as codex_rollout._constraint_retained,
    exposed here as a proper Scorer so it's swappable with GPT56DriftScorer."""

    # Words too generic to prove a constraint survived compaction.
    _STOPWORDS = frozenset(
        "the a an and or of to in on for with do not don't must should be is are "
        "that this it any anything all touch use using edit change modify constraint "
        "you your we our may can will would".split()
    )

    def score(self, constraint: str, post_compaction_summary: str) -> DriftScore:
        from .codex_rollout import _extract_protected_terms, _prohibitive_clause

        summary_low = post_compaction_summary.lower()
        terms = _extract_protected_terms(constraint)

        if terms:
            retained = any(t in summary_low for t in terms)
            if retained:
                return DriftScore(True, 90, 90, "Protected path from constraint is referenced in the summary.")
            return DriftScore(False, 15, 20, "Protected path from constraint is absent from the compaction summary.")

        # No path-shaped term (e.g. "do not use regex"). Fall back to content-word
        # overlap on the prohibitive clause rather than silently passing.
        clause_words = {
            w.strip(".,;:!?()") for w in _prohibitive_clause(constraint).lower().split()
        }
        content_words = {w for w in clause_words if w and w not in self._STOPWORDS and len(w) > 2}

        if not content_words:
            # Nothing checkable at all -> flag for review, never silently pass.
            return DriftScore(
                False, 30, 30,
                "Constraint could not be verified deterministically; flagged for review "
                "(use GPT56DriftScorer for semantic checking).",
            )

        if content_words & set(summary_low.replace(".", " ").replace(",", " ").split()):
            return DriftScore(True, 80, 80, "Constraint subject matter is referenced in the summary.")
        return DriftScore(
            False, 20, 25,
            "Constraint subject matter is absent from the compaction summary.",
        )


# --- GPT-5.6-backed (production path) ------------------------------------------

class OpenAIResponsesClient(Protocol):
    """Minimal shape needed from an OpenAI client -- lets tests substitute a fake
    without pulling in the real `openai` package as a hard dependency here."""

    def create_response(self, model: str, input: str) -> str: ...


class RealOpenAIResponsesClient:
    """Thin wrapper over the actual OpenAI Responses API. Requires OPENAI_API_KEY
    in the environment. Not exercised in this sandbox (api.openai.com isn't on
    the allowed network list here) -- wire and smoke-test this inside Codex."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

    def create_response(self, model: str, input: str) -> str:
        import urllib.request

        payload = json.dumps({"model": model, "input": input}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # Responses API: output text lives under output[].content[].text
        for item in body.get("output", []):
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block["text"]
        raise ValueError(f"Unexpected response shape: {body}")


DRIFT_SCORING_PROMPT = """You are checking whether a coding-agent's post-compaction \
summary still honors a specific constraint from the original task, even if reworded.

Constraint: {constraint}

Post-compaction summary: {summary}

Respond with ONLY a JSON object, no other text:
{{"retained": true|false, "confidence": 0-100, "rationale": "one sentence"}}"""


class DriftScoreParseError(ValueError):
    """GPT-5.6 returned something we can't trust. Callers must treat this as
    'unverified' and flag for review -- never as 'constraint retained'."""


def parse_drift_response(raw: str) -> tuple[bool, int, str]:
    """Parse and VALIDATE a drift-scoring response.

    A bare json.loads() is not enough. Codex/OpenAI structured output is known to
    degrade to markdown-fenced or unfenced JSON when tools are active
    (openai/codex#15451), and a model can omit fields. Both crashed the previous
    implementation -- JSONDecodeError and KeyError respectively -- which would
    have taken down the live path on first contact.

    Returns (retained, confidence, rationale). Raises DriftScoreParseError if the
    payload can't be validated.
    """
    import json
    import re

    if raw is None or not str(raw).strip():
        raise DriftScoreParseError("empty response")

    text = str(raw).strip()

    # Strip markdown fences: ```json ... ``` or ``` ... ```
    fence = re.match(r"(?s)^\s*```[a-zA-Z]*\s*(.*?)\s*```\s*$", text)
    if fence:
        text = fence.group(1).strip()

    # Fall back to the first balanced-looking JSON object in the text.
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise DriftScoreParseError(f"no JSON object found in response: {text[:120]!r}")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise DriftScoreParseError(f"malformed JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise DriftScoreParseError(f"expected a JSON object, got {type(payload).__name__}")

    # --- schema ---
    if "retained" not in payload:
        raise DriftScoreParseError("missing required field 'retained'")

    retained = payload["retained"]
    if isinstance(retained, str):
        low = retained.strip().lower()
        if low in ("true", "yes"):
            retained = True
        elif low in ("false", "no"):
            retained = False
        else:
            raise DriftScoreParseError(f"'retained' not boolean-like: {retained!r}")
    if not isinstance(retained, bool):
        raise DriftScoreParseError(f"'retained' must be a boolean, got {type(retained).__name__}")

    confidence = payload.get("confidence", 50)
    try:
        confidence = int(float(confidence))
    except (TypeError, ValueError):
        raise DriftScoreParseError(f"'confidence' not numeric: {confidence!r}")
    confidence = max(0, min(100, confidence))  # clamp rather than reject

    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = str(rationale)

    return retained, confidence, rationale.strip()


class GPT56DriftScorer:
    """Calls GPT-5.6 to make a semantic (not keyword) consistency judgment."""

    def __init__(self, client: OpenAIResponsesClient, model: str = "gpt-5.6"):
        self.client = client
        self.model = model

    def score(self, constraint: str, post_compaction_summary: str) -> DriftScore:
        prompt = DRIFT_SCORING_PROMPT.format(constraint=constraint, summary=post_compaction_summary)
        try:
            raw = self.client.create_response(model=self.model, input=prompt)
            retained, confidence, rationale = parse_drift_response(raw)
        except DriftScoreParseError as exc:
            # Unparseable == unverified. Per the project's standing rule, an
            # unverifiable result surfaces for review; it never passes silently.
            return DriftScore(
                False, freshness_score=30, trust_score=30,
                rationale=f"Constraint could not be verified: model response invalid ({exc}). Flagged for review.",
            )
        except Exception as exc:  # transport/API failure
            return DriftScore(
                False, freshness_score=30, trust_score=30,
                rationale=f"Constraint could not be verified: scoring call failed ({type(exc).__name__}). Flagged for review.",
            )

        if retained:
            # High confidence retained -> high freshness/trust; scale down if the
            # model itself is unsure, so borderline cases still get flagged for
            # human review rather than silently passing.
            score_val = max(50, min(95, confidence))
            return DriftScore(True, freshness_score=score_val, trust_score=score_val, rationale=rationale)

        # Cap at 34 (Context Health excludes below 35) so any "not retained"
        # verdict always surfaces for review, even at low model confidence --
        # a low-confidence drift flag is still safer than a silent pass.
        score_val = max(5, min(34, 100 - confidence))
        return DriftScore(False, freshness_score=score_val, trust_score=score_val, rationale=rationale)


def build_scorer_from_env() -> DriftScorer:
    """Picks GPT-5.6 scoring if OPENAI_API_KEY is present, otherwise falls back to
    the deterministic scorer -- this is what keeps the demo keyless for judges
    while still being the real production path when credentials are available."""
    if os.environ.get("OPENAI_API_KEY"):
        return GPT56DriftScorer(RealOpenAIResponsesClient())
    return DeterministicDriftScorer()

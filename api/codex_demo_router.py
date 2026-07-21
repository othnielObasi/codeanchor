"""Codex recovery demo endpoint — powers the "Codex Recovery" console tab.

Mount alongside the existing routers in services/api/app:

    from app.codex_demo.router import router as codex_demo_router
    app.include_router(codex_demo_router)

Adds ONE endpoint, mirroring the existing /api/demo/failure-recovery pattern.
No new tables, no schema changes, no modification to existing routes.

Keyless by default: replays the shipped Codex rollout fixture through the real
adapter + real ContextHealthService, so judges can run the whole story with no
OpenAI key and no live Codex session. Set OPENAI_API_KEY to swap the
deterministic drift scorer for GPT-5.6 (see adapters/drift_scoring.py).
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/demo", tags=["codex-demo"])


class CodexDemoRequest(BaseModel):
    session_path: str | None = None  # defaults to the bundled fixture


def _fixture_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "fixtures", "sample_rollout.jsonl")


@router.post("/codex-recovery")
def codex_recovery(payload: CodexDemoRequest | None = None) -> dict[str, Any]:
    from app.context_health.schemas import ContextBuildRequest, ContextCandidate
    from app.context_health.service import ContextHealthService
    from tracememory.adapters import codex_rollout as rollout
    from tracememory.adapters.drift_scoring import build_scorer_from_env

    path = (payload.session_path if payload else None) or _fixture_path()

    lines = rollout.parse_rollout_file(path)
    contract = rollout.extract_task_contract(lines)
    compactions = rollout.detect_compactions(lines)

    if not compactions:
        return {
            "session": os.path.basename(path),
            "objective": contract.objective,
            "constraints": contract.constraints,
            "protected_paths": sorted({t for c in contract.constraints for t in rollout._extract_protected_terms(c)}),
            "compaction": None,
            "drift": [],
            "violations": [],
            "timeline": [["No compaction in this session", "Nothing to verify — drift detection needs a compaction event."]],
        }

    scorer = build_scorer_from_env()
    # Analyse every compaction; loss compounds across repeated compactions.
    per_compaction = rollout.analyse_session(contract, lines, scorer=scorer)
    event = compactions[0]
    candidates = rollout.build_drift_candidates(contract, event, scorer=scorer)

    service = ContextHealthService()
    response, _receipt = service.build_context(
        ContextBuildRequest(
            task=contract.objective,
            agent_type="codex_session",
            candidate_context=[ContextCandidate(**c) for c in candidates],
        )
    )

    drift = [
        {
            "policy_id": d.policy_id,
            "rationale": (d.metadata or {}).get("scorer_rationale") or d.reason,
        }
        for d in response.decisions
        if d.policy_status == "excluded" and d.source_type == "compaction_summary"
    ]

    violations = [
        {
            "tool": v["violating_tool"],
            "path": v["violating_input"].get("path") or v["violating_input"].get("command", ""),
            "at": v["timestamp"],
            "constraint": v["constraint"],
            "at_compaction": rec["ordinal"],
        }
        for rec in per_compaction
        for v in rec["violations"]
    ]

    traces = rollout.extract_tool_traces(lines)
    timeline = [
        ["Task contract captured", f"objective + {len(contract.constraints)} constraint(s) extracted from the opening instruction"],
        ["Tool evidence recorded", f"{len(traces)} tool call(s) traced ({', '.join(t.tool for t in traces)})"],
        ["Checkpoint saved", "pre-compaction state stored, safe_to_resume=true"],
        ["Compaction detected", "explicit Compacted item in the rollout - not inferred"],
    ]
    if drift:
        timeline.append(["Drift flagged", drift[0]["rationale"]])
    if violations:
        timeline.append(["Violation confirmed", f"post-resume {violations[0]['tool']} touched {violations[0]['path']}"])
    timeline.append(["Recovery brief issued", "original constraint restored for the resumed session"])

    return {
        "session": os.path.basename(path),
        "objective": contract.objective,
        "constraints": contract.constraints,
        "protected_paths": sorted({t for c in contract.constraints for t in rollout._extract_protected_terms(c)}),
        "compaction": {"at": event.timestamp, "summary": event.summary},
        "compaction_count": len(compactions),
        "compactions": [
            {
                "ordinal": rec["ordinal"],
                "at": rec["at"],
                "summary": rec["summary"],
                "drift": rec["drift"],
                "violations": [
                    {"tool": v["violating_tool"], "path": v["violating_input"].get("path", "")}
                    for v in rec["violations"]
                ],
            }
            for rec in per_compaction
        ],
        "drift": drift,
        "violations": violations,
        "timeline": timeline,
        "scorer": type(scorer).__name__,
    }

"""TraceMemory adapter for Codex CLI sessions.

Structurally parallel to tracememory/adapters/openai_agents.py, langgraph.py,
and crewai.py -- same TraceMemoryClient surface, no new API endpoints, no new
data model. Only emits TraceMemory's existing required run-event vocabulary:
request_received, plan_prepared, trace_recorded, checkpoint_saved,
interruption_detected, checkpoint_restored, task_modified, final_answer.
"""
from __future__ import annotations

from typing import Any, Optional

from ..client import TraceMemoryClient
from . import codex_rollout as rollout


class TraceMemoryCodexAdapter:
    def __init__(self, client: TraceMemoryClient, task_id: str, session_path: str, scorer=None, repo_path: str | None = None):
        self.client = client
        self.task_id = task_id
        self.session_path = session_path
        self.scorer = scorer  # None => deterministic keyless scorer (see codex_rollout.build_drift_candidates)
        # Optional git working tree. When present, rollout claims are
        # cross-checked against actual repository state (see git_inspect.py).
        self.inspector = None
        if repo_path:
            from .git_inspect import GitError, GitInspector

            try:
                self.inspector = GitInspector(repo_path)
            except GitError:
                self.inspector = None  # not a repo -> degrade to rollout-only
        self._lines: list[rollout.RolloutLine] = []
        self._contract: rollout.TaskContract | None = None

    # -- ingestion --------------------------------------------------------

    def ingest_session(self) -> dict[str, Any]:
        self._lines = rollout.parse_rollout_file(self.session_path)
        self._contract = rollout.extract_task_contract(self._lines)
        if self.inspector:
            from .git_inspect import session_start_ref_from_lines

            self.inspector.set_session_start_ref(session_start_ref_from_lines(self._lines))

        self.client.record_event(
            self.task_id,
            "request_received",
            {"framework": "codex", "instruction": self._contract.raw_instruction},
        )
        self.client.record_event(
            self.task_id,
            "plan_prepared",
            {
                "framework": "codex",
                "objective": self._contract.objective,
                "constraints": self._contract.constraints,
                "acceptance_criteria": self._contract.acceptance_criteria,
            },
        )

        for trace in rollout.extract_tool_traces(self._lines):
            self.client.record_tool_trace(
                self.task_id,
                tool=trace.tool,
                input=trace.input,
                output=trace.output,
                tool_type=trace.tool_type,
            )
            self.client.record_event(self.task_id, "trace_recorded", {"tool": trace.tool, "timestamp": trace.timestamp})

        return {
            "contract": self._contract,
            "compactions": rollout.detect_compactions(self._lines),
        }

    # -- compaction / drift handling ---------------------------------------

    def handle_compaction(self, event: rollout.CompactionEvent) -> dict[str, Any]:
        assert self._contract is not None, "call ingest_session() first"

        checkpoint = self.client.save_checkpoint(
            self.task_id,
            checkpoint_name=f"pre_compaction_{event.index}",
            state={
                "objective": self._contract.objective,
                "constraints": self._contract.constraints,
                "acceptance_criteria": self._contract.acceptance_criteria,
                "tool_traces": [t.__dict__ for t in rollout.extract_tool_traces(event.pre_lines)],
            },
            resume_state={"compaction_index": event.index},
            safe_to_resume=True,
        )
        self.client.record_event(
            self.task_id, "interruption_detected", {"reason": "compaction", "summary": event.summary}
        )

        candidates = rollout.build_drift_candidates(self._contract, event, scorer=self.scorer)
        context_result = self.client.build_context(
            task=self._contract.objective,
            candidate_context=candidates,
            agent_type="codex_session",
        )

        violations = rollout.find_actual_violations(self._contract, event)

        # Independent second source: git. Confirms claimed violations and adds
        # protected-path changes the session log never reported.
        from .git_inspect import verify_violations

        protected = sorted({t for c in self._contract.constraints for t in rollout._extract_protected_terms(c)})
        violations = verify_violations(violations, self.inspector, protected)

        return {
            "checkpoint": checkpoint,
            "context_health": context_result,
            "actual_violations": violations,
        }

    # -- recovery -----------------------------------------------------------

    def generate_recovery_brief(self, checkpoint_id: str, drift_result: dict[str, Any]) -> str:
        assert self._contract is not None
        restored = self.client.recover_task(checkpoint_id)
        self.client.record_event(self.task_id, "checkpoint_restored", {"checkpoint_id": checkpoint_id})

        lines = [
            f"Objective: {self._contract.objective}",
            "Constraints:",
            *[f"  - {c}" for c in self._contract.constraints],
            "Acceptance criteria:",
            *[f"  - {a}" for a in self._contract.acceptance_criteria],
        ]
        flagged = [
            d for d in drift_result["context_health"].get("decisions", [])
            if d.get("policy_status") == "excluded" and d.get("source_type") == "compaction_summary"
        ]
        if flagged:
            lines.append("Flagged drift (compaction summary did not retain a constraint):")
            for d in flagged:
                rationale = (d.get("metadata") or {}).get("scorer_rationale") or d.get("reason")
                lines.append(f"  - {rationale}")
        if drift_result["actual_violations"]:
            lines.append("Confirmed violations after resume:")
            for v in drift_result["actual_violations"]:
                ev = v.get("evidence", "rollout-only")
                label = {
                    "rollout+git": "session log + git agree",
                    "rollout-only": "session log only, git could not confirm",
                    "git-only": "git only - NOT recorded in the session log",
                }.get(ev, ev)
                path = v["violating_input"].get("path", "")
                lines.append(f"  - {v['violating_tool']} touched {path} [{label}]")
                lines.append(f"      violating: {v['constraint']}")
        lines.append(f"Restored state reference: {restored}")
        return "\n".join(lines)

    # -- receipt --------------------------------------------------------------

    def generate_receipt(self) -> dict[str, Any]:
        events = self.client.list_events(self.task_id)
        return {
            "task_id": self.task_id,
            "contract": self._contract.__dict__ if self._contract else {},
            "events": events,
        }

import json
import urllib.request
from typing import Any, Dict, Iterator, Optional


class TraceMemoryClient:
    """API-first client for TraceMemory production runtime state.

    The SDK intentionally mirrors the infrastructure primitives: runs, events,
    tool traces, checkpoints, recovery, idempotent actions, and approved memory.
    """

    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any] | list[Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}

    def start_run(self, agent_id: str, task: str, dataset_type: str = "support_tickets", **kwargs: Any) -> Dict[str, Any]:
        return self._request("POST", "/api/tasks/run", {"agent_id": agent_id, "task_description": task, "dataset_type": dataset_type, **kwargs})  # type: ignore[return-value]

    def record_event(self, task_id: str, code: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("POST", f"/api/runs/{task_id}/events", {"code": code, "payload": payload or {}})  # type: ignore[return-value]

    def record_tool_trace(
        self,
        task_id: str,
        tool: str,
        input: Dict[str, Any],
        output: Dict[str, Any],
        validation: Optional[Dict[str, Any]] = None,
        observed_signals: Optional[Dict[str, Any]] = None,
        tool_type: str = "read",
        checkpoint_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request("POST", f"/api/runs/{task_id}/tool-traces", {
            "tool": tool,
            "tool_type": tool_type,
            "input": input,
            "output": output,
            "validation": validation or {},
            "observed_signals": observed_signals or {},
            "checkpoint_id": checkpoint_id,
            "trace_id": trace_id,
            "idempotency_key": idempotency_key,
        })  # type: ignore[return-value]

    def save_checkpoint(
        self,
        task_id: str,
        checkpoint_name: str,
        state: Dict[str, Any],
        resume_state: Optional[Dict[str, Any]] = None,
        safe_to_resume: bool = True,
        requires_human_review: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._request("POST", f"/api/runs/{task_id}/checkpoints", {
            "checkpoint_name": checkpoint_name,
            "state": state,
            "resume_state": resume_state or {},
            "safe_to_resume": safe_to_resume,
            "requires_human_review": requires_human_review,
            "metadata": metadata or {},
        })  # type: ignore[return-value]

    def restore_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/checkpoints/{checkpoint_id}/restore", {})  # type: ignore[return-value]

    def recover_task(self, checkpoint_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self._request("POST", "/api/tasks/recover", {"checkpoint_id": checkpoint_id, **kwargs})  # type: ignore[return-value]

    def modify_task(self, task_id: str, new_task_description: str, modification: str, parent_checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/modify", {"new_task_description": new_task_description, "modification": modification, "parent_checkpoint_id": parent_checkpoint_id})  # type: ignore[return-value]

    def approve_memory(self, task_id: str, rule: str, applies_to: list[str], confidence: float = 0.8, evidence: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        return self._request("POST", f"/api/runs/{task_id}/memory/approve", {"rule": rule, "applies_to": applies_to, "confidence": confidence, "evidence": evidence or {}, **kwargs})  # type: ignore[return-value]

    def execute_action(self, task_id: str, tool_name: str, idempotency_key: str, input: Optional[Dict[str, Any]] = None, tool_type: str = "external_action") -> Dict[str, Any]:
        return self._request("POST", f"/api/runs/{task_id}/actions/execute", {"tool_name": tool_name, "tool_type": tool_type, "idempotency_key": idempotency_key, "input": input or {}})  # type: ignore[return-value]

    def list_events(self, task_id: str) -> list[Any]:
        return self._request("GET", f"/api/runs/{task_id}/events")  # type: ignore[return-value]

    def stream_events(self, task_id: str) -> Iterator[Dict[str, Any]]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/api/runs/{task_id}/stream", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if line.startswith("data: "):
                    yield json.loads(line[len("data: "):])

    def generate_plan(self, task: str, run_events: Optional[list[str]] = None, checkpoint_id: Optional[str] = None, task_version: int = 1) -> Dict[str, Any]:
        return self._request("POST", "/api/ai/plan", {"task_description": task, "run_events": run_events or [], "checkpoint_id": checkpoint_id, "task_version": task_version})  # type: ignore[return-value]

    def synthesize_run_summary(self, text: str, voice_id: Optional[str] = None, run_id: Optional[str] = None, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        return self._request("POST", "/api/voice/run-summary", {"text": text, "voice_id": voice_id, "run_id": run_id, "checkpoint_id": checkpoint_id})  # type: ignore[return-value]


    def build_context(
        self,
        task: str,
        candidate_context: list[Dict[str, Any]],
        agent_type: str = "external_agent",
        token_budget: int = 12000,
        persist_receipt: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Build a clean context bundle and Context Health receipt for an agent step."""
        payload = {
            "task": task,
            "agent_type": agent_type,
            "token_budget": token_budget,
            "candidate_context": candidate_context,
            "persist_receipt": persist_receipt,
            **kwargs,
        }
        return self._request("POST", "/api/context-health/build", payload)  # type: ignore[return-value]

    def get_context_receipts(self) -> Dict[str, Any] | list[Any]:
        """Return persisted/recent context receipts exposed by the TraceMemory API."""
        return self._request("GET", "/api/context-health/receipts")

    def partner_status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/partners/status")  # type: ignore[return-value]

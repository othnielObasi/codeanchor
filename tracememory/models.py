from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class RunEvent:
    code: str
    payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolTrace:
    tool: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    validation: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Checkpoint:
    checkpoint_name: str
    state: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ApprovedMemory:
    rule: str
    applies_to: List[str]
    confidence: float = 0.8
    evidence: Optional[Dict[str, Any]] = None

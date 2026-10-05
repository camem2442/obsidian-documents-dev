"""JobRuntimeState dataclass for transient execution state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class JobRuntimeState:
    job_id: str
    task: Optional[Dict[str, Any]] = None
    queue: Optional[Dict[str, Any]] = None
    queue_history: List[Dict[str, Any]] = field(default_factory=list)
    ai_log: List[Dict[str, Any]] = field(default_factory=list)
    ai_progress: Dict[str, Any] = field(default_factory=dict)
    sample_review: Dict[str, Any] = field(default_factory=dict)
    bulk_approval: Dict[str, Any] = field(default_factory=dict)
    evaluation: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JobRuntimeState:
        if not isinstance(data.get("sample_review", {}), dict):
            raise ValueError("sample_review must be an object")
        if not isinstance(data.get("bulk_approval", {}), dict):
            raise ValueError("bulk_approval must be an object")
        if not isinstance(data.get("evaluation", {}), dict):
            raise ValueError("evaluation must be an object")
        return cls(
            job_id=data.get("job_id", ""),
            task=data.get("task"),
            queue=data.get("queue"),
            queue_history=list(data.get("queue_history", [])),
            ai_log=list(data.get("ai_log", [])),
            ai_progress=dict(data.get("ai_progress", {})),
            sample_review=dict(data.get("sample_review", {})),
            bulk_approval=dict(data.get("bulk_approval", {})),
            evaluation=dict(data.get("evaluation", {})),
            schema_version=int(data.get("schema_version", 1)),
        )

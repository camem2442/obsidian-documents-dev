"""JobRuntimeRepository for runtime.json persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..domain.job_runtime import JobRuntimeState
from .json_io import atomic_json_save


class JobRuntimeRepository:
    def __init__(self, filename: str = "runtime.json"):
        self.filename = filename

    def load(self, job_dir: Path, job_id: str = "") -> JobRuntimeState:
        path = job_dir / self.filename
        if not path.is_file():
            return JobRuntimeState(job_id=job_id or job_dir.name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return JobRuntimeState.from_dict(data)
        except (ValueError, TypeError, AttributeError) as exc:
            # Never replace unreadable persisted execution/review history with defaults.
            raise ValueError("runtime.json을 읽을 수 없습니다. 기존 기록을 보존하고 파일을 확인하세요.") from exc

    def save(self, job_dir: Path, state: JobRuntimeState | Dict[str, Any]) -> None:
        if isinstance(state, JobRuntimeState):
            data = state.to_dict()
        else:
            data = dict(state)
        data["schema_version"] = 1
        path = job_dir / self.filename
        atomic_json_save(path, data)

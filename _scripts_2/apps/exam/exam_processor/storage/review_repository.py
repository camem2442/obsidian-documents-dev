"""ReviewRepository for ReviewState persistence."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List

from ..domain.review_state import ReviewState


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-rev-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class ReviewRepository:
    def review_dir(self, job_dir: Path) -> Path:
        return job_dir / "review"

    def get(self, job_dir: Path, record_id: str) -> ReviewState:
        path = self.review_dir(job_dir) / f"{record_id}.json"
        if not path.is_file():
            return ReviewState(record_id=record_id, applies_to_revision_id="")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ReviewState.from_dict(data)

    def save(self, job_dir: Path, state: ReviewState) -> None:
        if not state.record_id:
            raise ValueError("Cannot save ReviewState without record_id")
        new_state_rev = uuid.uuid4().hex
        data = state.to_dict()
        data["state_revision_id"] = new_state_rev
        path = self.review_dir(job_dir) / f"{state.record_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json_save(path, data)
        state.state_revision_id = new_state_rev

    def get_many(self, job_dir: Path, record_ids: List[str]) -> Dict[str, ReviewState]:
        results = {}
        for rid in record_ids:
            results[rid] = self.get(job_dir, rid)
        return results

    def save_many(self, job_dir: Path, states: List[ReviewState]) -> None:
        for st in states:
            self.save(job_dir, st)

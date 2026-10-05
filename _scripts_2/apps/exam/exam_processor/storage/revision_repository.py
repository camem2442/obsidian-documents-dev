"""RevisionRepository for immutable snapshot bundles."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from ..domain.review_state import ReviewState


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-snap-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class RevisionRepository:
    def revisions_dir(self, job_dir: Path, record_id: str) -> Path:
        p = job_dir / "revisions" / record_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save_snapshot(
        self,
        job_dir: Path,
        record_id: str,
        revision_id: str,
        record: CanonicalQuestionRecord,
        review_state: ReviewState,
    ) -> None:
        if not record_id or not revision_id:
            return
        snapshot = {
            "snapshot_version": 1,
            "record_id": record_id,
            "revision_id": revision_id,
            "canonical": record.to_dict(),
            "review_state": review_state.to_dict(),
        }
        path = self.revisions_dir(job_dir, record_id) / f"{revision_id}.json"
        if not path.exists():
            _atomic_json_save(path, snapshot)

    def get_snapshot(
        self, job_dir: Path, record_id: str, revision_id: str
    ) -> Optional[Tuple[CanonicalQuestionRecord, ReviewState]]:
        path = self.revisions_dir(job_dir, record_id) / f"{revision_id}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        canonical_data = data.get("canonical") or data.get("record")
        review_data = data.get("review_state", {})
        if not canonical_data:
            return None
        record = CanonicalQuestionRecord.from_dict(canonical_data)
        review_state = ReviewState.from_dict(review_data)
        return record, review_state

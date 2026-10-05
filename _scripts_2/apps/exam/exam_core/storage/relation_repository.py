"""RelationRepository for Knowledge Relations persistence.

Storage layout:
    library/kice/links/{question_id}.json

Invariants:
    1. Independent from job records and master records.
    2. Optimistic concurrency control via relation_revision.
    3. Atomic JSON persistence via temporary file replacement.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from _scripts_2.apps.exam.exam_core.domain.relation_models import QuestionLinks


class RelationConcurrencyError(ValueError):
    """Raised when an expected_relation_revision mismatch occurs."""
    pass


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-link-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class RelationRepository:
    """Repository for managing knowledge relations under library/kice/links/."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    @property
    def library_root(self) -> Path:
        return self._library_root

    @property
    def links_dir(self) -> Path:
        return self._library_root / "links"

    def get(self, question_id: str) -> Optional[QuestionLinks]:
        path = self.links_dir / f"{question_id}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return QuestionLinks.from_dict(data)

    def get_or_create(self, question_id: str) -> QuestionLinks:
        links = self.get(question_id)
        if links is None:
            links = QuestionLinks(question_id=question_id)
        return links

    def exists(self, question_id: str) -> bool:
        return (self.links_dir / f"{question_id}.json").is_file()

    def save(self, links: QuestionLinks, expected_revision: Optional[str] = None) -> str:
        """Save knowledge relations with optimistic locking check."""
        question_id = links.question_id
        if not question_id:
            raise ValueError("Cannot save QuestionLinks without question_id")

        path = self.links_dir / f"{question_id}.json"

        if expected_revision is not None:
            if path.is_file():
                current_data = json.loads(path.read_text(encoding="utf-8"))
                current_rev = current_data.get("relation_revision", "")
                if current_rev != expected_revision:
                    raise RelationConcurrencyError(
                        f"Relation revision conflict for '{question_id}': "
                        f"expected '{expected_revision}', found '{current_rev}'"
                    )
            else:
                raise RelationConcurrencyError(
                    f"Relation revision conflict for '{question_id}': "
                    f"expected '{expected_revision}', but record does not exist on disk"
                )

        data = links.to_dict()
        _atomic_json_save(path, data)
        return links.relation_revision

    def delete(self, question_id: str) -> bool:
        path = self.links_dir / f"{question_id}.json"
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_ids(self) -> List[str]:
        if not self.links_dir.exists():
            return []
        ids = []
        for p in self.links_dir.glob("*.json"):
            if not p.name.startswith("."):
                ids.append(p.stem)
        return sorted(ids)

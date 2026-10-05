"""RecordService — canonical-native content mutation for CanonicalQuestionRecord.

All content mutations MUST go through this service. They:
  1. Load the current CanonicalQuestionRecord from RecordRepository.
  2. Snapshot the CURRENT revision to RevisionRepository (immutable history).
  3. Apply the mutation to the Canonical record.
  4. Generate a new provenance.revision_id (UUID hex).
  5. Reset review.human_approval to "pending".
  6. Invalidate audit / audit_waiver in ReviewState (audit applies only to the
     revision it was run against).
  7. Save the updated CanonicalQuestionRecord via RecordRepository.
  8. Save the updated ReviewState via ReviewRepository.
  9. Propagate dependent invalidation (passage → its questions, script → its
     questions) using the same steps above.

Review-only mutations (note, skip, waiver, AI run state) MUST NOT use this
service; those go through ReviewService, which does NOT touch
provenance.revision_id.
"""
from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import CanonicalQuestionRecord
from ..review_state import ReviewState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_revision_id() -> str:
    return uuid.uuid4().hex


def _content_invalidate(
    record: CanonicalQuestionRecord,
    review_state: ReviewState,
) -> str:
    """Bump revision_id, reset approval, record history, and mark audit obsolete.

    Returns the OLD revision_id (useful for snapshot key).
    """
    old_revision_id = record.provenance.revision_id

    # Record history entry in ReviewState for UI/legacy parity
    history_entry = {
        "revision": old_revision_id,
        "body": record.body,
        "solution": record.solution,
        "answer": record.question.answer or "",
        "points": record.question.points,
        "correct_rate": record.question.correct_rate,
        "unit": record.unit.display_name or "",
        "q_type": record.question.question_type or "",
        "regions": [r for r in record.provenance.source_regions if r.get("content_role") == "body"],
        "solution_regions": [r for r in record.provenance.source_regions if r.get("content_role") == "solution"],
        "assets": [{"path": a.path, "section": a.section, "id": a.asset_id} for a in record.assets],
        "review": record.review.human_approval,
        "audit": copy.deepcopy(review_state.audit),
        "audit_waiver": copy.deepcopy(review_state.audit_waiver),
        "selected_solution_id": review_state.selected_solution_id,
        "solution_link_backup": copy.deepcopy(review_state.solution_link_backup),
        "transcription_pending": list(review_state.transcription_pending),
        "modality": record.question.modality,
        "question_set_id": record.question.question_set_id,
        "listening_script_id": (
            record.relations.listening_script_ids[0]
            if record.relations.listening_script_ids else ""
        ),
        "strategy_tags": list(record.question.strategy_tags),
        "content_type": record.question.content_type,
    }
    review_state.history.append(history_entry)

    new_rev = _new_revision_id()
    record.provenance.revision_id = new_rev
    record.review.human_approval = "pending"
    record.review.source_audit = "pending"
    # Audit and waiver reference the old revision — they are now obsolete
    # but we intentionally leave the data in ReviewState (still visible in
    # the UI) so the user can see what was there. The approval gate in
    # Store.review() / ReviewService will refuse to approve until a new
    # audit passes for the NEW revision.
    return old_revision_id


# ---------------------------------------------------------------------------
# RecordService
# ---------------------------------------------------------------------------

class RecordService:
    """Canonical-native content mutation service.

    Dependencies are injected so that callers (Store) can share the same
    repository instances already constructed in Store.__init__().

    Usage::

        svc = RecordService(record_repo, review_repo, revision_repo)
        record, review_state = svc.update_content(
            job_dir, record_id, expected_revision_id, {"body": "...", "points": 3}
        )
    """

    def __init__(self, record_repo, review_repo, revision_repo):
        self._records = record_repo
        self._reviews = review_repo
        self._revisions = revision_repo

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(
        self, job_dir: Path, record_id: str
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        record = self._records.get(job_dir, record_id)
        if record is None:
            raise ValueError(f"Record not found: {record_id}")
        review_state = self._reviews.get(job_dir, record_id)
        if review_state is None:
            review_state = ReviewState(
                record_id=record_id,
                applies_to_revision_id=record.provenance.revision_id,
            )
        return record, review_state

    def _snapshot_and_invalidate(
        self,
        job_dir: Path,
        record: CanonicalQuestionRecord,
        review_state: ReviewState,
    ) -> str:
        """Save pre-mutation snapshot, then bump revision. Returns old revision_id."""
        old_rev = record.provenance.revision_id
        # Save immutable snapshot of the state BEFORE mutation
        self._revisions.save_snapshot(
            job_dir,
            record.question.question_id,
            old_rev,
            record,
            review_state,
        )
        _content_invalidate(record, review_state)
        review_state.applies_to_revision_id = record.provenance.revision_id
        return old_rev

    def _commit(
        self,
        job_dir: Path,
        record: CanonicalQuestionRecord,
        review_state: ReviewState,
    ) -> None:
        self._records.save(job_dir, record)
        self._reviews.save(job_dir, review_state)

    # ------------------------------------------------------------------
    # Core content mutation primitive
    # ------------------------------------------------------------------

    def mutate(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        mutator: Any,  # Callable[[CanonicalQuestionRecord, ReviewState, str], None]
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Universal mutation primitive that enforces all invariant checks:

        1. Validates optimistic lock against expected_revision_id.
        2. Preserves immutable snapshot in revisions/{record_id}/{old_rev}.json.
        3. Bumps provenance.revision_id and resets human_approval and source_audit to 'pending'.
        4. Calls mutator(record, review_state, new_revision_id).
        5. In-place commits CanonicalQuestionRecord and ReviewState.
        6. Propagates dependent invalidations to linked questions (passage/script).
        """
        if all_records_by_id and record_id in all_records_by_id:
            record = all_records_by_id[record_id]
            review_state = (all_reviews_by_id.get(record_id)
                            if all_reviews_by_id and record_id in all_reviews_by_id
                            else self._load(job_dir, record_id)[1])
        else:
            record, review_state = self._load(job_dir, record_id)

        from .review_service import Conflict
        if record.provenance.revision_id != expected_revision_id:
            raise Conflict("다른 수정본이 있습니다. 새로고침 후 확인하세요.")

        # Save snapshot of pre-mutation state
        self._revisions.save_snapshot(
            job_dir,
            record.question.question_id,
            record.provenance.revision_id,
            record,
            review_state,
        )

        # Invalidate revision, approvals, and audits
        _content_invalidate(record, review_state)
        new_rev = record.provenance.revision_id
        review_state.applies_to_revision_id = new_rev

        # Run custom mutation logic (e.g. image crop, text edit)
        if mutator:
            mutator(record, review_state, new_rev)

        from ..standard_content import populate_standard_metadata
        populate_standard_metadata(record)

        # Propagate dependent invalidation (passage / script)
        if all_records_by_id and all_reviews_by_id:
            self._invalidate_dependents(
                job_dir, record, all_records_by_id, all_reviews_by_id
            )

        self._commit(job_dir, record, review_state)
        return record, review_state

    def update_content(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        values: Dict[str, Any],
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Apply a content mutation to a single record."""
        CONTENT_FIELDS = frozenset({
            # QuestionDomain
            "body", "solution", "answer", "points", "correct_rate",
            "unit", "q_type", "question_number", "question_set_id",
            "modality", "content_type", "strategy_tags",
            # Structural
            "regions", "solution_regions", "assets",
            # Relations
            "passage_ids", "listening_script_ids",
            # Note included in content mutation
            "note",
        })

        if all_records_by_id and record_id in all_records_by_id:
            record = all_records_by_id[record_id]
            review_state = (all_reviews_by_id.get(record_id)
                            if all_reviews_by_id and record_id in all_reviews_by_id
                            else self._load(job_dir, record_id)[1])
        else:
            record, review_state = self._load(job_dir, record_id)

        from .review_service import Conflict
        if record.provenance.revision_id != expected_revision_id:
            raise Conflict("다른 수정본이 있습니다. 새로고침 후 확인하세요.")

        # Detect whether any *content* field actually changes
        changed = _detect_changes(record, values)
        if not changed:
            if "note" in values and review_state.note != values["note"]:
                review_state.note = values["note"]
                self._reviews.save(job_dir, review_state)
            return record, review_state

        def _apply(rec: CanonicalQuestionRecord, rev: ReviewState, new_rev: str) -> None:
            _apply_content_values(rec, values)
            if "note" in values:
                rev.note = values["note"]

        return self.mutate(
            job_dir,
            record_id,
            expected_revision_id,
            _apply,
            all_records_by_id=all_records_by_id,
            all_reviews_by_id=all_reviews_by_id,
        )

    def update_regions(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        regions_key: str,  # "regions" or "solution_regions"
        new_regions: List[Dict[str, Any]],
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Crop-edit a region bounding box (structural mutation)."""
        return self.update_content(
            job_dir,
            record_id,
            expected_revision_id,
            {regions_key: new_regions},
            all_records_by_id=all_records_by_id,
            all_reviews_by_id=all_reviews_by_id,
        )

    def update_asset(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        updated_assets: List[Dict[str, Any]],
        body_override: Optional[str] = None,
        solution_override: Optional[str] = None,
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Replace assets list (and optionally body/solution) after crop/refine.

        Used by Store.crop_source() and Store.reapply_diagram_refine() when
        they produce a new image file and update the asset path.
        """
        values: Dict[str, Any] = {"assets": updated_assets}
        if body_override is not None:
            values["body"] = body_override
        if solution_override is not None:
            values["solution"] = solution_override
        return self.update_content(
            job_dir,
            record_id,
            expected_revision_id,
            values,
            all_records_by_id=all_records_by_id,
            all_reviews_by_id=all_reviews_by_id,
        )

    def link_solution(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        candidate: Dict[str, Any],
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Attach a reviewed solution candidate (content mutation)."""
        record, review_state = self._load(job_dir, record_id)
        if review_state.selected_solution_id == candidate.get("id"):
            return record, review_state  # no-op

        def _apply(rec: CanonicalQuestionRecord, rev: ReviewState, new_rev: str) -> None:
            if not rev.selected_solution_id:
                rev.solution_link_backup = {
                    "solution": rec.solution,
                    "solution_regions": copy.deepcopy(
                        [r for r in rec.provenance.source_regions
                         if r.get("content_role") == "solution"]
                    ),
                    "answer": rec.question.answer or "",
                    "solution_transcription_pending": "solution" in rev.transcription_pending,
                }
            rec.solution = candidate.get("body", "")
            body_regions = [r for r in rec.provenance.source_regions if r.get("content_role") != "solution"]
            rec.provenance.source_regions = body_regions + [
                dict(copy.deepcopy(r), content_role="solution") for r in candidate.get("regions", [])
            ]
            rev.transcription_pending = [s for s in rev.transcription_pending if s != "solution"]
            if candidate.get("transcription_required"):
                rev.transcription_pending.append("solution")
            if candidate.get("answer"):
                rec.question.answer = candidate["answer"]
            rev.selected_solution_id = candidate.get("id", "")
            rev.solution_match = {"status": "matched", "basis": ["question_number"], "confirmed_by_user": True}

        return self.mutate(
            job_dir,
            record_id,
            expected_revision_id,
            _apply,
            all_records_by_id=all_records_by_id,
            all_reviews_by_id=all_reviews_by_id,
        )

    def unlink_solution(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        *,
        all_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
        all_reviews_by_id: Optional[Dict[str, ReviewState]] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Detach a candidate and restore pre-link content."""
        record, review_state = self._load(job_dir, record_id)
        if not review_state.selected_solution_id:
            raise ValueError("연결된 해설이 없습니다.")

        backup = copy.deepcopy(review_state.solution_link_backup or {})

        def _apply(rec: CanonicalQuestionRecord, rev: ReviewState, new_rev: str) -> None:
            rec.solution = backup.get("solution", "")
            body_regions = [r for r in rec.provenance.source_regions if r.get("content_role") != "solution"]
            rec.provenance.source_regions = body_regions + copy.deepcopy(backup.get("solution_regions", []))
            rev.transcription_pending = [s for s in rev.transcription_pending if s != "solution"]
            if backup.get("solution_transcription_pending"):
                rev.transcription_pending.append("solution")
            if "answer" in backup:
                rec.question.answer = backup["answer"]
            rev.selected_solution_id = ""
            rev.solution_link_backup = None
            if rev.solution_match:
                rev.solution_match.pop("confirmed_by_user", None)

        return self.mutate(
            job_dir,
            record_id,
            expected_revision_id,
            _apply,
            all_records_by_id=all_records_by_id,
            all_reviews_by_id=all_reviews_by_id,
        )

    # ------------------------------------------------------------------
    # Dependent invalidation
    # ------------------------------------------------------------------

    def _invalidate_dependents(
        self,
        job_dir: Path,
        changed_record: CanonicalQuestionRecord,
        all_records_by_id: Dict[str, CanonicalQuestionRecord],
        all_reviews_by_id: Dict[str, ReviewState],
    ) -> None:
        """Invalidate records that depend on a passage or script that changed."""
        changed_id = changed_record.question.question_id
        kind = changed_record.question.content_kind

        if kind not in ("passage", "script"):
            return

        for dep_record in all_records_by_id.values():
            dep_id = dep_record.question.question_id
            if dep_id == changed_id:
                continue

            is_dependent = (
                (kind == "passage" and changed_id in dep_record.relations.passage_ids)
                or (kind == "script" and changed_id in dep_record.relations.listening_script_ids)
            )
            if not is_dependent:
                continue

            dep_review = all_reviews_by_id.get(dep_id)
            if dep_review is None:
                dep_review = ReviewState(
                    record_id=dep_id,
                    applies_to_revision_id=dep_record.provenance.revision_id,
                )
                all_reviews_by_id[dep_id] = dep_review

            # Snapshot dep record before invalidation
            self._revisions.save_snapshot(
                job_dir,
                dep_id,
                dep_record.provenance.revision_id,
                dep_record,
                dep_review,
            )
            _content_invalidate(dep_record, dep_review)
            dep_review.applies_to_revision_id = dep_record.provenance.revision_id
            self._commit(job_dir, dep_record, dep_review)


# ---------------------------------------------------------------------------
# Apply helpers
# ---------------------------------------------------------------------------

def _detect_changes(record: CanonicalQuestionRecord, values: Dict[str, Any]) -> bool:
    """Return True if any value in ``values`` actually differs from ``record``."""
    field_map = {
        "body": lambda r: r.body,
        "solution": lambda r: r.solution,
        "answer": lambda r: r.question.answer,
        "points": lambda r: r.question.points,
        "correct_rate": lambda r: r.question.correct_rate,
        "unit": lambda r: (r.unit.display_name or ""),
        "q_type": lambda r: (r.question.question_type or ""),
        "question_number": lambda r: r.question.question_number,
        "question_set_id": lambda r: r.question.question_set_id,
        "modality": lambda r: r.question.modality,
        "content_type": lambda r: r.question.content_type,
        "strategy_tags": lambda r: r.question.strategy_tags,
        "regions": lambda r: [reg for reg in r.provenance.source_regions if reg.get("content_role") == "body"],
        "solution_regions": lambda r: [reg for reg in r.provenance.source_regions if reg.get("content_role") == "solution"],
        "assets": lambda r: [{"path": a.path, "section": a.section, "id": a.asset_id} for a in r.assets],
        "passage_ids": lambda r: r.relations.passage_ids,
        "listening_script_ids": lambda r: r.relations.listening_script_ids,
    }
    for key, val in values.items():
        getter = field_map.get(key)
        if getter is None:
            return True  # unknown field → assume changed
        if getter(record) != val:
            return True
    return False


def _apply_content_values(record: CanonicalQuestionRecord, values: Dict[str, Any]) -> None:
    """Write values from the mutation dict into the Canonical record (in-place)."""
    if "body" in values:
        record.body = values["body"]
    if "solution" in values:
        record.solution = values["solution"]
    if "answer" in values:
        record.question.answer = values["answer"] or None
    if "points" in values:
        record.question.points = values["points"]
    if "correct_rate" in values:
        record.question.correct_rate = values["correct_rate"]
    if "unit" in values:
        record.unit.display_name = values["unit"] or None
    if "q_type" in values:
        record.question.question_type = values["q_type"] or None
    if "question_number" in values:
        record.question.question_number = str(values["question_number"])
    if "question_set_id" in values:
        record.question.question_set_id = values["question_set_id"]
    if "modality" in values:
        record.question.modality = values["modality"]
    if "content_type" in values:
        record.question.content_type = values["content_type"]
    if "strategy_tags" in values:
        record.question.strategy_tags = list(values["strategy_tags"])
    if "regions" in values:
        others = [r for r in record.provenance.source_regions if r.get("content_role") != "body"]
        record.provenance.source_regions = others + [
            dict(r, content_role="body") for r in values["regions"]
        ]
    if "solution_regions" in values:
        others = [r for r in record.provenance.source_regions if r.get("content_role") != "solution"]
        record.provenance.source_regions = others + [
            dict(r, content_role="solution") for r in values["solution_regions"]
        ]
    if "assets" in values:
        from _scripts_2.apps.exam.exam_core.domain.canonical.schema import AssetDomain
        import mimetypes
        seen = set()
        new_assets = []
        for asset in values["assets"]:
            a_id = str(asset.get("id") or asset.get("path", ""))
            if a_id in seen:
                a_id = f"{asset.get('section', 'body')}:{a_id}"
            seen.add(a_id)
            new_assets.append(AssetDomain(
                asset_id=a_id,
                section=asset.get("section", "body"),
                source_path=asset.get("path", ""),
                path=asset.get("path", ""),
                media_type=mimetypes.guess_type(asset.get("path", ""))[0] or "application/octet-stream",
                description=asset.get("description", ""),
                sha256=asset.get("sha256"),
            ))
        record.assets = new_assets
    if "passage_ids" in values:
        record.relations.passage_ids = list(values["passage_ids"])
    if "listening_script_ids" in values:
        record.relations.listening_script_ids = list(values["listening_script_ids"])

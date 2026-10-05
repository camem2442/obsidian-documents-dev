"""ReviewService — review-only mutations that do NOT touch provenance.revision_id.

These mutations affect only ReviewState (and optionally ReviewDomain.human_approval
in CanonicalQuestionRecord), but never change provenance.revision_id:

  - note update
  - approve / hold
  - audit skip / unskip
  - audit waiver
  - AI run log append
  - revert_audit_suggestion (delegates body/solution write to RecordService, then
    updates audit issue state here)

The invariant: none of these methods call RecordService.update_content().
RecordService.update_content() triggers from the "revert_audit_suggestion" path only
because the text is being changed — ReviewService then handles the audit-issue flag
update AFTER RecordService commits the content change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import CanonicalQuestionRecord
from ..review_state import ReviewState
from ..models import (
    normalize_audit_waiver_reason,
    structural_errors,
    audit_approval_gate,
)


class Conflict(ValueError):
    pass


class ReviewService:
    """Review-only mutation service.

    Shares repository instances with Store / RecordService.
    """

    def __init__(self, record_repo, review_repo):
        self._records = record_repo
        self._reviews = review_repo

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

    def _assert_revision(
        self,
        record: CanonicalQuestionRecord,
        expected: str,
    ) -> None:
        if record.provenance.revision_id != expected:
            raise Conflict("문항 버전이 바뀌었습니다.")

    def _commit_review(self, job_dir: Path, review_state: ReviewState) -> None:
        """Save only the ReviewState (no Canonical record change)."""
        self._reviews.save(job_dir, review_state)

    def _commit_both(
        self,
        job_dir: Path,
        record: CanonicalQuestionRecord,
        review_state: ReviewState,
    ) -> None:
        self._records.save(job_dir, record)
        self._reviews.save(job_dir, review_state)

    # ------------------------------------------------------------------
    # Note
    # ------------------------------------------------------------------

    def update_note(
        self,
        job_dir: Path,
        record_id: str,
        note: str,
    ) -> ReviewState:
        """Update the free-text note without touching the Canonical record."""
        _, review_state = self._load(job_dir, record_id)
        review_state.note = note
        self._commit_review(job_dir, review_state)
        return review_state

    # ------------------------------------------------------------------
    # Approve / Hold
    # ------------------------------------------------------------------

    def approve(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        note: str = "",
        *,
        passage_approval_status: Optional[str] = None,
        script_approval_status: Optional[str] = None,
        job_warnings: Optional[list] = None,
        approval_receipt: Optional[dict] = None,
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Approve a record.  Gate checks are performed here, NOT in Store.

        ``passage_approval_status`` / ``script_approval_status`` are the
        human_approval values of the linked passage/script records — the caller
        is responsible for looking them up.
        ``job_warnings`` is the list of job-level warnings (from JobManifest).
        """
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)

        errors = approval_errors(
            record, review_state, note,
            passage_approval_status=passage_approval_status,
            script_approval_status=script_approval_status,
            job_warnings=job_warnings,
        )

        if errors:
            raise ValueError("; ".join(errors))

        record.review.human_approval = "approved"
        review_state.note = note
        if approval_receipt is not None:
            from copy import deepcopy
            review_state.approval_events.append(deepcopy(approval_receipt))
        self._commit_both(job_dir, record, review_state)
        return record, review_state

    def hold(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        note: str = "",
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Set approval to 'held'."""
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)
        record.review.human_approval = "held"
        review_state.note = note
        self._commit_both(job_dir, record, review_state)
        return record, review_state

    # ------------------------------------------------------------------
    # Audit skip / unskip
    # ------------------------------------------------------------------

    def skip_audit_issue(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        issue_index: int,
        skipped: bool = True,
        note: Optional[str] = None,
    ) -> ReviewState:
        """Mark an audit issue as skipped (or unskip it).

        This is a review-only mutation — provenance.revision_id does NOT change.
        """
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)

        audit = review_state.audit or {}
        issues = audit.get("issues") or []
        if issue_index < 0 or issue_index >= len(issues):
            raise ValueError("지적을 찾지 못했습니다.")
        issue = issues[issue_index]
        if issue.get("applied"):
            raise ValueError("적용된 AI 제안은 되돌린 뒤 스킵할 수 있습니다.")

        if note is not None:
            note = str(note).strip()
            if len(note) > 500:
                raise ValueError("스킵 메모는 500자 이내로 입력하세요.")
            if note:
                issue["skip_note"] = note
            else:
                issue.pop("skip_note", None)

        if skipped:
            issue["skipped"] = True
        else:
            issue.pop("skipped", None)

        self._commit_review(job_dir, review_state)
        self._record_skip_decision(job_dir, record, review_state, issue_index, issue, skipped)
        return review_state

    def _record_skip_decision(
        self,
        job_dir: Path,
        record: CanonicalQuestionRecord,
        review_state: ReviewState,
        issue_index: int,
        issue: Dict[str, Any],
        skipped: bool,
    ) -> None:
        """Append an ai_run entry for audit skip/unskip decisions."""
        from datetime import datetime, timezone
        ai_runs = review_state.ai_runs or []
        ai_runs.append({
            "run_type": "audit_skip_decision",
            "issue_index": issue_index,
            "skipped": skipped,
            "skip_note": issue.get("skip_note", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "revision_id": record.provenance.revision_id,
        })
        review_state.ai_runs = ai_runs
        self._commit_review(job_dir, review_state)

    # ------------------------------------------------------------------
    # Audit waiver
    # ------------------------------------------------------------------

    def waive_audit(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        reason: str,
    ) -> ReviewState:
        """Grant an audit waiver for the current revision.

        review-only: does not change provenance.revision_id.
        """
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)

        if review_state.transcription_pending:
            raise ValueError("전사가 끝나야 대조를 생략할 수 있습니다.")

        reason = normalize_audit_waiver_reason(reason)
        review_state.audit_waiver = {
            "revision": expected_revision_id,
            "status": "waived",
            "reason": reason,
        }
        if not (review_state.note or "").strip():
            review_state.note = reason

        self._commit_review(job_dir, review_state)
        return review_state

    # ------------------------------------------------------------------
    # Quality check promotion
    # ------------------------------------------------------------------

    def approve_quality_check(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        index: int,
        finding_id: str,
    ) -> Dict[str, Any]:
        """Mark a quality check as promoted/reviewed without bumping revision."""
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)

        checks = review_state.quality_checks or []
        if not isinstance(index, int) or not 0 <= index < len(checks):
            raise ValueError("품질 검사 항목을 찾지 못했습니다.")
        check = checks[index]
        check["promoted_finding_id"] = finding_id
        check["review_required"] = False
        self._commit_review(job_dir, review_state)
        return check

    # ------------------------------------------------------------------
    # Audit issue applied-flag update (used after revert_audit_suggestion)
    # ------------------------------------------------------------------

    def mark_audit_issue_applied(
        self,
        job_dir: Path,
        record_id: str,
        issue_index: int,
        applied: bool,
        applied_section: Optional[str] = None,
    ) -> ReviewState:
        """Set or clear the 'applied' flag on an audit issue.

        Called by Store.revert_audit_suggestion() after the text revert has
        already been committed via RecordService.  This does NOT check revision
        because the caller owns the lock and has already verified consistency.
        """
        _, review_state = self._load(job_dir, record_id)
        audit = review_state.audit or {}
        issues = audit.get("issues") or []
        if issue_index < len(issues):
            issues[issue_index]["applied"] = applied
            if applied_section and applied:
                issues[issue_index]["applied_section"] = applied_section
            elif not applied:
                issues[issue_index].pop("applied_section", None)
        self._commit_review(job_dir, review_state)
        return review_state

    # ------------------------------------------------------------------
    # AI run log
    # ------------------------------------------------------------------

    def append_ai_run(
        self,
        job_dir: Path,
        record_id: str,
        run_entry: Dict[str, Any],
    ) -> ReviewState:
        """Append an AI run log entry to ReviewState (review-only)."""
        _, review_state = self._load(job_dir, record_id)
        ai_runs = review_state.ai_runs or []
        ai_runs.append(run_entry)
        review_state.ai_runs = ai_runs
        self._commit_review(job_dir, review_state)
        return review_state

    # ------------------------------------------------------------------
    # Bulk audit state update (used by pipeline/ai.py)
    # ------------------------------------------------------------------

    def set_audit_state(
        self,
        job_dir: Path,
        record_id: str,
        audit_dict: Optional[Dict[str, Any]],
    ) -> ReviewState:
        """Replace the audit dict in ReviewState (review-only, no revision bump)."""
        _, review_state = self._load(job_dir, record_id)
        review_state.audit = audit_dict
        self._commit_review(job_dir, review_state)
        return review_state

    def set_audit_result(
        self,
        job_dir: Path,
        record_id: str,
        expected_revision_id: str,
        audit_dict: Dict[str, Any],
    ) -> tuple[CanonicalQuestionRecord, ReviewState]:
        """Save AI audit results on the current revision without revision bump.

        - Verifies expected_revision_id
        - Updates ReviewState.audit with detailed evidence
        - Clears ReviewState.audit_waiver
        - Syncs record.review.source_audit summary ('no_difference', 'suspected_difference', 'unreadable')
        """
        record, review_state = self._load(job_dir, record_id)
        self._assert_revision(record, expected_revision_id)
        review_state.audit = audit_dict
        review_state.audit_waiver = None
        result_status = audit_dict.get("result", "pending")
        record.review.source_audit = result_status
        self._commit_review(job_dir, review_state)
        self._records.save(job_dir, record)
        return record, review_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def approval_errors(record, review_state, note="", *, passage_approval_status=None,
                    script_approval_status=None, job_warnings=None):
    """Read-only evaluation of the exact checks used by approve()."""
    item_view = _record_to_gate_view(record, review_state)
    from ..standard_content import workbook_content
    errors = structural_errors(item_view) + (job_warnings or []) + workbook_content(record)["warnings"]
    if passage_approval_status and passage_approval_status != "approved":
        errors.append("공통 지문을 먼저 승인하세요.")
    if script_approval_status and script_approval_status != "approved":
        errors.append("연결된 듣기 대본을 먼저 승인하세요.")
    gate = audit_approval_gate(item_view, note)
    if gate:
        errors.append(gate)
    return errors


def _record_to_gate_view(
    record: CanonicalQuestionRecord,
    review_state: ReviewState,
) -> Dict[str, Any]:
    """Build the minimal legacy item dict needed by structural_errors() and audit_approval_gate().

    Only the fields actually read by those functions are populated.
    """
    body_regions = [r for r in record.provenance.source_regions if r.get("content_role") == "body"]
    sol_regions = [r for r in record.provenance.source_regions if r.get("content_role") == "solution"]
    return {
        "id": record.question.question_id,
        "revision": record.provenance.revision_id,
        "body": record.body,
        "solution": record.solution,
        "points": record.question.points,
        "regions": body_regions,
        "solution_regions": sol_regions,
        "assets": [
            {"path": a.path, "section": a.section, "id": a.asset_id}
            for a in record.assets
        ],
        "transcription_pending": review_state.transcription_pending or [],
        "audit": review_state.audit,
        "audit_waiver": review_state.audit_waiver,
        "note": review_state.note,
        "solution_candidates": review_state.solution_candidates or [],
        "selected_solution_id": review_state.selected_solution_id or "",
        "solution_match": review_state.solution_match,
        "style_mapping_errors": review_state.style_mapping_errors or [],
        "group_mapping_errors": review_state.group_mapping_errors or [],
        "box_checks": review_state.box_checks or [],
        "material_validation_errors": review_state.material_validation_errors or [],
        "warnings": review_state.warnings or [],
    }

"""ReviewDecision classification module for Exam Processor Review Orchestration Track (R1 & R1.1).

Provides pure domain classifications for item review tiers:
  - NOT_READY: Transcription pending, audit missing (without current waiver), audit incomplete,
               audit stale, review_state stale, or dependency approval status unknown.
  - RED: Structural errors, unreadable audit result, blocking dependencies, audit summary/result
         inconsistency, or gate blockers.
  - YELLOW: Suspected audit differences, unresolved audit issues, current waivers, or review warnings.
  - GREEN: All checks passed with no_difference audit, zero structural errors, zero warnings (sample review candidate).

Precedence order: NOT_READY -> RED -> YELLOW -> GREEN (most conservative wins).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


CLASSIFIER_VERSION = "review-decision/1.2"


class ReviewTier(str, Enum):
    NOT_READY = "NOT_READY"
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


@dataclass(frozen=True)
class ReviewDecisionContext:
    """Context information provided by the caller/runtime for review classification."""

    job_warnings: Tuple[str, ...] = field(default_factory=tuple)
    passage_approval_status: Optional[str] = None
    script_approval_status: Optional[str] = None


@dataclass(frozen=True)
class ReviewDecision:
    tier: ReviewTier

    basis_record_revision_id: str
    basis_review_state_revision_id: str

    reasons: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)

    eligible_for_sample_review: bool = False
    requires_focused_review: bool = False
    requires_detailed_review: bool = False

    decision_version: str = CLASSIFIER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        d["reasons"] = list(self.reasons)
        d["blockers"] = list(self.blockers)
        return d


def classify_review_decision(
    record: Any,  # CanonicalQuestionRecord
    review_state: Any,  # ReviewState
    context: Optional[ReviewDecisionContext] = None,
    *,
    job_manifest: Optional[Any] = None,
    passage_approval_status: Optional[str] = None,
    script_approval_status: Optional[str] = None,
    job_warnings: Optional[List[str]] = None,
) -> ReviewDecision:
    """Classify a record and its review state into a ReviewDecision (V1.2 fail-closed baseline)."""
    rec_rev = getattr(record.provenance, "revision_id", "")
    rev_state_rev = getattr(review_state, "state_revision_id", "")
    applies_rev = getattr(review_state, "applies_to_revision_id", "")

    # Extract effective context parameters
    eff_job_warnings: List[str] = []
    if context and context.job_warnings:
        eff_job_warnings.extend(context.job_warnings)
    if job_warnings:
        eff_job_warnings.extend(job_warnings)

    eff_passage_status = (
        context.passage_approval_status
        if context and context.passage_approval_status is not None
        else passage_approval_status
    )
    eff_script_status = (
        context.script_approval_status
        if context and context.script_approval_status is not None
        else script_approval_status
    )

    # 0. Check ReviewState revision staleness (fail-closed)
    if applies_rev != rec_rev:
        return ReviewDecision(
            tier=ReviewTier.NOT_READY,
            basis_record_revision_id=rec_rev,
            basis_review_state_revision_id=rev_state_rev,
            reasons=("review_state_stale",),
            blockers=("review_state_stale",),
            eligible_for_sample_review=False,
            requires_focused_review=False,
            requires_detailed_review=False,
            decision_version=CLASSIFIER_VERSION,
        )

    # Build gate view dictionary for model helper functions
    from .services.review_service import _record_to_gate_view
    from .models import audit_approval_gate, structural_errors

    item_view = _record_to_gate_view(record, review_state)

    # 1. Check NOT_READY conditions
    not_ready_reasons: List[str] = []
    if review_state.transcription_pending:
        not_ready_reasons.append("transcription_pending")

    waiver = review_state.audit_waiver or {}
    has_current_waiver = bool(
        waiver.get("status") == "waived" and waiver.get("revision") == rec_rev
    )
    if waiver and waiver.get("status") == "waived" and waiver.get("revision") != rec_rev:
        not_ready_reasons.append("audit_waiver_stale")

    audit = review_state.audit or {}

    if not has_current_waiver:
        if not audit:
            not_ready_reasons.append("audit_missing")
        elif audit.get("status") != "completed":
            not_ready_reasons.append("audit_not_completed")
        elif audit.get("revision") != rec_rev:
            not_ready_reasons.append("audit_stale")

    # Dependency approval status unknown check (unknown != approved)
    if record.relations.passage_ids and eff_passage_status is None:
        not_ready_reasons.append("passage_approval_unknown")

    if record.relations.listening_script_ids and eff_script_status is None:
        not_ready_reasons.append("script_approval_unknown")

    if not_ready_reasons:
        return ReviewDecision(
            tier=ReviewTier.NOT_READY,
            basis_record_revision_id=rec_rev,
            basis_review_state_revision_id=rev_state_rev,
            reasons=tuple(not_ready_reasons),
            blockers=tuple(not_ready_reasons),
            eligible_for_sample_review=False,
            requires_focused_review=False,
            requires_detailed_review=False,
            decision_version=CLASSIFIER_VERSION,
        )

    # 2. Check RED conditions
    red_blockers: List[str] = []

    from .standard_content import workbook_content
    s_errors = structural_errors(item_view) + workbook_content(record)["warnings"]
    if eff_job_warnings:
        s_errors.extend(eff_job_warnings)
    if s_errors:
        red_blockers.extend(s_errors)

    audit_result = audit.get("result")
    source_audit = getattr(record.review, "source_audit", "")

    if source_audit == "unreadable" or audit_result == "unreadable":
        red_blockers.append("audit_result_unreadable")

    # Audit summary vs completed audit result strict consistency check
    if audit and audit.get("status") == "completed" and audit.get("revision") == rec_rev:
        if source_audit != audit_result:
            red_blockers.append("audit_state_inconsistent")

        # Completed audit result validity check
        if not audit_result or audit_result not in ("no_difference", "suspected_difference", "unreadable"):
            red_blockers.append("audit_result_invalid")

    if record.relations.passage_ids and eff_passage_status != "approved":
        red_blockers.append("passage_not_approved")

    if record.relations.listening_script_ids and eff_script_status != "approved":
        red_blockers.append("listening_script_not_approved")

    gate_error = audit_approval_gate(item_view, review_state.note)
    if gate_error:
        red_blockers.append(f"gate_blocked: {gate_error}")

    if red_blockers:
        return ReviewDecision(
            tier=ReviewTier.RED,
            basis_record_revision_id=rec_rev,
            basis_review_state_revision_id=rev_state_rev,
            reasons=tuple(red_blockers),
            blockers=tuple(red_blockers),
            eligible_for_sample_review=False,
            requires_focused_review=False,
            requires_detailed_review=True,
            decision_version=CLASSIFIER_VERSION,
        )

    # 3. Check YELLOW conditions
    yellow_reasons: List[str] = []

    if source_audit == "suspected_difference" or audit_result == "suspected_difference":
        yellow_reasons.append("audit_suspected_difference")

    unresolved_issues = [
        issue for issue in (audit.get("issues") or [])
        if not issue.get("applied") and not issue.get("skipped")
    ]
    if unresolved_issues:
        yellow_reasons.append(f"unresolved_audit_issues_{len(unresolved_issues)}")

    if has_current_waiver:
        yellow_reasons.append("audit_waived")

    if review_state.warnings:
        yellow_reasons.append("review_state_warnings")

    if yellow_reasons:
        return ReviewDecision(
            tier=ReviewTier.YELLOW,
            basis_record_revision_id=rec_rev,
            basis_review_state_revision_id=rev_state_rev,
            reasons=tuple(yellow_reasons),
            blockers=(),
            eligible_for_sample_review=False,
            requires_focused_review=True,
            requires_detailed_review=False,
            decision_version=CLASSIFIER_VERSION,
        )

    # 4. Explicit GREEN predicate
    if (
        audit.get("status") == "completed"
        and audit.get("revision") == rec_rev
        and audit_result == "no_difference"
        and source_audit == "no_difference"
        and not has_current_waiver
        and not review_state.warnings
        and not s_errors
        and (not record.relations.passage_ids or eff_passage_status == "approved")
        and (not record.relations.listening_script_ids or eff_script_status == "approved")
    ):
        green_reasons = ("audit_no_difference", "zero_structural_errors", "all_checks_passed")
        return ReviewDecision(
            tier=ReviewTier.GREEN,
            basis_record_revision_id=rec_rev,
            basis_review_state_revision_id=rev_state_rev,
            reasons=green_reasons,
            blockers=(),
            eligible_for_sample_review=True,
            requires_focused_review=False,
            requires_detailed_review=False,
            decision_version=CLASSIFIER_VERSION,
        )

    # Fallback fail-closed if GREEN predicate failed without triggering preceding tiers
    return ReviewDecision(
        tier=ReviewTier.RED,
        basis_record_revision_id=rec_rev,
        basis_review_state_revision_id=rev_state_rev,
        reasons=("green_predicate_failed",),
        blockers=("green_predicate_failed",),
        eligible_for_sample_review=False,
        requires_focused_review=False,
        requires_detailed_review=True,
        decision_version=CLASSIFIER_VERSION,
    )

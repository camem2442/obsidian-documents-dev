"""Unit tests for ReviewDecision V1.2 & R1.2 Fail-Closed Hardening."""

import unittest

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import (
    CanonicalQuestionRecord,
    ProvenanceDomain,
    QuestionDomain,
    ReviewDomain,
    RelationsDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState
from _scripts_2.apps.exam.exam_processor.domain.review_decision import (
    CLASSIFIER_VERSION,
    ReviewDecision,
    ReviewDecisionContext,
    ReviewTier,
    classify_review_decision,
)


class TestReviewDecision(unittest.TestCase):

    def _make_record(
        self,
        question_id="q1",
        revision_id="rev1",
        body="Sample question body text.",
        points=2,
        passage_ids=None,
        script_ids=None,
        source_audit="pending",
    ):
        return CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id=question_id,
                display_code=question_id,
                subject="math",
                points=points,
            ),
            provenance=ProvenanceDomain(revision_id=revision_id),
            body=body,
            review=ReviewDomain(source_audit=source_audit),
            relations=RelationsDomain(
                passage_ids=passage_ids or [],
                listening_script_ids=script_ids or [],
            ),
        )

    def _make_review_state(
        self,
        record_id="q1",
        applies_to_revision_id="rev1",
        audit=None,
        transcription_pending=None,
        audit_waiver=None,
        warnings=None,
        material_validation_errors=None,
        style_mapping_errors=None,
        group_mapping_errors=None,
        box_checks=None,
        note="",
    ):
        return ReviewState(
            record_id=record_id,
            applies_to_revision_id=applies_to_revision_id,
            audit=audit,
            transcription_pending=transcription_pending or [],
            audit_waiver=audit_waiver,
            warnings=warnings or [],
            material_validation_errors=material_validation_errors or [],
            style_mapping_errors=style_mapping_errors or [],
            group_mapping_errors=group_mapping_errors or [],
            box_checks=box_checks or [],
            note=note,
        )

    # ------------------------------------------------------------------
    # R1.2 & R1.1 Hardening Tests
    # ------------------------------------------------------------------

    def test_yellow_current_waiver_without_audit(self):
        """Current revision waiver without audit should be classified as YELLOW (audit_waived), not NOT_READY."""
        rec = self._make_record(source_audit="pending")
        waiver = {"status": "waived", "revision": "rev1", "reason": "Bypassed"}
        rev_state = self._make_review_state(audit=None, audit_waiver=waiver)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.YELLOW)
        self.assertIn("audit_waived", decision.reasons)

    def test_not_ready_stale_waiver_without_audit(self):
        """Stale waiver without audit must remain NOT_READY with audit_waiver_stale reason."""
        rec = self._make_record(revision_id="rev2")
        waiver = {"status": "waived", "revision": "rev1", "reason": "Bypassed"}
        rev_state = self._make_review_state(
            applies_to_revision_id="rev2", audit=None, audit_waiver=waiver
        )
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("audit_waiver_stale", decision.reasons)
        self.assertIn("audit_missing", decision.reasons)

    def test_red_audit_state_inconsistent_pending(self):
        """Mismatch when source_audit is 'pending' but completed audit result is 'no_difference' -> RED (audit_state_inconsistent)."""
        rec = self._make_record(source_audit="pending")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)

        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertIn("audit_state_inconsistent", decision.blockers)

    def test_red_invalid_audit_result(self):
        """Completed audit with unknown or missing result -> RED (audit_result_invalid)."""
        rec = self._make_record(source_audit="unknown_val")
        audit = {"status": "completed", "revision": "rev1", "result": "unknown_val"}
        rev_state = self._make_review_state(audit=audit)

        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertIn("audit_result_invalid", decision.blockers)

    def test_not_ready_passage_approval_unknown(self):
        """Passage relation exists but passage_approval_status is None -> NOT_READY (passage_approval_unknown)."""
        rec = self._make_record(passage_ids=["p1"])
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)

        decision = classify_review_decision(rec, rev_state, passage_approval_status=None)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("passage_approval_unknown", decision.reasons)

    def test_not_ready_script_approval_unknown(self):
        """Listening script relation exists but script_approval_status is None -> NOT_READY (script_approval_unknown)."""
        rec = self._make_record(script_ids=["s1"])
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)

        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("script_approval_unknown", decision.reasons)

    def test_not_ready_review_state_stale(self):
        """ReviewState.applies_to_revision_id != record.provenance.revision_id -> NOT_READY (review_state_stale)."""
        rec = self._make_record(revision_id="rev2")
        audit = {"status": "completed", "revision": "rev2", "result": "no_difference"}
        rev_state = self._make_review_state(
            applies_to_revision_id="rev1", audit=audit
        )

        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("review_state_stale", decision.reasons)

    def test_red_audit_state_inconsistent(self):
        """Mismatch between record.review.source_audit and completed audit.result -> RED (audit_state_inconsistent)."""
        rec = self._make_record(source_audit="suspected_difference")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)

        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertIn("audit_state_inconsistent", decision.blockers)

    def test_red_job_warnings_via_context(self):
        """Job-level warnings passed via ReviewDecisionContext trigger RED tier."""
        rec = self._make_record()
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)
        ctx = ReviewDecisionContext(job_warnings=("Job-level warning message",))

        decision = classify_review_decision(rec, rev_state, context=ctx)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertIn("Job-level warning message", decision.blockers)

    # ------------------------------------------------------------------
    # Standard Classifier Unit Tests
    # ------------------------------------------------------------------

    def test_not_ready_transcription_pending(self):
        rec = self._make_record()
        rev_state = self._make_review_state(transcription_pending=["body"])
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("transcription_pending", decision.reasons)

    def test_not_ready_audit_missing(self):
        rec = self._make_record()
        rev_state = self._make_review_state(audit=None)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("audit_missing", decision.reasons)

    def test_not_ready_audit_not_completed(self):
        rec = self._make_record()
        audit = {"status": "running", "revision": "rev1"}
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("audit_not_completed", decision.reasons)

    def test_not_ready_audit_stale(self):
        rec = self._make_record(revision_id="rev2")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(applies_to_revision_id="rev2", audit=audit)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.NOT_READY)
        self.assertIn("audit_stale", decision.reasons)

    def test_red_empty_body(self):
        rec = self._make_record(body="")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertTrue(decision.requires_detailed_review)

    def test_red_audit_unreadable(self):
        rec = self._make_record(source_audit="unreadable")
        audit = {"status": "completed", "revision": "rev1", "result": "unreadable"}
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertTrue(decision.requires_detailed_review)

    def test_red_passage_not_approved(self):
        rec = self._make_record(passage_ids=["p1"])
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(
            rec, rev_state, passage_approval_status="pending"
        )

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertIn("passage_not_approved", decision.blockers)

    def test_red_missing_box_checks(self):
        rec = self._make_record(source_audit="no_difference")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        box_checks = [{"status": "missing", "text_preview": "sample box"}]
        rev_state = self._make_review_state(audit=audit, box_checks=box_checks)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.RED)
        self.assertTrue(decision.requires_detailed_review)

    def test_yellow_suspected_difference(self):
        rec = self._make_record(source_audit="suspected_difference")
        audit = {
            "status": "completed",
            "revision": "rev1",
            "result": "suspected_difference",
            "issues": [{"applied": False, "skipped": False, "message": "difference in text"}],
        }
        rev_state = self._make_review_state(audit=audit, note="Checked issue")
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.YELLOW)
        self.assertTrue(decision.requires_focused_review)
        self.assertIn("audit_suspected_difference", decision.reasons)

    def test_yellow_warnings(self):
        rec = self._make_record(source_audit="no_difference")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit, warnings=["Check solution candidate."])
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.YELLOW)
        self.assertIn("review_state_warnings", decision.reasons)

    def test_green_all_checks_passed(self):
        rec = self._make_record(source_audit="no_difference")
        audit = {
            "status": "completed",
            "revision": "rev1",
            "result": "no_difference",
            "issues": [],
        }
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(rec, rev_state)

        self.assertEqual(decision.tier, ReviewTier.GREEN)
        self.assertTrue(decision.eligible_for_sample_review)
        self.assertFalse(decision.requires_focused_review)
        self.assertFalse(decision.requires_detailed_review)
        self.assertIn("audit_no_difference", decision.reasons)
        self.assertEqual(decision.decision_version, CLASSIFIER_VERSION)

    def test_precedence_not_ready_over_red_and_yellow(self):
        rec = self._make_record(body="")  # structural error (RED candidate)
        rev_state = self._make_review_state(transcription_pending=["body"])
        decision = classify_review_decision(rec, rev_state)

        # NOT_READY evaluated first
        self.assertEqual(decision.tier, ReviewTier.NOT_READY)

    def test_precedence_red_over_yellow(self):
        rec = self._make_record(body="")  # structural error (RED candidate)
        audit = {
            "status": "completed",
            "revision": "rev1",
            "result": "suspected_difference",  # YELLOW candidate
            "issues": [{"applied": False, "skipped": False}],
        }
        rev_state = self._make_review_state(audit=audit, note="Checked")
        decision = classify_review_decision(rec, rev_state)

        # RED evaluated before YELLOW
        self.assertEqual(decision.tier, ReviewTier.RED)

    def test_to_dict_format(self):
        rec = self._make_record(source_audit="no_difference")
        audit = {"status": "completed", "revision": "rev1", "result": "no_difference"}
        rev_state = self._make_review_state(audit=audit)
        decision = classify_review_decision(rec, rev_state)
        d = decision.to_dict()

        self.assertEqual(d["tier"], "GREEN")
        self.assertIsInstance(d["reasons"], list)
        self.assertIsInstance(d["blockers"], list)
        self.assertEqual(d["basis_record_revision_id"], "rev1")
        self.assertEqual(d["decision_version"], CLASSIFIER_VERSION)


if __name__ == "__main__":
    unittest.main()

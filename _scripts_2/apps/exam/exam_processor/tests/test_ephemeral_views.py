import unittest
from typing import Dict

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import (
    CanonicalQuestionRecord,
    ProvenanceDomain,
    QuestionDomain,
    RelationsDomain,
    ReviewDomain,
    SourceDomain,
    UnitDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.job_manifest import JobManifest
from _scripts_2.apps.exam.exam_processor.domain.review_decision import CLASSIFIER_VERSION
from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState
from _scripts_2.apps.exam.exam_processor.storage.ephemeral_views import (
    build_legacy_item_view,
    build_legacy_job_view,
    build_review_decision_context,
    resolve_dependency_status,
    verify_store_views_contract,
)


class EphemeralViewsTests(unittest.TestCase):
    def test_verify_contract_passes(self):
        verify_store_views_contract()

    def _make_record(
        self,
        question_id="q1",
        revision_id="rev1",
        body="Question body text",
        points=2,
        passage_ids=None,
        script_ids=None,
        source_audit="no_difference",
        human_approval="pending",
        content_kind="question",
    ):
        return CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id=question_id,
                display_code=question_id,
                subject="math",
                points=points,
                content_kind=content_kind,
            ),
            provenance=ProvenanceDomain(revision_id=revision_id),
            body=body,
            review=ReviewDomain(
                source_audit=source_audit,
                human_approval=human_approval,
            ),
            relations=RelationsDomain(
                passage_ids=passage_ids or [],
                listening_script_ids=script_ids or [],
            ),
        )

    def _make_review_state(
        self,
        record_id="q1",
        applies_to_revision_id="rev1",
        audit=...,
        transcription_pending=None,
        audit_waiver=None,
        warnings=None,
        note="",
    ):
        if audit is ...:
            effective_audit = {
                "status": "completed",
                "revision": applies_to_revision_id,
                "result": "no_difference",
                "issues": [],
            }
        else:
            effective_audit = audit

        return ReviewState(
            record_id=record_id,
            applies_to_revision_id=applies_to_revision_id,
            audit=effective_audit,
            transcription_pending=transcription_pending or [],
            audit_waiver=audit_waiver,
            warnings=warnings or [],
            note=note,
        )

    def test_resolve_dependency_status(self):
        records = {
            "p1": self._make_record(question_id="p1", human_approval="approved"),
            "p2": self._make_record(question_id="p2", human_approval="pending"),
        }

        # Empty ids -> None
        self.assertIsNone(resolve_dependency_status([], records))

        # Missing record -> None
        self.assertIsNone(resolve_dependency_status(["nonexistent"], records))
        self.assertIsNone(resolve_dependency_status(["p1", "nonexistent"], records))

        # All approved -> 'approved'
        self.assertEqual(resolve_dependency_status(["p1"], records), "approved")

        # Any pending -> 'pending'
        self.assertEqual(resolve_dependency_status(["p1", "p2"], records), "pending")
        self.assertEqual(resolve_dependency_status(["p2"], records), "pending")

    def test_build_legacy_job_view_review_decision_and_summary(self):
        # 1. Clean pending question -> GREEN
        q1 = self._make_record("q1", "rev1", source_audit="no_difference", human_approval="pending")
        s1 = self._make_review_state("q1", "rev1")

        # 2. Suspected diff -> YELLOW
        q2 = self._make_record("q2", "rev1", source_audit="suspected_difference", human_approval="pending")
        s2 = self._make_review_state(
            "q2",
            "rev1",
            audit={
                "status": "completed",
                "revision": "rev1",
                "result": "suspected_difference",
                "issues": [{"applied": False, "skipped": False, "message": "typo"}],
            },
            note="checked",
        )

        # 3. Structural error (empty body) -> RED
        q3 = self._make_record("q3", "rev1", body="", source_audit="no_difference", human_approval="pending")
        s3 = self._make_review_state("q3", "rev1")

        # 4. Audit missing -> NOT_READY
        q4 = self._make_record("q4", "rev1", source_audit="pending", human_approval="pending")
        s4 = self._make_review_state("q4", "rev1", audit=None)

        # 5. Passage approved -> dependent question gets GREEN
        p1 = self._make_record("p1", "rev1", human_approval="approved", content_kind="passage")
        sp1 = self._make_review_state("p1", "rev1")
        q5 = self._make_record("q5", "rev1", passage_ids=["p1"], human_approval="pending")
        s5 = self._make_review_state("q5", "rev1")

        # 6. Passage pending -> dependent question gets RED (passage_not_approved)
        p2 = self._make_record("p2", "rev1", human_approval="pending", content_kind="passage")
        sp2 = self._make_review_state("p2", "rev1")
        q6 = self._make_record("q6", "rev1", passage_ids=["p2"], human_approval="pending")
        s6 = self._make_review_state("q6", "rev1")

        # 7. Passage missing from records -> dependent question gets NOT_READY (passage_approval_unknown)
        q7 = self._make_record("q7", "rev1", passage_ids=["missing_p"], human_approval="pending")
        s7 = self._make_review_state("q7", "rev1")

        # 8. Approved items: one GREEN, one YELLOW
        q8 = self._make_record("q8", "rev1", human_approval="approved")
        s8 = self._make_review_state("q8", "rev1")
        q9 = self._make_record("q9", "rev1", source_audit="suspected_difference", human_approval="approved")
        s9 = self._make_review_state(
            "q9",
            "rev1",
            audit={
                "status": "completed",
                "revision": "rev1",
                "result": "suspected_difference",
                "issues": [{"applied": False, "skipped": False}],
            },
            note="verified",
        )

        # 9. Held items: one RED
        q10 = self._make_record("q10", "rev1", body="", human_approval="held")
        s10 = self._make_review_state("q10", "rev1")

        manifest = JobManifest(
            id="test-job",
            source_id="test-source",
            source="Test Exam",
            track="Math",
            format="test_format",
            record_ids=["q1", "q2", "q3", "q4", "p1", "q5", "p2", "q6", "q7", "q8", "q9", "q10"],
            documents={},
        )

        records: Dict[str, CanonicalQuestionRecord] = {
            "q1": q1, "q2": q2, "q3": q3, "q4": q4,
            "p1": p1, "q5": q5, "p2": p2, "q6": q6,
            "q7": q7, "q8": q8, "q9": q9, "q10": q10,
        }
        review_states: Dict[str, ReviewState] = {
            "q1": s1, "q2": s2, "q3": s3, "q4": s4,
            "p1": sp1, "q5": s5, "p2": sp2, "q6": s6,
            "q7": s7, "q8": s8, "q9": s9, "q10": s10,
        }

        job_view = build_legacy_job_view(manifest, records, review_states)

        # Check items review_decision projection
        items_by_id = {i["id"]: i for i in job_view["items"]}

        self.assertEqual(items_by_id["q1"]["review_decision"]["tier"], "GREEN")
        self.assertEqual(items_by_id["q1"]["review_decision"]["decision_version"], CLASSIFIER_VERSION)

        self.assertEqual(items_by_id["q2"]["review_decision"]["tier"], "YELLOW")
        self.assertEqual(items_by_id["q3"]["review_decision"]["tier"], "RED")
        self.assertEqual(items_by_id["q4"]["review_decision"]["tier"], "NOT_READY")

        # Passage items have review_decision
        self.assertEqual(items_by_id["p1"]["review_decision"]["tier"], "GREEN")
        self.assertEqual(items_by_id["p2"]["review_decision"]["tier"], "GREEN")

        # Dependent items
        self.assertEqual(items_by_id["q5"]["review_decision"]["tier"], "GREEN")
        self.assertEqual(items_by_id["q6"]["review_decision"]["tier"], "RED")
        self.assertIn("passage_not_approved", items_by_id["q6"]["review_decision"]["blockers"])

        self.assertEqual(items_by_id["q7"]["review_decision"]["tier"], "NOT_READY")
        self.assertIn("passage_approval_unknown", items_by_id["q7"]["review_decision"]["reasons"])

        # Check review_queue_summary
        summary = job_view.get("review_queue_summary")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["classifier_version"], CLASSIFIER_VERSION)
        self.assertEqual(summary["total_items"], 12)

        # pending items: q1 (GREEN), q2 (YELLOW), q3 (RED), q4 (NOT_READY), q5 (GREEN), p2 (GREEN), q6 (RED), q7 (NOT_READY)
        # pending GREEN: q1, q5, p2 (3)
        # pending YELLOW: q2 (1)
        # pending RED: q3, q6 (2)
        # pending NOT_READY: q4, q7 (2)
        self.assertEqual(summary["pending"]["GREEN"], 3)
        self.assertEqual(summary["pending"]["YELLOW"], 1)
        self.assertEqual(summary["pending"]["RED"], 2)
        self.assertEqual(summary["pending"]["NOT_READY"], 2)

        # approved items: p1 (GREEN), q8 (GREEN), q9 (YELLOW) -> total: 3
        self.assertEqual(summary["approved"]["total"], 3)
        self.assertEqual(summary["approved"]["by_tier"]["GREEN"], 2)
        self.assertEqual(summary["approved"]["by_tier"]["YELLOW"], 1)
        self.assertEqual(summary["approved"]["by_tier"]["RED"], 0)
        self.assertEqual(summary["approved"]["by_tier"]["NOT_READY"], 0)

        # held items: q10 (RED) -> total: 1
        self.assertEqual(summary["held"]["total"], 1)
        self.assertEqual(summary["held"]["by_tier"]["RED"], 1)
        self.assertEqual(summary["held"]["by_tier"]["GREEN"], 0)

        # Invariant check: 3+1+2+2 + 3 + 1 == 12 == total_items
        self.assertEqual(
            sum(summary["pending"].values()) + summary["approved"]["total"] + summary["held"]["total"],
            summary["total_items"],
        )

    def test_invalid_human_approval_fails_closed(self):
        rec = self._make_record("q1", "rev1", human_approval="unknown_status")
        rev = self._make_review_state("q1", "rev1")
        manifest = JobManifest(id="j1", source_id="s1", source="s", track="t", format="f", record_ids=["q1"], documents={})

        with self.assertRaises(ValueError) as ctx:
            build_legacy_job_view(manifest, {"q1": rec}, {"q1": rev})
        self.assertIn("Invalid human_approval status", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

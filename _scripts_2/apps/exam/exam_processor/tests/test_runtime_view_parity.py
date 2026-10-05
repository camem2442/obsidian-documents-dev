import unittest
import uuid

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    AssetDomain,
    CanonicalQuestionRecord,
    OriginDomain,
    QuestionDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.job_manifest import JobManifest
from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState
from _scripts_2.apps.exam.exam_processor.storage.ephemeral_views import (
    build_legacy_item_view,
    build_legacy_job_view,
)


class RuntimeViewParityTests(unittest.TestCase):
    def test_legacy_item_view_parity(self):
        rev_id = str(uuid.uuid4())
        record = CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id="item-001",
                display_code="Q21",
                subject="english",
                question_number="21",
                points=3.0,
                answer="3",
                question_type="multiple_choice",
            ),
            source=SourceDomain(
                source_id="src-1",
                type="exam",
                title="2025-06-mock",
            ),
            origin=OriginDomain(
                type="mock",
                raw_label="2025년 6월 모의고사 21번",
            ),
            body="Read the following passage and choose the best answer.\n\n① Option 1\n② Option 2\n③ Option 3\n④ Option 4\n⑤ Option 5",
            solution="Here is the detailed solution explanation.",
            assets=[
                AssetDomain(
                    asset_id="body:fig-1",
                    section="body",
                    source_path="assets/fig1.png",
                    path="assets/fig1.png",
                    media_type="image/png",
                )
            ],
        )
        record.provenance.revision_id = rev_id

        review = ReviewState(
            record_id="item-001",
            applies_to_revision_id=rev_id,
            published_revision_id=rev_id,
            state_revision_id="state-001",
            note="Teacher note for item 1",
            audit={"status": "approved", "timestamp": "2026-09-21T12:00:00Z"},
            warnings=["Check diagram resolution"],
            quality_checks=[{"name": "test_check", "passed": True}],
            reference_text="Reference text for parity check",
            solution_candidates=[{"id": "sol-1", "text": "candidate 1"}],
            selected_solution_id="sol-1",
        )

        manifest = JobManifest(
            id="a" * 32,
            source_id="src-1",
            source="2025-06-mock",
            track="english",
            format="exam",
            record_ids=["item-001"],
            documents={},
            storage_version=2,
        )

        item_view = build_legacy_item_view(record, review, manifest)

        # Check required legacy item fields
        self.assertEqual(item_view["id"], "item-001")
        self.assertEqual(item_view["revision"], rev_id)
        self.assertEqual(item_view["published_revision"], rev_id)
        self.assertEqual(item_view["q_type"], "multiple_choice")
        self.assertEqual(item_view["number"], "21")
        self.assertEqual(item_view["points"], 3.0)
        self.assertIn("Read the following passage", item_view["body"])
        self.assertEqual(item_view["answer"], "3")
        self.assertEqual(item_view["solution"], "Here is the detailed solution explanation.")

        # Check review-state projections
        self.assertEqual(item_view["note"], "Teacher note for item 1")
        self.assertEqual(item_view["audit"]["status"], "approved")
        self.assertEqual(item_view["warnings"], ["Check diagram resolution"])
        self.assertEqual(item_view["quality_checks"], [{"name": "test_check", "passed": True}])
        self.assertEqual(item_view["reference_text"], "Reference text for parity check")
        self.assertEqual(item_view["selected_solution_id"], "sol-1")

        # Check asset projection
        self.assertEqual(len(item_view["assets"]), 1)
        self.assertEqual(item_view["assets"][0]["id"], "fig-1")
        self.assertEqual(item_view["assets"][0]["section"], "body")
        self.assertEqual(item_view["assets"][0]["path"], "assets/fig1.png")

    def test_legacy_job_view_parity(self):
        manifest = JobManifest(
            id="a" * 32,
            source_id="src-1",
            source="2025-06-mock",
            track="english",
            format="exam",
            record_ids=["q-1"],
            documents={},
            storage_version=2,
            subject="english",
        )
        record = CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id="q-1",
                display_code="Q1",
                subject="english",
                question_number="1",
                points=2.0,
            ),
            source=SourceDomain(source_id="src-1", type="exam", title="2025-06-mock"),
            body="Hello world",
        )
        review = ReviewState(record_id="q-1", applies_to_revision_id=record.provenance.revision_id)

        job_view = build_legacy_job_view(manifest, {"q-1": record}, {"q-1": review})

        self.assertEqual(job_view["id"], "a" * 32)
        self.assertEqual(job_view["storage_version"], 2)
        self.assertEqual(job_view["subject"], "english")
        self.assertEqual(len(job_view["items"]), 1)
        self.assertEqual(job_view["items"][0]["id"], "q-1")
        self.assertEqual(job_view["items"][0]["body"], "Hello world")

    def test_legacy_job_view_fails_closed_on_missing_record(self):
        manifest = JobManifest(
            id="b" * 32,
            source_id="src-1",
            source="2025-06-mock",
            track="english",
            format="exam",
            record_ids=["q-missing"],
            documents={},
            storage_version=2,
        )
        with self.assertRaises(ValueError) as ctx:
            build_legacy_job_view(manifest, {}, {})
        self.assertIn("Storage corruption", str(ctx.exception))


    def test_korean_context_and_set_id_parity(self):
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
        from _scripts_2.apps.exam.exam_processor.domain.models import new_item

        job = {
            "id": "c" * 32,
            "source_id": "src-korean",
            "source": "2026학년도 6월 모의평가",
            "format": "kice_korean",
            "subject": "국어",
            "track": "국어",
        }
        item = new_item("src-korean", "38-39", "38", "국어 문항 본문")
        item["question_set_id"] = "38-39"

        # Map to Canonical
        record = item_to_canonical(job, item)
        self.assertEqual(record.question.question_set_id, "38-39")

        # Project back to legacy item view
        review = ReviewState(record_id=item["id"], applies_to_revision_id=record.provenance.revision_id)
        projected = build_legacy_item_view(record, review, job)
        self.assertEqual(projected["context"], "38-39")
        self.assertEqual(projected["question_set_id"], "38-39")

        # Roundtrip back to Canonical
        roundtrip_record = item_to_canonical(job, projected)
        self.assertEqual(roundtrip_record.question.question_set_id, "38-39")

    def test_workbook_example_and_concept_subtype_parity(self):
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
        from _scripts_2.apps.exam.exam_processor.domain.models import new_item

        job = {
            "id": "d" * 32,
            "source_id": "src-math",
            "source": "한완기 2026",
            "format": "hanwangi_2026_probability",
            "subject": "수학",
            "workbook": {"id": "wb-1"},
        }
        # 1. Example
        example_item = new_item("src-math", "node-1", "01", "예제 본문")
        example_item["subtype"] = "example"
        example_item["section_code"] = "A1"
        example_record = item_to_canonical(job, example_item)
        self.assertEqual(example_record.question.content_kind, "example")
        self.assertEqual(example_record.source.source_set_id, "node-1")
        self.assertEqual(example_record.source.section_code, "A1")

        review_ex = ReviewState(record_id=example_item["id"], applies_to_revision_id=example_record.provenance.revision_id)
        proj_ex = build_legacy_item_view(example_record, review_ex, job)
        self.assertEqual(proj_ex["kind"], "question")
        self.assertEqual(proj_ex["subtype"], "example")
        self.assertEqual(proj_ex["context"], "node-1")
        self.assertEqual(proj_ex["section_code"], "A1")

        rt_ex = item_to_canonical(job, proj_ex)
        self.assertEqual(rt_ex.question.content_kind, "example")

        # 2. Concept
        concept_item = new_item("src-math", "node-1", "01", "개념 본문", kind="concept")
        concept_item["section_code"] = "A1"
        concept_record = item_to_canonical(job, concept_item)
        self.assertEqual(concept_record.question.content_kind, "concept")

        review_cp = ReviewState(record_id=concept_item["id"], applies_to_revision_id=concept_record.provenance.revision_id)
        proj_cp = build_legacy_item_view(concept_record, review_cp, job)
        self.assertEqual(proj_cp["kind"], "concept")
        self.assertEqual(proj_cp["subtype"], "")
    def test_runtime_state_separation_and_immutability(self):
        import hashlib
        import tempfile
        from _scripts_2.apps.exam.exam_processor.domain.job_runtime import JobRuntimeState
        from _scripts_2.apps.exam.exam_processor.pipeline.ai_activity import append, begin
        from _scripts_2.apps.exam.exam_processor.storage.store import Store

        def sha256(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = Store(root=tmpdir)
            job = {
                "id": "e" * 32,
                "source_id": "src-test",
                "source": "Test Exam",
                "subject": "english",
                "items": [
                    {
                        "id": "item-1",
                        "number": "1",
                        "body": "Question 1 body",
                        "points": 2.0,
                    }
                ],
            }
            store.save(job)
            job_dir = store.job_dir(job["id"])
            manifest_file = job_dir / "job.json"
            record_file = job_dir / "records" / "item-1.json"
            review_file = job_dir / "review" / "item-1.json"
            runtime_file = job_dir / "runtime.json"
            self.assertTrue(manifest_file.exists())
            self.assertTrue(record_file.exists())
            self.assertTrue(review_file.exists())

            manifest_hash_orig = sha256(manifest_file)
            record_hash_orig = sha256(record_file)
            review_hash_orig = sha256(review_file)

            # Mutate runtime state via ai_activity
            begin(store, job["id"], "extract", source="single")
            append(store, job["id"], "Processing item 1", phase="api")

            # Verify runtime.json created and updated
            self.assertTrue(runtime_file.exists())
            runtime_state = store.runtime.load(job_dir, job["id"])
            self.assertEqual(runtime_state.ai_progress["operation"], "extract")
            self.assertEqual(len(runtime_state.ai_log), 1)
            self.assertEqual(runtime_state.ai_log[0]["message"], "Processing item 1")

            # Verify manifest, record, review SHA256 are completely unchanged
            self.assertEqual(sha256(manifest_file), manifest_hash_orig)
            self.assertEqual(sha256(record_file), record_hash_orig)
            self.assertEqual(sha256(review_file), review_hash_orig)

            # Verify get() projects runtime fields into legacy view
            job_view = store.get(job["id"])
            self.assertEqual(job_view["ai_progress"]["operation"], "extract")
            self.assertEqual(len(job_view["ai_log"]), 1)

    def test_ai_process_audit_canonical_native(self):
        import tempfile
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import process

        with tempfile.TemporaryDirectory() as tmpdir:
            store = Store(root=tmpdir)
            job = {
                "id": "f" * 32,
                "source_id": "src-audit",
                "source": "Audit Test",
                "subject": "국어",
                "items": [
                    {
                        "id": "item-1",
                        "number": "1",
                        "body": "본문 전사 내용",
                        "solution": "해설 전사 내용",
                        "regions": [{"image": "body.png", "page": 1, "bbox": [0, 0, 100, 100]}],
                        "solution_regions": [],
                        "transcription_pending": [],
                    }
                ],
            }
            store.save(job)
            job_dir = store.job_dir(job["id"])
            (job_dir / "regions").mkdir(parents=True, exist_ok=True)
            from PIL import Image
            Image.new("RGB", (10, 10), "white").save(job_dir / "regions" / "body.png")

            rec_before = store.records.get(job_dir, "item-1")
            rev_before = store.reviews.get(job_dir, "item-1")
            self.assertEqual(rec_before.review.source_audit, "pending")

            def mock_caller(_payload):
                return {
                    "data": {"result": "no_difference", "issues": []},
                    "provider": "mock",
                    "configured_model": "mock-v1",
                }

            process(store, job["id"], "item-1", rec_before.provenance.revision_id, "audit", caller=mock_caller)

            rec_after = store.records.get(job_dir, "item-1")
            rev_after = store.reviews.get(job_dir, "item-1")

            # Revision is unchanged for audit
            self.assertEqual(rec_after.provenance.revision_id, rec_before.provenance.revision_id)
            # source_audit is updated on record
            self.assertEqual(rec_after.review.source_audit, "no_difference")
            # audit evidence is saved on ReviewState
            self.assertEqual(rev_after.audit["result"], "no_difference")
            self.assertEqual(rev_after.audit["status"], "completed")

    def test_work_queue_status_immutability(self):
        import hashlib
        import tempfile
        from _scripts_2.apps.exam.exam_processor.pipeline.work_queue import WorkQueue
        from _scripts_2.apps.exam.exam_processor.storage.store import Store

        def sha256(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = Store(root=tmpdir)
            job = {
                "id": "1" * 32,
                "source_id": "src-queue-test",
                "source": "Queue Test",
                "subject": "국어",
                "items": [
                    {
                        "id": "item-1",
                        "number": "1",
                        "body": "본문",
                        "solution": "해설",
                        "regions": [{"image": "body.png", "page": 1, "bbox": [0, 0, 100, 100]}],
                        "solution_regions": [],
                        "transcription_pending": [],
                    }
                ],
            }
            store.save(job)
            job_dir = store.job_dir(job["id"])
            manifest_file = job_dir / "job.json"
            record_file = job_dir / "records" / "item-1.json"
            review_file = job_dir / "review" / "item-1.json"
            runtime_file = job_dir / "runtime.json"

            manifest_hash_orig = sha256(manifest_file)
            record_hash_orig = sha256(record_file)
            review_hash_orig = sha256(review_file)

            queue = WorkQueue(store)
            queue.prepare(job["id"], "audit")

            # Check runtime.json updated with queue
            self.assertTrue(runtime_file.exists())
            runtime_state = store.runtime.load(job_dir, job["id"])
            self.assertEqual(runtime_state.queue["status"], "running")
            self.assertEqual(len(runtime_state.queue["entries"]), 1)

            # Check job.json, records, reviews are unchanged by queue prepare
            self.assertEqual(sha256(manifest_file), manifest_hash_orig)
            self.assertEqual(sha256(record_file), record_hash_orig)
            self.assertEqual(sha256(review_file), review_hash_orig)

            # Pause queue
            queue.pause(job["id"])
            runtime_state = store.runtime.load(job_dir, job["id"])
            self.assertTrue(runtime_state.queue["stop_requested"])

            # Check hashes still unchanged
            self.assertEqual(sha256(manifest_file), manifest_hash_orig)
            self.assertEqual(sha256(record_file), record_hash_orig)
            self.assertEqual(sha256(review_file), review_hash_orig)


if __name__ == "__main__":
    unittest.main()

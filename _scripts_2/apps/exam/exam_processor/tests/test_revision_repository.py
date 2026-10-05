import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    QuestionDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState
from _scripts_2.apps.exam.exam_processor.storage.revision_repository import RevisionRepository


class RevisionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp_dir.name)
        self.repo = RevisionRepository()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_snapshot(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id="q-001",
                display_code="Q1",
                subject="math",
                points=4.0,
            ),
            source=SourceDomain(
                source_id="src-1",
                type="exam",
                title="2025-csat",
            ),
            body="Sample math question",
        )
        review = ReviewState(
            record_id="q-001",
            applies_to_revision_id="rev-001",
            note="Snapshot test note",
        )

        self.repo.save_snapshot(self.job_dir, "q-001", "rev-001", record, review)

        snap_file = self.repo.revisions_dir(self.job_dir, "q-001") / "rev-001.json"
        self.assertTrue(snap_file.is_file())

        result = self.repo.get_snapshot(self.job_dir, "q-001", "rev-001")
        self.assertIsNotNone(result)
        loaded_record, loaded_review = result
        self.assertEqual(loaded_record.question.question_id, "q-001")
        self.assertEqual(loaded_record.question.subject, "math")
        self.assertEqual(loaded_record.body, "Sample math question")
        self.assertEqual(loaded_review.note, "Snapshot test note")

    def test_get_nonexistent_returns_none(self):
        result = self.repo.get_snapshot(self.job_dir, "q-001", "rev-nonexistent")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

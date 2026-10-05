import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState
from _scripts_2.apps.exam.exam_processor.storage.review_repository import ReviewRepository


class ReviewRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp_dir.name)
        self.repo = ReviewRepository()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_review_state(self):
        state = ReviewState(
            record_id="q-001",
            applies_to_revision_id="rev-canon-123",
            published_revision_id="rev-canon-123",
            note="Sample teacher review note",
            audit={"status": "approved", "timestamp": "2026-09-21T12:00:00Z"},
            warnings=["Check question number formatting"],
            quality_checks=[{"name": "math_check", "passed": True}],
            ai_runs=[{"model": "gemini-1.5-pro", "action": "latex_ocr"}],
        )
        self.repo.save(self.job_dir, state)

        rev_file = self.repo.review_dir(self.job_dir) / "q-001.json"
        self.assertTrue(rev_file.is_file())

        loaded = self.repo.get(self.job_dir, "q-001")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.record_id, "q-001")
        self.assertEqual(loaded.applies_to_revision_id, "rev-canon-123")
        self.assertEqual(loaded.published_revision_id, "rev-canon-123")
        self.assertEqual(loaded.note, "Sample teacher review note")
        self.assertEqual(loaded.audit.get("status"), "approved")
        self.assertEqual(len(loaded.warnings), 1)
        self.assertEqual(len(loaded.ai_runs), 1)

    def test_save_rotates_state_revision_id(self):
        state = ReviewState(record_id="q-001", applies_to_revision_id="r1")
        initial_state_rev = state.state_revision_id

        self.repo.save(self.job_dir, state)
        save1_state_rev = state.state_revision_id
        self.assertNotEqual(initial_state_rev, save1_state_rev)

        # Subsequent save updates state_revision_id again
        self.repo.save(self.job_dir, state)
        save2_state_rev = state.state_revision_id
        self.assertNotEqual(save1_state_rev, save2_state_rev)

    def test_get_nonexistent_returns_default_empty_state(self):
        loaded = self.repo.get(self.job_dir, "nonexistent")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.record_id, "nonexistent")
        self.assertEqual(loaded.applies_to_revision_id, "")

    def test_get_does_not_create_directory(self):
        review_dir = self.job_dir / "review"
        self.assertFalse(review_dir.exists())
        self.repo.get(self.job_dir, "nonexistent")
        self.assertFalse(review_dir.exists())

    def test_get_many_and_save_many(self):
        s1 = ReviewState(record_id="q-001", applies_to_revision_id="r1")
        s2 = ReviewState(record_id="q-002", applies_to_revision_id="r2")
        self.repo.save_many(self.job_dir, [s1, s2])

        many = self.repo.get_many(self.job_dir, ["q-001", "q-002"])
        self.assertEqual(len(many), 2)
        self.assertEqual(many["q-001"].applies_to_revision_id, "r1")
        self.assertEqual(many["q-002"].applies_to_revision_id, "r2")


if __name__ == "__main__":
    unittest.main()

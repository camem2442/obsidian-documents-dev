import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    QuestionDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_processor.storage.record_repository import RecordRepository


class RecordRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp_dir.name)
        self.repo = RecordRepository()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _sample_record(self, qid: str = "q-001") -> CanonicalQuestionRecord:
        return CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id=qid,
                display_code="Q1",
                subject="english",
                question_number="1",
                points=3.0,
            ),
            source=SourceDomain(
                source_id="src-1",
                type="exam",
                title="2025-06-mock",
            ),
            body="Sample question body",
            solution="Sample question solution",
        )

    def test_save_and_get_record(self):
        record = self._sample_record("q-101")
        self.repo.save(self.job_dir, record)

        rec_file = self.repo.records_dir(self.job_dir) / "q-101.json"
        self.assertTrue(rec_file.is_file())

        loaded = self.repo.get(self.job_dir, "q-101")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.question.question_id, "q-101")
        self.assertEqual(loaded.question.subject, "english")
        self.assertEqual(loaded.question.points, 3.0)
        self.assertEqual(loaded.body, "Sample question body")

    def test_save_strict_validation_rejects_invalid_record(self):
        record = self._sample_record("q-invalid")
        record.schema_version = "invalid_version_999"
        with self.assertRaises(ValueError) as ctx:
            self.repo.save(self.job_dir, record)
        self.assertIn("Invalid CanonicalQuestionRecord", str(ctx.exception))

    def test_get_nonexistent_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.repo.get(self.job_dir, "nonexistent")

    def test_get_many_fails_closed_on_missing_record(self):
        r1 = self._sample_record("q-001")
        r2 = self._sample_record("q-002")
        self.repo.save_many(self.job_dir, [r1, r2])

        # Success case
        many = self.repo.get_many(self.job_dir, ["q-001", "q-002"])
        self.assertEqual(len(many), 2)
        self.assertIn("q-001", many)
        self.assertIn("q-002", many)

        # Fail-closed case: missing record must raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.repo.get_many(self.job_dir, ["q-001", "q-missing"])
        self.assertIn("Record 'q-missing' not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

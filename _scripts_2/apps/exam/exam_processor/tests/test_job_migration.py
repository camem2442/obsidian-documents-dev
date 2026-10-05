import json
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor.storage.migration import migrate_job
from _scripts_2.apps.exam.exam_processor.storage.store import Store


class JobMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = Store(root=self.root / "data", output=self.root / "exports")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_v1_job(self, job_id: str = "a" * 32) -> Path:
        job_dir = self.root / "data" / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        v1_data = {
            "id": job_id,
            "target": "exam",
            "subject": "english",
            "exam_type": "mock",
            "test_id": "2025-06-mock",
            "status": "in_progress",
            "items": [
                {
                    "id": f"{job_id}-item-1",
                    "number": 1,
                    "type": "multiple_choice",
                    "points": 2,
                    "body": "Question 1 body text",
                    "answer": "2",
                    "solution": "Solution 1 text",
                    "choices": ["A", "B", "C", "D", "E"],
                    "note": "Initial note",
                    "published_revision": "pub-rev-001",
                    "audit": {"status": "needs_review"},
                },
                {
                    "id": f"{job_id}-item-2",
                    "number": 2,
                    "type": "multiple_choice",
                    "points": 3,
                    "body": "Question 2 body text",
                    "answer": "4",
                    "solution": "Solution 2 text",
                    "choices": ["1", "2", "3", "4", "5"],
                },
            ],
        }
        with open(job_dir / "job.json", "w", encoding="utf-8") as f:
            json.dump(v1_data, f, ensure_ascii=False, indent=2)
        return job_dir

    def test_dual_read_does_not_modify_disk(self):
        job_id = "1" * 32
        job_dir = self._create_v1_job(job_id)
        job = self.store.get(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(len(job.get("items", [])), 2)
        self.assertEqual(job["items"][0]["body"], "Question 1 body text")
        self.assertEqual(job["items"][0]["published_revision"], "pub-rev-001")

        # Confirm disk is still v1 (no records/ folder, job.json still has items)
        self.assertFalse((job_dir / "records").exists())
        with open(job_dir / "job.json", "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        self.assertIn("items", disk_data)
        self.assertFalse((job_dir / "job.v1.backup.json").exists())

    def test_migrate_job_creates_v2_and_backup(self):
        job_id = "2" * 32
        job_dir = self._create_v1_job(job_id)
        res = migrate_job(job_dir, dry_run=False)
        self.assertEqual(res.get("status"), "migrated")

        # Check backup file exists
        self.assertTrue((job_dir / "job.v1.backup.json").exists())

        # Check records, review, revisions folders exist
        self.assertTrue((job_dir / "records").exists())
        self.assertTrue((job_dir / "review").exists())
        self.assertTrue((job_dir / "revisions").exists())

        # Check job.json has no items and storage_version == 2
        with open(job_dir / "job.json", "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        self.assertEqual(manifest_data.get("storage_version"), 2)
        self.assertNotIn("items", manifest_data)
        self.assertEqual(manifest_data.get("record_ids"), [f"{job_id}-item-1", f"{job_id}-item-2"])

        # Check records files exist
        self.assertTrue((job_dir / "records" / f"{job_id}-item-1.json").exists())
        self.assertTrue((job_dir / "records" / f"{job_id}-item-2.json").exists())

        # Read back through store facade
        job = self.store.get(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.get("storage_version"), 2)
        self.assertEqual(len(job.get("items", [])), 2)
        self.assertEqual(job["items"][0]["body"], "Question 1 body text")
        self.assertEqual(job["items"][0]["note"], "Initial note")
        self.assertEqual(job["items"][0]["published_revision"], "pub-rev-001")

    def test_migrate_on_write(self):
        job_id = "3" * 32
        job_dir = self._create_v1_job(job_id)
        job = self.store.get(job_id)
        job["items"][0]["body"] = "Updated body via store.save"
        job["items"][0]["published_revision"] = "pub-rev-999"

        self.store.save(job)

        # Now disk must be v2
        self.assertTrue((job_dir / "records").exists())
        with open(job_dir / "job.json", "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        self.assertEqual(manifest_data.get("storage_version"), 2)
        self.assertNotIn("items", manifest_data)

        # Re-read through store
        reloaded = self.store.get(job_id)
        self.assertEqual(reloaded["items"][0]["body"], "Updated body via store.save")
        self.assertEqual(reloaded["items"][0]["published_revision"], "pub-rev-999")


if __name__ == "__main__":
    unittest.main()

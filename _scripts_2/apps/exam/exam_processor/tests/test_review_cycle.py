import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from _scripts_2.apps.exam.exam_processor.pipeline.review_cycle import (
    SCHEMA, compare, evaluate_case, file_hash, main, run,
)

MODULE = "_scripts_2.apps.exam.exam_processor.pipeline.review_cycle"


class ReviewCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "regions").mkdir()
        self.crop = self.root / "regions" / "crop.png"
        self.crop.write_bytes(b"source image fixture")
        self.pdf = self.root / "original.pdf"
        self.pdf.write_bytes(b"original fixture")
        self.item = {"id": "item", "revision": "r1", "body": "정답률 94%", "solution": "정답③",
                     "regions": [{"image": "crop.png"}], "transcription_pending": []}
        self.case = {"id": "q1", "reviewer": "assistant", "evidence": "original page reviewed",
                     "data_root": str(self.root), "job_id": "job", "item_id": "item",
                     "source_regions": {"crop.png": file_hash(self.crop)},
                     "checks": [{"id": "rate", "field": "body", "matches": "94%"},
                                {"id": "no-copy", "field": "body", "absent": "92%"}]}
        self.spec = {"schema": SCHEMA, "sources": [{"path": str(self.pdf), "sha256": file_hash(self.pdf)}],
                     "cases": [self.case]}
        self.store = Mock()
        self.store.get.return_value = {"id": "job"}
        self.store.item.return_value = self.item
        self.store.job_dir.return_value = self.root
        self.factory = patch(MODULE + ".Store", return_value=self.store)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_negative_source_check_detects_copied_example(self):
        self.item["body"] = "정답률 92%"
        result = run(self.spec)
        self.assertEqual(result["failed_checks"], 2)
        self.assertFalse(result["passed"])
        self.assertNotIn("approved", json.dumps(result))

    def test_recurrence_keeps_failures_and_detects_regression(self):
        self.item["body"] = "정답률 누락"
        before = run(self.spec)
        self.item["body"] = "정답률 94%"
        fixed = run(self.spec, before)
        self.assertTrue(fixed["passed"])
        self.assertEqual(fixed["comparison"]["fixed"], [("q1", "rate")])
        self.item["body"] = "정답률 92%"
        after = run(self.spec, fixed)
        self.assertEqual(len(after["comparison"]["regressions"]), 2)
        self.assertFalse(after["passed"])

    def test_relaxed_or_removed_checks_cannot_become_success(self):
        before = run(self.spec)
        self.case["checks"][0]["matches"] = ".*"
        self.case["checks"].pop()
        result = run(self.spec, before)
        self.assertFalse(result["passed"])
        self.assertEqual(result["comparison"]["changed_checks"], [("q1", "rate")])
        self.assertEqual(result["comparison"]["missing_checks"], [("q1", "no-copy")])
        self.assertFalse(run(self.spec, result)["passed"])

    def test_changed_source_or_crop_is_rejected(self):
        self.crop.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "crop changed"):
            run(self.spec)
        self.case["source_regions"]["crop.png"] = file_hash(self.crop)
        self.pdf.write_bytes(b"changed original")
        with self.assertRaisesRegex(ValueError, "Original source"):
            run(self.spec)

    def test_duplicate_checks_and_pending_transcription_rejected(self):
        self.case["checks"].append(copy.deepcopy(self.case["checks"][0]))
        with self.assertRaises(ValueError):
            evaluate_case(self.item, self.case)
        self.case["checks"].pop()
        self.item["transcription_pending"] = ["solution"]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            run(self.spec)

    def test_checkpoint_never_overwrites_prior_result(self):
        spec, output = self.root / "spec.json", self.root / "result.json"
        spec.write_text(json.dumps(self.spec))
        output.write_text("prior failed evidence")
        with patch("sys.argv", ["review_cycle", "--spec", str(spec), "--output", str(output)]):
            with self.assertRaises(FileExistsError):
                main()
        self.assertEqual(output.read_text(), "prior failed evidence")
        self.store.review.assert_not_called()

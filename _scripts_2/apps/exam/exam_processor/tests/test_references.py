"""Optional local integration checks against the user's read-only reference files."""
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor import config
from _scripts_2.apps.exam.exam_processor.store import Store
from _scripts_2.apps.exam.exam_processor.models import digest
from _scripts_2.apps.exam.exam_processor.ingestion.formats.kice_korean import reflow_saved_draft


class ReferenceTests(unittest.TestCase):
    def test_supplied_markdown_sets(self):
        if not all((config.KS_ROOT / sample["path"]).is_file() for sample in config.SAMPLES.values()):
            self.skipTest("Local vault references unavailable")
        expected = {"media": ["40", "41", "42", "43"], "hwajak1": ["35", "36", "37"], "hwajak2": ["38", "39", "40", "41", "42"]}
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/"data", Path(temp)/"out")
            for key, sample in config.SAMPLES.items():
                path = config.KS_ROOT / sample["path"]
                before = digest(path)
                job = store.import_file(path, "2026학년도 6월 모의평가", sample["track"], solution=str(config.KS_ROOT/sample["solution"]) if sample.get("solution") else "")
                self.assertEqual([i["number"] for i in job["items"] if i["kind"] == "question"], expected[key])
                self.assertEqual(job["warnings"], [])
                self.assertEqual(digest(path), before)
                if key == "media":
                    self.assertEqual([i["answer"] for i in job["items"][1:]], ["⑤", "①", "④", "①"])

    def test_hwajak_pdf_full_set(self):
        path = config.KS_ROOT / config.SAMPLE_PDF
        if not path.is_file():
            self.skipTest("Local PDF unavailable")
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/"data", Path(temp)/"out")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            questions = [i for i in job["items"] if i["kind"] == "question"]
            self.assertEqual([i["number"] for i in questions], [str(n) for n in range(35,46)])
            self.assertEqual(job["warnings"], [])
            self.assertEqual(len([i for i in job["items"] if i["kind"] == "passage"]),3)
            self.assertTrue(all(i["regions"] for i in job["items"]))
            shared = next(i for i in job["items"] if i["number"] == "38-42")
            self.assertGreaterEqual(len(shared["regions"]),2)
            self.assertNotIn("제 1 교시", shared["body"])
            draft = next(i for i in job["items"] if i["number"] == "43-45")
            self.assertIn("\n\n지역 경제의 측면에서도", draft["body"])
            self.assertIn("\n\n현재 미식 관광의", draft["body"])
            self.assertIn("초고이다.", draft["body"])
            old_draft = dict(draft, body=draft["reference_text"])
            restored = reflow_saved_draft(job, old_draft, store.job_dir(job["id"]))
            self.assertIn("초고이다.", restored)
            self.assertIn("\n\n지역 경제의 측면에서도", restored)
            last = questions[-1]
            self.assertEqual(len([line for line in last["body"].splitlines() if line.startswith(tuple("①②③④⑤"))]), 5)

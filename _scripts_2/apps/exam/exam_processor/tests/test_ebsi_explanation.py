import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor import config
from _scripts_2.apps.exam.exam_processor.ingestion.formats.base import SolutionFormatRequest
from _scripts_2.apps.exam.exam_processor.ingestion.formats.ebsi_korean_explanation import (
    EBSiKoreanExplanationFormat,
    clean_solution_text,
)
from _scripts_2.apps.exam.exam_processor.store import Store


class EBSiExplanationTests(unittest.TestCase):
    def pair(self, code):
        problem = config.KS_ROOT / f"1 국어/assets/화작 기출/추출/{code}.pdf"
        # Collected files are migrated to academic-year folders and filenames.
        solution = config.KS_ROOT / f"99 기출/국어/20{code[:2]}/{code} 국어(화법과 작문)(해설).pdf"
        if not problem.is_file() or not solution.is_file():
            self.skipTest(f"Local {code} problem/explanation pair unavailable")
        return problem, solution

    def verify_pair(self, code):
        problem, solution = self.pair(code)
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "data", Path(root) / "output")
            job = store.import_file(problem, f"20{code[:2]}학년도 6월 모의평가", "화법과 작문", solution=str(solution))
        questions = [item for item in job["items"] if item["kind"] == "question"]
        self.assertEqual(job["solution_format"], "ebsi_korean_explanation")
        self.assertEqual([item["number"] for item in questions], [str(number) for number in range(35, 46)])
        self.assertTrue(all(item["solution_match"]["status"] == "matched" for item in questions))
        self.assertTrue(all(len(item["solution_candidates"]) == 1 for item in questions))
        self.assertTrue(all(item["solution_candidates"][0]["answer"] for item in questions))
        solutions = "\n".join(item["solution_candidates"][0]["body"] for item in questions)
        self.assertNotIn("\uf149", solutions)
        self.assertNotIn("사이 테스", solutions)
        self.assertNotIn("질 문", solutions)
        self.assertNotIn("내 용", solutions)
        self.assertNotIn("하 는", solutions)
        self.assertNotIn("또한이 자료", solutions)
        self.assertNotIn("하 여", solutions)
        self.assertNotIn("만으 로", solutions)
        self.assertNotIn("부속 서", solutions)
        self.assertNotIn("적절하 지", solutions)

    def test_solution_text_spacing_artifacts_are_cleaned(self):
        dirty = "수 월해진 대출·반납에서 반영되 었다. 명확하 게 제시되 어 있 다. ㉰를 기준으 로 적절하 다."
        clean = clean_solution_text(dirty)
        for fragment in (
            "수 월해진",
            "반영되 었",
            "하 게",
            "제시되 어",
            "있 다",
            "기준으 로",
            "적절하 다",
        ):
            self.assertNotIn(fragment, clean)
        self.assertIn("수월해진", clean)
        self.assertIn("반영되었다", clean)
        self.assertIn("명확하게", clean)
        self.assertIn("제시되어 있다", clean)
        self.assertIn("기준으로", clean)
        self.assertIn("적절하다", clean)

    def test_2506_pair(self):
        self.verify_pair("2506")

    def test_2606_pair(self):
        self.verify_pair("2606")

    def test_academic_year_mismatch_is_rejected(self):
        _, solution = self.pair("2606")
        with tempfile.TemporaryDirectory() as root:
            request = SolutionFormatRequest(
                path=solution,
                source_id="fixture",
                track="화법과 작문",
                workdir=Path(root),
                expected_academic_year="2025",
            )
            with self.assertRaisesRegex(ValueError, "문제는 2025학년도인데 해설은 2026학년도"):
                EBSiKoreanExplanationFormat().parse(request)


if __name__ == "__main__":
    unittest.main()

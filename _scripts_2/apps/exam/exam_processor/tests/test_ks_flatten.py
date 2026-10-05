import json
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.scripts.ks_flatten import (
    discover_candidates,
    discover_format_review_candidates,
    flatten_file,
    heuristic_format_review,
    infer_display_code,
    review_format_file,
    source_title_from_origin,
    summarize,
)


class TestKSFlatten(unittest.TestCase):
    def test_korean_hyphenated_code_generates_source_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "1 국어" / "3 독서" / "2606 수소 에너지" / "11.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "**11. 설명으로 가장 적절한 것은?**\n\n① A\n② B\n③ C\n④ D\n⑤ E\n",
                encoding="utf-8",
            )

            result = flatten_file(note, root)

            self.assertEqual(result.record.source.title, "2026학년도 6월 평가원")
            self.assertEqual(result.record.origin.question_number, "11")
            self.assertIn("## 2026학년도 6월 평가원 11번", result.markdown)
            self.assertEqual(source_title_from_origin(result.record.origin), "2026학년도 6월 평가원")

    def test_grammar_note_splits_body_and_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "2 문법" / "2 기출 분석" / "1 음운" / "2306.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "\n".join([
                    "35. 다음 중 음운 변동의 사례로 적절한 것은?",
                    "",
                    "① 값이 ② 같이 ③ 닫는 ④ 좋고 ⑤ 읽는",
                    "",
                    "알겠습니다. 이제 이 문항을 분석해 보겠습니다.",
                    "",
                    "정답: ③",
                ]),
                encoding="utf-8",
            )

            result = flatten_file(note, root)
            self.assertIn("음운 변동", result.record.body)
            self.assertNotIn("분석해 보겠습니다", result.record.body)
            self.assertIn("정답", result.record.solution)
            self.assertEqual(result.record.question.subject, "문법")
            self.assertEqual(result.record.question.display_code, "2306")
            self.assertEqual(result.record.question.answer, "③")
            self.assertEqual(result.review_status(), "pass")
            self.assertGreater(result.metrics["body_chars"], 20)
            self.assertTrue(result.metrics["markdown_has_all_section_markers"])
            CanonicalQuestionRecord.from_dict(json.loads(result.record.to_json()))
            self.assertIn("<!-- SECTION:PROBLEM_START -->", result.markdown)
            self.assertIn("<!-- SECTION:SOLUTION_START -->", result.markdown)

    def test_math_image_note_is_kept_but_flagged_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "4 수학" / "2 문제" / "8 도형" / "250610.md"
            note.parent.mkdir(parents=True)
            note.write_text("![[Pasted image 250610.png]]\n", encoding="utf-8")

            result = flatten_file(note, root)
            codes = {issue.code for issue in result.issues}
            self.assertIn("embedded_assets_not_copied", codes)
            self.assertIn("image_heavy_or_image_only", codes)
            self.assertEqual(result.review_status(), "needs_review")
            self.assertEqual(result.metrics["image_link_count"], 1)
            self.assertEqual(result.record.review.source_audit, "pending")
            self.assertIn("![[Pasted image 250610.png]]", result.record.body)
            CanonicalQuestionRecord.from_dict(json.loads(result.record.to_json()))

    def test_discovery_skips_generated_merge_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "3 영어" / "0 독해" / "3 순서" / "기출" / "260936.md"
            bad = root / "3 영어" / "99 병합" / "260936.md"
            good.parent.mkdir(parents=True)
            bad.parent.mkdir(parents=True)
            good.write_text("36. 글의 순서로 가장 적절한 것은?", encoding="utf-8")
            bad.write_text("generated", encoding="utf-8")

            self.assertEqual(infer_display_code(good, root), "260936")
            self.assertEqual(discover_candidates(root, {"영어"}), [good])

    def test_review_flags_existing_section_markers_and_duplicate_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "4 수학" / "2 문제" / "1 지수로그" / "260621.md"
            second = root / "4 수학" / "2 문제" / "2 함수" / "260621.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text(
                "\n".join([
                    "<!-- SECTION:PROBLEM_START -->",
                    "21. 이미 평탄화된 문제인가?",
                    "① 예 ② 아니오 ③ 보류 ④ 삭제 ⑤ 승인",
                    "<!-- SECTION:PROBLEM_END -->",
                ]),
                encoding="utf-8",
            )
            second.write_text("21. 같은 표시 코드 문제\n① A ② B ③ C ④ D ⑤ E", encoding="utf-8")

            first_result = flatten_file(first, root)
            second_result = flatten_file(second, root)
            self.assertIn("source_already_has_section_markers", {issue.code for issue in first_result.issues})
            self.assertEqual(first_result.review_status(), "needs_review")
            report = summarize([first_result, second_result], root)
            self.assertIn("260621", report["duplicate_display_codes"])
            self.assertEqual(report["by_review_status"]["needs_review"], 1)

    def test_format_review_collects_passage_question_and_study_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "1 국어" / "3 독서" / "2606 수소 에너지"
            folder.mkdir(parents=True)
            passage = folder / "수소 에너지.md"
            question = folder / "11.md"
            study = folder / "11 추론.md"
            ignored = root / "1 국어" / "3 독서" / "0 학습" / "일반론.md"
            ignored.parent.mkdir(parents=True)
            passage.write_text("# [10~13] 다음 글을 읽고 물음에 답하시오.\n\n지문", encoding="utf-8")
            question.write_text(
                "**11. 적절한 것은?**\n\n① A\n② B\n③ C\n④ D\n⑤ E\n\n알겠습니다. 분석하겠습니다.\n\n### 선지 분석",
                encoding="utf-8",
            )
            study.write_text("네, 좋은 질문입니다.\n\n### 갈등 추론\n\n실질 내용", encoding="utf-8")
            ignored.write_text("일반 학습 노트", encoding="utf-8")

            self.assertEqual(
                discover_format_review_candidates(root),
                sorted([passage, question, study]),
            )
            self.assertEqual(heuristic_format_review(passage, root, passage.read_text())["role"], "passage")
            question_review = heuristic_format_review(question, root, question.read_text())
            self.assertEqual(question_review["role"], "question")
            self.assertIn("embedded_ai_response", question_review["format_variants"])
            self.assertEqual(question_review["suggested_header"], "## 2026학년도 6월 평가원 11번")
            self.assertEqual(heuristic_format_review(study, root, study.read_text())["role"], "study_note")

    def test_ai_format_review_validates_exact_preamble_span(self):
        class FakeClient:
            def generate(self, **kwargs):
                return json.dumps({
                    "role": "question",
                    "confidence": 0.97,
                    "reasoning": "개별 문항 뒤에 AI 해설이 결합되어 있다.",
                    "format_variants": ["question_with_solution"],
                    "source_title": "2026학년도 6월 평가원",
                    "question_numbers": ["11"],
                    "question_range": None,
                    "substantive_start": {
                        "exact_text": "### 선지 분석",
                        "reason": "실제 판단 근거가 시작된다.",
                    },
                    "remove_spans": [{
                        "kind": "response_preamble",
                        "exact_text": "알겠습니다. 11번 문제를 분석하겠습니다.\n\n---\n",
                        "confidence": 0.99,
                        "reason": "응답 인사와 작업 예고이다.",
                    }],
                    "warnings": [],
                }, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "1 국어" / "3 독서" / "2606 수소 에너지" / "11.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "**11. 적절한 것은?**\n\n① A\n② B\n③ C\n④ D\n⑤ E\n\n"
                "알겠습니다. 11번 문제를 분석하겠습니다.\n\n---\n\n### 선지 분석\n\n①은 틀리다.",
                encoding="utf-8",
            )

            result = review_format_file(note, root, use_ai=True, client=FakeClient())

            self.assertEqual(result.review_status(), "reviewed")
            self.assertEqual(result.ai_review["role"], "question")
            self.assertTrue(result.ai_review["substantive_start"]["validated"])
            span = result.ai_review["remove_spans"][0]
            self.assertTrue(span["validated"])
            self.assertEqual(note.read_text(encoding="utf-8")[span["start"]:span["end"]], span["exact_text"])

    def test_ai_format_review_rejects_span_containing_exam_set(self):
        class FakeClient:
            def generate(self, **kwargs):
                return json.dumps({
                    "role": "question_set",
                    "confidence": 0.95,
                    "reasoning": "세트 파일이다.",
                    "format_variants": [],
                    "source_title": "2026학년도 6월 평가원",
                    "question_numbers": ["10", "11"],
                    "question_range": {"start": "10", "end": "13"},
                    "substantive_start": None,
                    "remove_spans": [{
                        "kind": "response_preamble",
                        "exact_text": "[10~13] 다음 글을 읽고 물음에 답하시오.",
                        "confidence": 0.99,
                        "reason": "잘못된 제거 후보",
                    }],
                    "warnings": [],
                }, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "1 국어" / "3 독서" / "2606 수소 에너지" / "세트.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "[10~13] 다음 글을 읽고 물음에 답하시오.\n\n지문\n\n"
                "**10. 문제?**\n① A\n② B\n③ C\n④ D\n⑤ E",
                encoding="utf-8",
            )

            result = review_format_file(note, root, use_ai=True, client=FakeClient())

            self.assertEqual(result.review_status(), "needs_review")
            self.assertFalse(result.ai_review["remove_spans"][0]["validated"])
            self.assertEqual(result.ai_review["remove_spans"][0]["blocked_reason"], "protected_exam_content")


if __name__ == "__main__":
    unittest.main()

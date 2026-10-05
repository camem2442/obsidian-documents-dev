import os
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor.ai import symbol_differences
from _scripts_2.apps.exam.exam_processor.text_layout import normalize_markdown
from _scripts_2.apps.exam.exam_processor.ingestion.pdf_styles import (
    apply_group_ranges,
    apply_underlines,
    detect_source_styles,
)
from _scripts_2.apps.exam.exam_processor.ai import (check_boxes, check_material, classify_issue,
                               contradicted_by_geometry, contradicted_by_preserved_material,
                               contradicted_by_exact_text, contradicted_by_stored_text,
                               contradicted_by_stored_underline)

from _scripts_2.apps.exam.exam_processor import config as _exam_config

KS_ROOT = _exam_config.KS_ROOT


class ReviewRegressions(unittest.TestCase):
    def test_geometry_restores_exact_underline_range(self):
        result, missing = apply_underlines("(<u>㉢ 자료 제시</u>)", [{"type": "underline", "text": "자료"}])
        self.assertEqual(result, "(㉢ <u>자료</u> 제시)")
        self.assertEqual(missing, [])

    def test_geometry_selects_repeated_word_by_source_line(self):
        source = "(㉠ 자료 제시)\n(㉡ 자료 제시)\n(㉢ 자료 제시)"
        styles = [{"type": "underline", "text": "자료", "line_text": line}
                  for line in ("(㉠자료 제시)", "(㉡자료 제시)", "(㉢자료 제시)")]
        result, missing = apply_underlines(source, styles)
        self.assertEqual(result, "(㉠ <u>자료</u> 제시)\n(㉡ <u>자료</u> 제시)\n(㉢ <u>자료</u> 제시)")
        self.assertEqual(missing, [])

    def test_geometry_uses_local_context_for_adjacent_repeated_word(self):
        source = "(㉡ 자료 제시) 이 자료를 보면\n(㉢ 자료 제시) 여기를 보면"
        styles = [{"type": "underline", "text": "자료", "line_text": "(㉡자료 제시)"},
                  {"type": "underline", "text": "자료", "line_text": "(㉢자료 제시)"}]
        result, missing = apply_underlines(source, styles)
        self.assertEqual(result, "(㉡ <u>자료</u> 제시) 이 자료를 보면\n(㉢ <u>자료</u> 제시) 여기를 보면")
        self.assertEqual(missing, [])

    def test_2606_detects_material_and_a_group(self):
        import pdfplumber
        path = KS_ROOT / "1 국어/assets/화작 기출/추출/2606.pdf"
        if not path.exists():
            self.skipTest("2606 source PDF is outside this checkout")
        with pdfplumber.open(path) as pdf:
            page1 = detect_source_styles(pdf.pages[0], 1, [45, 150, 397, 1060])
            page2 = detect_source_styles(pdf.pages[1], 2, [45, 800, 400, 1060])
        self.assertTrue(any(s["type"] == "underline" and s["text"] == "자료" and "㉠" in s["line_text"] for s in page1))
        group = next(s for s in page2 if s["type"] == "group" and s["label"] == "A")
        self.assertEqual(group["start_text"], "학생")
        self.assertIn(group["end_text"], {"때문입니다.", "때문입니다"})

    def test_group_range_markers_are_readable_by_downstream_ai(self):
        styles = [{"type": "group", "label": "B", "start_line": "청소년이 국가유산 지킴이 활동에 참여하기 위해서는",
                   "end_line": "주도적으로 동참할 수 있을 것이다."}]
        body = "[B] 청소년이 국가유산 지킴이 활동에 참여하기 위해서는 누리집에 신청서를 제출한 후 승인을 받아야 한다.\n\n청소년도 지역 국가유산에 관심을 가지고 주도적으로 동참할 수 있을 것이다."
        result, missing = apply_group_ranges(body, styles)
        self.assertEqual(missing, [])
        self.assertIn("<!-- GROUP:B START -->", result)
        self.assertIn("<!-- GROUP:B END -->", result)

    def test_2506_detects_left_bracket_group(self):
        import pdfplumber
        path = KS_ROOT / "1 국어/assets/화작 기출/추출/2506.pdf"
        if not path.exists():
            self.skipTest("2506 source PDF is outside this checkout")
        with pdfplumber.open(path) as pdf:
            styles = detect_source_styles(pdf.pages[3], 4, [45, 150, 416, 470])
        group = next(s for s in styles if s["type"] == "group" and s["label"] == "B")
        self.assertEqual(group["side"], "left")
        self.assertIn("청소년이", group["start_line"])
        self.assertIn("것이다", group["end_line"])

    def test_2606_merges_multiline_underlines(self):
        import pdfplumber
        from pathlib import Path
        path = KS_ROOT / "1 국어/assets/화작 기출/추출/2606.pdf"
        if not path.exists():
            self.skipTest("2606 source PDF is outside this checkout")
        with pdfplumber.open(path) as pdf:
            styles = detect_source_styles(pdf.pages[1], 2, [45, 150, 400, 720])
        texts = [s["text"] for s in styles if s["type"] == "underline"]
        self.assertTrue(any("자신의 일상을" in t and "현상이 되었다." in t for t in texts))
        self.assertTrue(any("그러나 인증 숏에는" in t and "주목할 필요가 있다." in t for t in texts))

    def test_2606_detects_closed_view_box(self):
        import pdfplumber
        from pathlib import Path
        path = KS_ROOT / "1 국어/assets/화작 기출/추출/2606.pdf"
        if not path.exists():
            self.skipTest("2606 source PDF is outside this checkout")
        with pdfplumber.open(path) as pdf:
            boxes = detect_source_styles(pdf.pages[2], 3, [45, 150, 410, 350])
        self.assertTrue(any(s["type"] == "box" and s["box_detection"] == "likely" for s in boxes))

    def test_box_content_mapping_is_separate_from_box_detection(self):
        styles = [{"type": "box", "bbox": [0, 0, 100, 100], "box_detection": "likely", "text_preview": "학생이 세운 계획"}]
        self.assertEqual(check_boxes("> [!quote] 보기\n> 학생이 세운 계획", styles)[0]["status"], "preserved")
        self.assertEqual(check_boxes("발문만 남음", styles)[0]["status"], "missing")

    def test_passage_frame_is_not_treated_as_quote_box(self):
        styles = [{"type": "box", "role": "passage_frame", "bbox": [0, 0, 100, 100],
                   "box_detection": "likely", "text_preview": "지문 전체"}]
        self.assertEqual(check_boxes("지문 일부", styles), [])

    def test_box_mapping_survives_markdown_structure(self):
        styles = [{"type": "box", "bbox": [0, 0, 100, 100], "box_detection": "likely",
                   "text_preview": "친구들의 의견 친구 1 문단과 문단이 자연스럽게 연결"}]
        markdown = "> [!quote] 보기\n> ◦ 친구들의 의견 **친구 1** : 문단과 문단이 자연스럽게 연결"
        self.assertEqual(check_boxes(markdown, styles)[0]["status"], "preserved")

    def test_pdf_geometry_overrides_false_missing_underline_issue(self):
        issue = {"message": "원본 이미지에서 '않은'은 밑줄이 없습니다.", "extracted": "적절하지 <u>않은</u> 것은?"}
        self.assertTrue(contradicted_by_geometry(issue, [{"type": "underline", "text": "않은"}]))

    def test_preserved_image_overrides_visual_shape_complaint_only(self):
        body = "<!-- MATERIAL:fig1 START -->\n![[assets/q/rev/fig1.png]]\n\n| 항목 | 값 |"
        assets = [{"id": "fig1", "path": "assets/q/rev/fig1.png"}]
        shape = {"message": "원본의 시각적 형태가 마크다운 표로 변환되어 다릅니다.",
                 "suggestion": "이미지 자체로 유지하십시오."}
        value = {"message": "표의 수치 32가 23으로 오탈자입니다.", "suggestion": "수치를 고치십시오."}
        self.assertTrue(contradicted_by_preserved_material(shape, body, assets))
        self.assertFalse(contradicted_by_preserved_material(value, body, assets))

    def test_identical_original_and_extracted_text_is_not_a_difference(self):
        issue = {"original": "③ ㉢을 바탕으로", "extracted": "③ ㉢을 바탕으로",
                 "message": "기호가 잘못 매칭되었습니다."}
        self.assertTrue(contradicted_by_exact_text(issue))

    def test_hallucinated_typo_is_dropped_when_stored_text_is_already_correct(self):
        item = {"body": "", "solution": "따라서 두 사건 A, B의 경우의 수가 같음을 보이면 된다."}
        issue = {
            "location": "solution",
            "original": "경우의 수가 같음을",
            "extracted": "경우가 수가 같음을",
            "suggestion": "경우의 수가 같음을",
            "message": "오자가 발생했습니다.",
        }
        self.assertTrue(contradicted_by_stored_text(issue, item))

    def test_real_typo_remains_when_extracted_is_in_stored_text(self):
        item = {"body": "", "solution": "따라서 경우가 수가 같음을 보이면 된다."}
        issue = {
            "location": "solution",
            "original": "경우의 수가 같음을",
            "extracted": "경우가 수가 같음을",
            "suggestion": "경우의 수가 같음을",
            "message": "오자",
        }
        self.assertFalse(contradicted_by_stored_text(issue, item))

    def test_false_missing_underline_dropped_when_u_tag_already_stored(self):
        item = {
            "body": "",
            "solution": (
                "4 × (A로 고정했을 때의 경우의 수) ... ①\n\n"
                "라 생각할 수 <u>있으므로</u>, 가장 위에 있는 영역은 $A$로 고정시키자."
            ),
        }
        issue = {
            "location": "solution",
            "message": "원본 이미지에서 '있으므로' 부분에 밑줄이 그어져 있으나 전사 텍스트에서 누락되었습니다.",
            "original": "가장 위에 있는 영역은 A 로 고정시키자.",
            "extracted": "가장 위에 있는 영역은 A 로 고정시키자.",
            "suggestion": "<u>있으므로</u>, 가장 위에 있는 영역은 $A$ 로 고정시키자.",
        }
        self.assertTrue(contradicted_by_stored_underline(issue, item))
        self.assertTrue(contradicted_by_exact_text(issue))

    def test_real_missing_underline_remains_when_tag_absent(self):
        item = {"body": "", "solution": "라 생각할 수 있으므로, 가장 위에 있는 영역은 A로 고정시키자."}
        issue = {
            "location": "solution",
            "message": "'있으므로'에 밑줄이 있으나 전사에서 누락되었습니다.",
            "original": "있으므로, 가장 위",
            "extracted": "있으므로, 가장 위",
            "suggestion": "<u>있으므로</u>, 가장 위",
        }
        self.assertFalse(contradicted_by_stored_underline(issue, item))

    def test_observed_material_description_is_not_treated_as_extra_source_text(self):
        body = "<!-- MATERIAL:fig1 START -->\n![[assets/q/rev/fig1.png]]\n\n**[자료 1]**\n- 그림에는 바다와 그물이 보인다."
        assets = [{"id": "fig1", "path": "assets/q/rev/fig1.png"}]
        issue = {"message": "원본 이미지에는 텍스트 설명이 없는데 상세한 설명이 추가되었습니다."}
        self.assertTrue(contradicted_by_preserved_material(issue, body, assets))

    def test_material_structure_checks_numbers_symbols_and_units(self):
        structured = {"symbols": ["◇◇"], "units": ["%"], "tables": [{"headers": ["구분"], "rows": [["1,099"]]}]}
        self.assertEqual(check_material("◇◇ 1,099 %", structured), [])
        self.assertEqual(check_material("자료 표", structured), ["[기호] ◇◇", "[단위] %", "[표 값] 1,099"])

    def test_second_kice_round_reuses_boundaries_and_styles(self):
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.ingestion.formats.kice_korean import parse_pdf
        path = KS_ROOT / "1 국어/assets/화작 기출/추출/2506.pdf"
        if not path.exists():
            self.skipTest("2506 source PDF is outside this checkout")
        items, warnings = parse_pdf(path, "2506", "화법과 작문", "", Path("/tmp/exam-format-regression-2506"), "problem")
        self.assertEqual(warnings, [])
        self.assertEqual(len([i for i in items if i["kind"] == "question"]), 11)
        styles = [s for item in items for s in item.get("source_styles", [])]
        self.assertGreaterEqual(len([s for s in styles if s["type"] == "underline"]), 10)
        self.assertGreaterEqual(len([s for s in styles if s["type"] == "box"]), 1)
        self.assertGreaterEqual(len([s for s in styles if s["type"] == "group"]), 1)

    def test_audit_issue_categories(self):
        self.assertEqual(classify_issue({"message": "밑줄 범위가 다릅니다."}), "밑줄")
        self.assertEqual(classify_issue({"message": "보기 박스가 누락되었습니다."}), "박스")
        self.assertEqual(classify_issue({"message": "자료의 단위가 다릅니다."}), "자료")
        self.assertEqual(classify_issue({"location": "선지 ② 표지 문자"}), "표지 문자")
        self.assertEqual(classify_issue({"location": "본문 및 보기", "message": "ⓐ가 ㉠으로 바뀜"}), "표지 문자")
        self.assertEqual(classify_issue({"message": "문장이 누락되었습니다."}), "텍스트")
    def test_inline_choices_match_separate_lines(self):
        self.assertEqual(symbol_differences("① ㉠ ② ㉡ ③ ㉢ ④ ㉣ ⑤ ㉤", "① ㉠\n\n② ㉡\n\n③ ㉢\n\n④ ㉣\n\n⑤ ㉤"), [])

    def test_inline_choices_still_detect_wrong_symbol(self):
        issues = symbol_differences("① ㉠ ② ㉣ ③ ㉡", "① ㉠\n② ㉡\n③ ㉡")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["location"], "선지 ② 표지 문자")

    def test_circle_entries_are_not_merged(self):
        result = normalize_markdown("> [!quote] 보기\n> ○ 첫 번째 조건\n> ○ 두 번째 조건")
        self.assertIn("> ○ 첫 번째 조건\n> ○ 두 번째 조건", result)

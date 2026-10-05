import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from _scripts_2.apps.exam.exam_processor.ai import process, EXTRACT_PROMPT, AUDIT_PROMPT
from _scripts_2.apps.exam.exam_processor.ai_worker import validate
from _scripts_2.apps.exam.exam_processor.models import (
    format_output_markdown, new_item, structural_errors, markdown_note,
)
from _scripts_2.apps.exam.exam_processor.rich_content import (
    image_description_callout, image_description_from_markdown, markup_errors,
    without_image_description_callout,
)
from _scripts_2.apps.exam.exam_processor.store import Store


class RichContentTests(unittest.TestCase):
    def response(self):
        return {"body": "발문\n\n{{diagram:fig1}}\n\n① <u>원문</u>\n\n② 다른 선지",
                "diagrams": [{"id": "fig1", "region": 0, "box": [0, 0, 1000, 1000],
                              "description": "왼쪽에는 표가 있고 오른쪽에는 범례가 있다.",
                              "markdown": "**[자료 1] 조사**\n\n| 항목 | 명 |\n| --- | --- |\n| 있음 | 257 |\n\n> [!quote] 안내\n> <u>원문의 강조</u>"}]}

    def test_transcription_required_and_validated(self):
        response = self.response()
        self.assertEqual(validate(response, "extract", 1), response)
        for value in (None, "", "![[assets/x.png]]", "<u>미완성", "&lt;u&gt;태그&lt;/u&gt;"):
            candidate = copy.deepcopy(response)
            candidate["diagrams"][0]["markdown"] = value
            with self.assertRaises(ValueError):
                validate(candidate, "extract", 1)

    def test_underline_pair_validation(self):
        self.assertEqual(markup_errors("<u>실제 밑줄</u>"), [])
        for text in ("</u><u>", "<u>누락", "<u/>", "&lt;u&gt;본문&lt;/u&gt;"):
            self.assertTrue(markup_errors(text))

    def test_image_transcription_persists_and_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/"data", Path(temp)/"out")
            job_id = "a" * 32
            record = new_item("source", "set", "01", "기존 수정본")
            record["regions"] = [{"image": "region.png", "bbox": [0, 0, 20, 20], "width": 20, "height": 20}]
            directory = store.job_dir(job_id)
            (directory/"regions").mkdir(parents=True)
            Image.new("RGB", (40,40), "white").save(directory/"regions/region.png")
            job = {"id": job_id, "source": "참조", "items": [record]}
            store.save(job)
            result = process(store, job_id, record["id"], record["revision"], "extract", caller=lambda _: {
                "data": validate(self.response(), "extract", 1), "provider": "fixture", "configured_model": "fixture"})
            item = result["items"][0]
            self.assertEqual(structural_errors(item), [])
            self.assertEqual(item["history"][0]["body"], "기존 수정본")
            output = markdown_note(result, item)
            self.assertLess(output.index("![[assets/"), output.index("| 있음 | 257 |"))
            self.assertIn("<u>원문의 강조</u>", output)
            self.assertIn("> [!quote] 안내", output)
            self.assertIn("<!-- SECTION:PROBLEM_START -->\n\n", output)
            self.assertIn("\n\n<!-- SECTION:PROBLEM_END -->", output)
            self.assertIn("<!-- MATERIAL:fig1 START -->\n\n![[assets/", output)
            self.assertIn("> [!info]- 이미지 설명\n> 왼쪽에는 표가 있고 오른쪽에는 범례가 있다.", output)
            self.assertIn("\n\n<!-- MATERIAL:fig1 END -->", output)
            self.assertEqual(item["assets"][0]["description"], "왼쪽에는 표가 있고 오른쪽에는 범례가 있다.")
            self.assertTrue((directory/item["assets"][0]["path"]).is_file())
            damaged = copy.deepcopy(item)
            start = damaged["body"].index("**[자료")
            end = damaged["body"].index("<!-- MATERIAL:fig1 END")
            damaged["body"] = damaged["body"][:start] + damaged["body"][end:]
            self.assertTrue(structural_errors(damaged))

    def test_description_is_not_an_unresolved_image(self):
        item = new_item("s", "c", "1", "")
        item["assets"] = [{"id": "fig1", "path": "assets/fig1.png", "section": "body"}]
        item["body"] = "<!-- MATERIAL:fig1 START -->\n![[assets/fig1.png]]\n\n(이미지: 자막이 있는 뉴스 화면)\n<!-- MATERIAL:fig1 END -->"
        self.assertEqual(structural_errors(item), [])
        item["body"] += "\n(이미지: 연결되지 않은 두 번째 자료)"
        self.assertTrue(structural_errors(item))

    def test_image_description_callout_preserves_paragraphs_and_lists(self):
        rendered = image_description_callout("왼쪽에 세 경우가 있다.\n\n- 첫째\n- 둘째")
        self.assertEqual(
            rendered,
            "> [!info]- 이미지 설명\n> 왼쪽에 세 경우가 있다.\n>\n> - 첫째\n> - 둘째",
        )
        self.assertEqual(without_image_description_callout(rendered + "\n\n원문 전사"), "원문 전사")
        self.assertEqual(
            image_description_from_markdown(
                "![[assets/fig.png]]\n\n" + rendered + "\n\n원문 전사",
                "assets/fig.png",
            ),
            "왼쪽에 세 경우가 있다.\n\n- 첫째\n- 둘째",
        )

    def test_choice_lines_are_compact_only_between_choices(self):
        source = "발문\n\n① 첫째\n\n② 둘째\n\n③ 셋째\n\n해설 문단"
        output = format_output_markdown(source)
        self.assertIn("발문\n\n① 첫째", output)
        self.assertIn("① 첫째\n② 둘째\n③ 셋째", output)
        self.assertIn("③ 셋째\n\n해설 문단", output)

    def test_section_boundary_comments_keep_one_blank_line(self):
        source = (
            "<!-- SECTION:PROBLEM_START -->\n"
            "발문\n\n"
            "<!-- SECTION:PROBLEM_END -->\n\n"
            "---\n\n"
            "<!-- SECTION:SOLUTION_START -->\n\n"
            "해설\n"
            "<!-- SECTION:SOLUTION_END -->"
        )
        output = format_output_markdown(source)
        self.assertIn("<!-- SECTION:PROBLEM_START -->\n\n발문", output)
        self.assertIn("해설\n\n<!-- SECTION:SOLUTION_END -->", output)

    def test_group_boundary_comments_are_isolated_like_structural_markers(self):
        source = "**(가)** <!-- GROUP:A START -->\n본문\n문장 끝 <!-- GROUP:A END -->"
        output = format_output_markdown(source)
        self.assertIn("**(가)**\n\n<!-- GROUP:A START -->\n\n> [A] 본문", output)
        self.assertIn("> 문장 끝\n\n<!-- GROUP:A END -->", output)

    def test_group_boundary_comments_render_visible_source_label_once(self):
        source = "<!-- GROUP:B START -->\n청소년이 신청서를 제출한다.\n\n지역 단체는 홍보한다.\n<!-- GROUP:B END -->"
        output = format_output_markdown(source)
        self.assertIn("<!-- GROUP:B START -->\n\n> [B] 청소년이 신청서를 제출한다.\n>\n> 지역 단체는 홍보한다.\n\n<!-- GROUP:B END -->", output)
        self.assertEqual(format_output_markdown(output).count("> [B]"), 1)

    def test_export_markdown_repairs_stored_pdf_spacing_artifacts(self):
        source = "수 월해진 대출·반납에서 반영되 었다. 반영 되었다. 반 영되었다. (나) 의 ‘행사’ 에서 명확하 게 제시되 어 있 다. ㉰를 기준으 로 적절하 다."
        output = format_output_markdown(source)
        for fragment in ("수 월해진", "반영되 었", "반영 되", "반 영", ") 의", "’ 에서", "하 게", "제시되 어", "있 다", "기준으 로", "적절하 다"):
            self.assertNotIn(fragment, output)
        self.assertIn("수월해진", output)
        self.assertIn("반영되었다", output)
        self.assertIn("(나)의", output)
        self.assertIn("‘행사’에서", output)
        self.assertIn("명확하게", output)
        self.assertIn("제시되어 있다", output)
        self.assertIn("기준으로", output)
        self.assertIn("적절하다", output)

    def test_prompts_require_visual_fidelity(self):
        for term in ("자막", "범례", "발화자", "<u>", "박스"):
            self.assertIn(term, EXTRACT_PROMPT)
            self.assertIn(term, AUDIT_PROMPT)

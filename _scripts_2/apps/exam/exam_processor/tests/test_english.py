"""English contract, grouping, and self-contained Markdown regeneration."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, canonical_relation_errors
from _scripts_2.apps.exam.exam_core.domain.canonical.schema import (
    OriginDomain,
    QuestionDomain,
    RelationsDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.models import new_item
from _scripts_2.apps.exam.exam_processor.domain.rich_content import material_errors
from _scripts_2.apps.exam.exam_processor.exporters.english_export import output_stem, write_english_batch
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown
from _scripts_2.apps.exam.exam_processor.exporters.rebuild import rebuild_markdown_batch
from _scripts_2.apps.exam.exam_processor.ingestion.formats.english.kice_english import (
    parse_problem_pdf, strip_listening_end, visual_material,
)
from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import format_display_title
from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical


class _Column:
    def __init__(self, side):
        self.side = side


class _Page:
    width = 842
    height = 1191
    lines = [
        {"y0": 1080, "y1": 1080, "top": 111, "width": 750, "height": 0,
         "bottom": 111, "x0": 45, "x1": 795},
        {"y0": 100, "y1": 1070, "top": 121, "width": 0, "height": 970,
         "bottom": 1090, "x0": 421, "x1": 421},
    ]

    def crop(self, box):
        return _Column("left" if box[0] < 421 else "right")


class _Pdf:
    pages = [_Page()]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_lines(column):
    if column.side == "left":
        return [(150, "1. 첫 문제"), (170, "① 선지"),
                (200, "[41～42] 다음 글을 읽고 답하시오."), (220, "공유 지문 A"),
                (250, "41. 제목"), (270, "① A"), (300, "42. 어휘"),
                (340, "[43～45] 다음 글을 읽고 답하시오."), (360, "(A) 공유 이야기")]
    return [(150, "(B) 이야기 계속"), (200, "43. 순서"), (230, "44. 지칭"),
            (260, "45. 내용")]


class EnglishTests(unittest.TestCase):
    def test_english_title_form_and_unverified_private_exam(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(question_id="q1", display_code="16", subject="영어", question_number="16"),
            source=SourceDomain(source_id="s1", type="exam", title="사설 모의고사", exam_form="홀수형"),
        )
        private_note = project_to_markdown(record)
        self.assertIn('content_type: "original"', private_note)
        self.assertIn("## 사설 모의고사 16번", private_note)
        record.origin = OriginDomain(type="kice", academic_year="2026", session="11",
                                     authority="KICE", track="영어", question_number="16")
        note = project_to_markdown(record)
        self.assertIn("## 2026학년도 수능 영어 홀수형 16번", note)
        self.assertNotIn("book_code:", private_note)
        self.assertNotIn("\nsection:", private_note)
        self.assertNotIn("book_code:", note)
        self.assertNotIn("\nsection:", note)

    def test_missing_script_relation_and_script_change_invalidate_question(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(question_id="q1", display_code="16", subject="영어"),
            source=SourceDomain(source_id="s1", type="exam", title="평가원"),
            relations=RelationsDomain(listening_script_ids=["missing"]),
        )
        self.assertTrue(any("script missing" in error for error in canonical_relation_errors([record])))
        script = new_item("s1", "script", "16-17", "대본", kind="script")
        question = new_item("s1", "q16", "16", "문제")
        question["listening_script_id"] = script["id"]
        question["review"] = "approved"
        before = question["revision"]
        Store.__new__(Store).invalidate_dependents({"items": [script, question]}, script)
        self.assertEqual(question["review"], "pending")
        self.assertNotEqual(question["revision"], before)

    def test_two_column_shared_passages_keep_continuation(self):
        with patch("_scripts_2.apps.exam.exam_processor.ingestion.formats.english.kice_english.pdfplumber.open",
                   return_value=_Pdf()), patch(
                       "_scripts_2.apps.exam.exam_processor.ingestion.formats.english.kice_english.lines",
                       side_effect=_fake_lines):
            records, review_states, warnings = parse_problem_pdf("unused.pdf", "exam", pages="1")
        questions = {r.question.question_number: r for r in records if r.question.content_kind == "question"}
        passages = {r.question.question_number: r for r in records if r.question.content_kind == "passage"}
        self.assertEqual(set(questions), {"1", "41", "42", "43", "44", "45"})
        self.assertEqual(len(passages), 2)
        self.assertEqual(questions["41"].relations.passage_ids[0], questions["42"].relations.passage_ids[0])
        self.assertEqual(questions["43"].relations.passage_ids[0], questions["45"].relations.passage_ids[0])
        self.assertIn("(B) 이야기 계속", passages["43-45"].body)
        self.assertNotIn("(B) 이야기 계속", passages["41-42"].body)
        self.assertTrue(any("누락/중복" in warning for warning in warnings))

    def test_q17_drops_listening_end_announcement(self):
        leaked = (
            "17. 언급된 장소가 아닌 것은?\n\n"
            "① sports stadiums ② schools ③ concert halls\n"
            "④ military bases ⑤ farms 이제 듣기 문제가 끝났습니다. "
            "18번부터는 문제지의 지시에 따라 답을 하시기 바랍니다."
        )
        cleaned = strip_listening_end(leaked)
        self.assertIn("⑤ farms", cleaned)
        self.assertNotIn("이제 듣기 문제가 끝났습니다", cleaned)
        self.assertNotIn("18번부터", cleaned)
        speaking = strip_listening_end("17. 문항\n\n이제 듣기·말하기 문제가 끝났습니다. 다음으로 가십시오.")
        self.assertEqual(speaking, "17. 문항")

        def fake_lines(column):
            if column.side == "left":
                return [(150, "16. 주제"), (170, "① A"),
                        (200, "17. 언급된 장소가 아닌 것은?"),
                        (220, "① A ② B ③ C ④ D ⑤ E 이제 듣기 문제가 끝났습니다.")]
            return [(150, "18. 목적"), (170, "① A")]

        with patch("_scripts_2.apps.exam.exam_processor.ingestion.formats.english.kice_english.pdfplumber.open",
                   return_value=_Pdf()), patch(
                       "_scripts_2.apps.exam.exam_processor.ingestion.formats.english.kice_english.lines",
                       side_effect=fake_lines):
            records, _, _ = parse_problem_pdf("unused.pdf", "exam", pages="1")
        questions = {r.question.question_number: r.body for r in records if r.question.content_kind == "question"}
        self.assertIn("⑤ E", questions["17"])
        self.assertNotIn("이제 듣기 문제가 끝났습니다", questions["17"])
        self.assertNotIn("이제 듣기 문제가 끝났습니다", questions["18"])

    def test_kice_english_job_attaches_display_title(self):
        source_id = "kice-2027-09-eng"
        item = new_item(source_id, "q16", "16", "16. 문제")
        item["kind"] = "question"
        item["content_type"] = "past_exam"
        item["origin"] = {"type": "kice", "academic_year": "2027", "session": "09",
                           "authority": "KICE", "track": "영어", "question_number": "16",
                           "question_id": item["id"]}
        job = {"format": "kice_english", "source": "2027학년도 9월 모의평가 영어",
               "subject": "영어", "source_id": source_id, "source_type": "exam",
               "source_set_id": source_id, "exam_code": "2709", "items": [item]}
        Store._attach_display_titles(job)
        self.assertEqual(item["display_title"], "2027학년도 9월 평가원 영어 16번")
        self.assertEqual(format_display_title(item_to_canonical(job, item)), item["display_title"])

    def test_kice_korean_job_attaches_display_title(self):
        source_id = "kice-2020-06"
        item = new_item(source_id, "q28", "28", "28. 문제")
        item["kind"] = "question"
        item["content_type"] = "past_exam"
        item["origin"] = {"type": "kice", "academic_year": "2020", "session": "06",
                           "authority": "KICE", "track": "나형", "question_number": "28",
                           "question_id": item["id"]}
        job = {"format": "kice_korean", "source": "2020학년도 6월 모의평가", "subject": "국어",
               "source_id": source_id, "source_type": "exam", "exam_code": "2006", "items": [item]}
        Store._attach_display_titles(job)
        self.assertEqual(item["display_title"], "2020학년도 6월 평가원 나형 28번")

    def test_display_title_not_written_to_job_json(self):
        source_id = "kice-2027-09-eng"
        item = new_item(source_id, "q1", "1", "1. 문제")
        item["kind"] = "question"
        job = {"id": "a" * 32, "format": "kice_english", "source": "test",
               "subject": "영어", "source_id": source_id, "items": [item]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "data", root / "exports")
            store.job_dir(job["id"]).mkdir(parents=True)
            Store._attach_display_titles(job)
            store.save(job)
            saved = json.loads((store.job_dir(job["id"]) / "job.json").read_text(encoding="utf-8"))
            self.assertNotIn("items", saved)
            self.assertNotIn("display_title", saved)
            self.assertIn("display_title", job["items"][0])

    def test_visual_asset_id_matches_material_marker(self):
        asset, markup = visual_material(4, "regions/x.png")
        self.assertEqual(asset.asset_id, "fig1")
        self.assertIn("<!-- MATERIAL:fig1 START -->", markup)
        self.assertIn("![[regions/x.png]]", markup)
        empty = "4. 그림\n" + markup
        asset_dict = {"id": asset.asset_id, "path": asset.path, "section": asset.section}
        self.assertTrue(material_errors(empty, [asset_dict]))
        transcribed = (
            "4. 그림\n\n<!-- MATERIAL:fig1 START -->\n![[regions/x.png]]\n\n"
            "① 왼쪽 나무\n<!-- MATERIAL:fig1 END -->"
        )
        self.assertEqual(material_errors(transcribed, [asset_dict]), [])

    def test_script_links_stay_in_solution_and_rebuild_from_json(self):
        source_id = "kice-2027-09-eng"
        script = new_item(source_id, "script-16-17", "16-17", "## 16~17번\n\nM: test", kind="script")
        early_script = new_item(source_id, "script-4", "4", "## 4번\n\nM: picture", kind="script")
        passage = new_item(source_id, "41-42", "41-42", "공유 독해 지문", kind="passage")
        items = [early_script, script, passage]
        self.assertEqual(output_stem("4"), "04")
        self.assertEqual(output_stem("16-17"), "16-17")
        self.assertEqual(output_stem("41-42"), "41-42")
        for number in (16, 17, 41, 42):
            context = "41-42" if number > 17 else f"q{number}"
            item = new_item(source_id, context, str(number), f"{number}. 문제 본문")
            item["modality"] = "listening" if number <= 17 else "reading"
            item["content_type"] = "past_exam"
            item["origin"] = {"type": "kice", "academic_year": "2027", "session": "09",
                              "authority": "KICE", "track": "영어", "question_number": str(number),
                              "question_id": item["id"]}
            if number <= 17:
                item["listening_script_id"] = script["id"]
            else:
                item["passage_id"] = passage["id"]
            items.append(item)
        picture = new_item(source_id, "q04", "4", "4. 그림 문항")
        picture["modality"] = "listening"
        picture["content_type"] = "past_exam"
        picture["origin"] = {"type": "kice", "academic_year": "2027", "session": "09",
                             "authority": "KICE", "track": "영어", "question_number": "4",
                             "question_id": picture["id"]}
        picture["listening_script_id"] = early_script["id"]
        items.append(picture)
        for item in items:
            item["review"] = "approved"
        job = {"format": "kice_english", "source": "2027학년도 9월 모의평가 영어",
               "subject": "영어", "source_id": source_id, "source_type": "exam",
               "source_set_id": source_id, "exam_code": "2709", "documents": {}, "items": items}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage, rebuilt = root / "stage", root / "rebuilt"
            stage.mkdir()
            write_english_batch(stage, job, root)
            note = (stage / "questions/16.md").read_text(encoding="utf-8")
            problem = note.split("<!-- SECTION:PROBLEM_START -->", 1)[1].split(
                "<!-- SECTION:PROBLEM_END -->", 1)[0]
            solution = note.split("<!-- SECTION:SOLUTION_START -->", 1)[1]
            self.assertIn("## 2027학년도 9월 평가원 영어 16번", note)
            self.assertIn('source_set_id: "kice-2027-09-eng"', note)
            self.assertNotIn("듣기 대본", problem)
            self.assertIn("### 듣기 대본\n\n![[../scripts/16-17.md]]", solution)
            self.assertFalse((stage / "scripts/4.md").exists())
            self.assertTrue((stage / "scripts/04.md").is_file())
            self.assertIn("![[../scripts/04.md]]", (stage / "questions/04.md").read_text())
            self.assertNotIn("book_code:", note)
            self.assertNotIn("\nsection:", note)
            self.assertIn("![[../passages/41-42.md]]", (stage / "questions/41.md").read_text())
            canonical = json.loads((stage / "questions/16.json").read_text())
            restored = CanonicalQuestionRecord.from_dict(canonical)
            self.assertEqual(restored.relations.listening_script_ids, [script["id"]])
            rebuild_markdown_batch(stage, rebuilt)
            self.assertEqual(note, (rebuilt / "questions/16.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

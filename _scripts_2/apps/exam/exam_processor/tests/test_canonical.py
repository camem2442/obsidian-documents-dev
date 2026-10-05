import json
import copy
import unittest

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    OriginDomain,
    QuestionDomain,
    SourceDomain,
    UnitDomain,
    validate_canonical_dict,
)
from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown


class TestCanonicalModel(unittest.TestCase):
    def test_moved_store_and_pipeline_execute_edit_and_audit(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import process
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = job["items"][0]
            item["source_styles"] = [{"type": "underline", "text": "원본", "page": 1, "bbox": [0, 0, 10, 10]}]
            store.save(job)
            store.update(job["id"], item["id"], item["revision"], {"body": item["body"] + "\n수정"})
            item = store.get(job["id"])["items"][0]
            process(store, job["id"], item["id"], item["revision"], "audit", caller=lambda _: {
                "data": {"result": "no_difference", "issues": []}, "provider": "fixture",
                "configured_model": "fixture"})
            self.assertEqual(store.get(job["id"])["items"][0]["audit"]["status"], "completed")

    def test_runtime_mapping_preserves_identifiers_and_pending_states(self):
        job = {"id": "job-1", "source_id": "source-1", "source": "2026학년도 6월 모의평가"}
        item = {"id": "q1", "number": "19", "revision": "new", "body": "[전사 대기]",
                "transcription_pending": ["body"], "audit": {"revision": "old", "status": "completed"},
                "classification_status": "unclassified", "classification_method": "unclassified",
                "regions": [{"document": "problem", "page": 12}],
                "solution_regions": [{"document": "answer", "page": 2}],
                "selected_solution_id": "answer-candidate-1", "section_title": "not a textbook"}
        record = item_to_canonical(job, item)
        self.assertEqual(record.question.display_code, "260619")
        self.assertEqual(record.source.source_id, "source-1")
        self.assertEqual(record.source.locator.document_id, "problem")
        self.assertEqual(record.source.locator.pdf_pages, [12])
        self.assertIsNone(record.source.section_title)
        self.assertEqual(record.review.transcription, "pending")
        self.assertEqual(record.review.source_audit, "pending")
        self.assertEqual(record.review.classification, "not_started")
        self.assertIsNone(record.classification.method)
        self.assertEqual(record.relations.solution_ids, [])
        self.assertEqual(record.provenance.source_record_ids, ["answer-candidate-1"])
        self.assertEqual([r["content_role"] for r in record.provenance.source_regions], ["body", "solution"])
        CanonicalQuestionRecord.from_dict(json.loads(record.to_json()))
        item.update(body="본문", transcription_pending=[])
        item["audit"]["revision"] = "new"
        record = item_to_canonical(job, item)
        self.assertEqual(record.review.transcription, "completed")
        self.assertEqual(record.review.source_audit, "no_difference")

    def test_workbook_mapping_does_not_infer_type_from_brand(self):
        job = {"source": "새 문제집", "workbook": {"id": "wb"}, "subject": "수학"}
        item = {"id": "q1", "number": "001", "body": "본문", "unit_code": "ps.counting",
                "curriculum_id": "taxonomy-1", "unit_path": ["확률과 통계", "순열과 조합"],
                "classification_method": "workbook_toc_confirmed"}
        record = item_to_canonical(job, item)
        self.assertEqual(record.source.type, "workbook")
        self.assertEqual(record.question.question_number, "001")
        self.assertEqual(record.question.display_code, "001")
        self.assertEqual(record.unit.code, "ps.counting")
        self.assertEqual(record.unit.taxonomy_id, "taxonomy-1")
        self.assertEqual(record.unit.path, item["unit_path"])
        self.assertEqual(record.classification.method, "workbook_toc")

    def test_direct_exam_uses_exam_origin_without_workbook_fields(self):
        job = {
            "source_type": "exam",
            "source": "2026 LEET 언어이해",
            "origin": {
                "type": "leet", "academic_year": "2026", "session": "08",
                "authority": "법학전문대학원협의회", "track": "언어이해",
                "question_number": "12",
            },
        }
        record = item_to_canonical(job, {"id": "q1", "number": "12"})
        self.assertEqual(record.source.type, "exam")
        self.assertEqual(record.source.title, "2026 LEET 언어이해")
        self.assertIsNone(record.source.edition)
        self.assertIsNone(record.source.section_code)
        self.assertIsNone(record.source.item_code)
        self.assertEqual(record.origin.type, "leet")
        self.assertEqual(record.origin.question_number, "12")
        CanonicalQuestionRecord.from_dict(json.loads(record.to_json()))

    def test_validation_rejects_bad_types_values_and_unknown_fields(self):
        valid = item_to_canonical({"source": "시험"}, {"id": "q1", "number": "001"}).to_dict()
        mutations = [("question", []), ("origin", []), ("extra", True)]
        for key, value in mutations:
            data = copy.deepcopy(valid)
            data[key] = value
            with self.subTest(key=key):
                self.assertTrue(validate_canonical_dict(data))
                with self.assertRaises(ValueError):
                    CanonicalQuestionRecord.from_dict(data)
        missing_origin_type = copy.deepcopy(valid)
        missing_origin_type["origin"] = {"academic_year": "2026"}
        self.assertIn("origin.type is required when origin is present.",
                      validate_canonical_dict(missing_origin_type))
        for key, value in [("question_number", 1), ("points", -1), ("points", float("nan")),
                           ("correct_rate", 1.01), ("correct_rate", -0.01),
                           ("points", True), ("status", "unknown")]:
            data = copy.deepcopy(valid)
            data["question"][key] = value
            with self.subTest(key=key, value=value):
                self.assertTrue(validate_canonical_dict(data))
        for field, value in [("path", "../outside.png"), ("source_path", "/private/file.png")]:
            data = copy.deepcopy(valid)
            data["assets"] = [{"asset_id": "a1", "section": "body", "source_path": "a.png",
                                "path": "a.png", "media_type": "image/png", "sha256": None}]
            data["assets"][0][field] = value
            with self.subTest(asset_field=field, value=value):
                self.assertTrue(validate_canonical_dict(data))
        record = CanonicalQuestionRecord.from_dict(valid)
        record.question.points = float("inf")
        with self.assertRaises(ValueError):
            record.to_json()

    def test_canonical_serialization_roundtrip(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id="q-260619",
                display_code="260619",
                subject="수학",
                question_number="19",
                points=3,
                correct_rate=0.34,
                answer="④",
            ),
            source=SourceDomain(
                source_id="wb-hanwangi-2026",
                type="workbook",
                title="2026 한완기 확률과통계",
                section_code="D1",
                section_title="Pattern 01",
                item_code="D1-14",
            ),
            origin=OriginDomain(
                type="kice",
                academic_year="2026",
                session="06",
                track="확률과 통계",
                question_number="19",
            ),
            unit=UnitDomain(
                display_name="순열과 조합",
                path=["확률과 통계", "경우의 수", "순열과 조합"],
                code="ps.counting",
            ),
            body="다음 그림과 같이...",
            solution="정답 해설...",
        )

        d = record.to_dict()
        errors = validate_canonical_dict(d)
        self.assertEqual(errors, [])

        restored = CanonicalQuestionRecord.from_dict(d)
        self.assertEqual(restored.question.question_id, "q-260619")
        self.assertEqual(restored.source.item_code, "D1-14")
        self.assertEqual(restored.origin.track, "확률과 통계")
        self.assertEqual(restored.question.correct_rate, 0.34)
        self.assertEqual(restored.unit.path, ["확률과 통계", "경우의 수", "순열과 조합"])

    def test_markdown_projection(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(
                question_id="q-260635",
                display_code="260635",
                subject="국어",
                question_number="35",
                points=2,
                answer="④",
            ),
            source=SourceDomain(
                source_id="exam-2606",
                type="exam",
                title="2026학년도 6월 모의평가",
            ),
            unit=UnitDomain(display_name="화법과 작문"),
            body="35. 위 발표자의 말하기 방식으로...",
            solution="발표 내용 중에...",
        )

        md = project_to_markdown(record, passage_file="2606_1.md")
        self.assertIn('subject: "국어"', md)
        self.assertIn('source: "2026학년도 6월 모의평가"', md)
        self.assertIn('q_number: "35"', md)
        self.assertIn("<!-- SECTION:PROBLEM_START -->", md)
        self.assertIn("**정답: ④**", md)

    def test_original_and_variant_titles_share_the_same_sections(self):
        record = CanonicalQuestionRecord(
            question=QuestionDomain(question_id="drill-03", display_code="03", subject="수학",
                                    question_number="03", content_type="original"),
            source=SourceDomain(source_id="drill", type="workbook", title="드릴 미적분"),
            body="창작 문제", solution="창작 해설",
        )
        original = project_to_markdown(record)
        self.assertIn("## 드릴 미적분 03번", original)
        self.assertNotIn("\norigin:", original)
        record.question.content_type = "variant"
        record.question.variant_of = ["kice-2022-csat-22"]
        record.origin = OriginDomain(type="kice", academic_year="2022", session="11",
                                     authority="KICE", question_number="22")
        variant = project_to_markdown(record)
        self.assertIn("## 드릴 미적분 03번 (변형: 2022학년도 수능 22번)", variant)
        self.assertIn('variant_of: ["kice-2022-csat-22"]', variant)
        for note in (original, variant):
            for marker in ("PROBLEM_START", "PROBLEM_END", "SOLUTION_START", "SOLUTION_END"):
                self.assertEqual(note.count(f"SECTION:{marker}"), 1)

    def test_workbook_solution_header_moves_to_metadata_without_changing_question(self):
        from _scripts_2.apps.exam.exam_processor.exporters.profiles.workbook_markdown import (
            prepare_workbook_record,
        )

        body = "원문 발문\n\n> 조건\n> $a < b$"
        solution = (
            "## D1-13 | 2024·확통 29번 |\n\n"
            "정답률 34% | Pattern 01\n\n"
            "### 교과서적 해법 1\n\n풀이 본문"
        )
        job = {"source": "2026 한완기 확률과통계", "format": "hanwangi_2026_probability", "workbook": {"nodes": [
            {"id": "D1", "section_code": "1-1", "section_title": "순열과 조합"}
        ]}}
        item = {"id": "q1", "number": "D1·13", "context": "D1", "body": body, "solution": solution}
        record = item_to_canonical(job, item)
        prepare_workbook_record(record, job, item)
        md = project_to_markdown(record)

        self.assertIn("## 2024학년도 9월 평가원 확률과 통계 29번", md)
        self.assertIn("<!-- SECTION:PROBLEM_START -->\n\n" + body, md)
        self.assertIn('origin: "2024학년도 9월 평가원 확률과 통계 29번"', md)
        self.assertIn("### 교과서적 해법 1", md)
        self.assertNotIn("2024·확통 29번", md)
        self.assertNotIn("정답률 34%", md)
        self.assertIn('section: "Pattern 01"', md)
        self.assertNotIn("Pattern 01", md.split("<!-- SECTION:PROBLEM_START -->", 1)[1])
        self.assertEqual(record.question.display_code, "D1-13")
        self.assertEqual(record.question.correct_rate, 0.34)
        self.assertEqual(record.source.item_code, "D1-13")
        self.assertEqual(record.source.section_code, "1-1")
        self.assertEqual(record.source.section_title, "Pattern 01")
        self.assertEqual(record.unit.display_name, "순열과 조합")
        self.assertEqual(record.origin.type, "kice")
        self.assertEqual(record.origin.academic_year, "2024")
        self.assertEqual(record.origin.session, "09")
        self.assertEqual(record.origin.authority, "KICE")
        self.assertEqual(record.origin.track, "확률과 통계")
        self.assertEqual(record.origin.question_number, "29")

    def test_workbook_variants_and_single_method_heading_are_projected(self):
        from _scripts_2.apps.exam.exam_processor.exporters.profiles.workbook_markdown import (
            prepare_workbook_record,
        )
        job = {"source": "2026 한완기 확률과통계", "format": "hanwangi_2026_probability", "subject": "수학", "workbook": {"nodes": []}}
        item = {"id": "a102", "number": "A1·02", "kind": "question",
                "body": "A1-02\n\n$P$의 값은? [2점]", "solution": (
                    "A1·02 | 2017.6·가 3번, 나 22번 |\n\n"
                    '<span class="source-inline-box">교과서적 해법</span>\n\n$P=24$')}
        record = item_to_canonical(job, item)
        prepare_workbook_record(record, job, item)
        md = project_to_markdown(record)
        self.assertIn("## 2017학년도 6월 평가원 가형 3번 · 나형 22번", md)
        self.assertIn("<!-- SECTION:PROBLEM_START -->\n\n$P$의 값은? [2점]", md)
        self.assertIn("<!-- SECTION:SOLUTION_START -->\n\n$P=24$", md)
        self.assertEqual(item["solution"].splitlines()[0], "A1·02 | 2017.6·가 3번, 나 22번 |")
        self.assertEqual(record.origin.type, "kice")
        self.assertEqual(record.origin.authority, "KICE")
        self.assertEqual(record.origin.session, "06")
        self.assertEqual(record.origin.variants, [
            {"track": "가", "question_number": "3"}, {"track": "나", "question_number": "22"},
        ])
        self.assertEqual(record.origin.raw_label, "2017.6·가 3번, 나 22번")
        self.assertEqual(record.origin.question_number, "")
        self.assertEqual(record.source.item_code, "A1-02")
        self.assertFalse(validate_canonical_dict(record.to_dict()))
        self.assertEqual(CanonicalQuestionRecord.from_dict(json.loads(record.to_json())).origin.variants,
                         record.origin.variants)

    def test_hanwangi_origin_without_month_keeps_session_unknown(self):
        from _scripts_2.apps.exam.exam_processor.exporters.profiles.workbook_markdown import prepare_workbook_record
        job = {"source": "2026 한완기 확률과통계", "format": "hanwangi_2026_probability",
               "subject": "수학", "workbook": {"nodes": []}}
        item = {"id": "q3", "number": "A1·03", "kind": "question", "body": "A1·03\n\n문제",
                "solution": "A1·03 | 2021·나 12번 |\n\n해설"}
        record = item_to_canonical(job, item)
        prepare_workbook_record(record, job, item)
        self.assertEqual(record.origin.type, "kice")
        self.assertEqual(record.origin.authority, "KICE")
        self.assertEqual(record.origin.session, "")
        self.assertIn("## 2021학년도 평가원 나형 12번", project_to_markdown(record))

    def test_multiple_or_distinct_method_headings_remain(self):
        from _scripts_2.apps.exam.exam_processor.domain.workbook_content import workbook_presentation
        for solution in (
            "교과서적 해법 1\n첫 풀이\n\n교과서적 해법 2\n둘째 풀이",
            "**교과서적 해법**\n첫 풀이\n\n**수능적 해법**\n둘째 풀이",
        ):
            self.assertEqual(workbook_presentation({"number": "A1·02", "solution": solution})["solution"], solution)

    def test_hanwangi_projection_does_not_change_another_workbook(self):
        from _scripts_2.apps.exam.exam_processor.exporters.profiles.workbook_markdown import prepare_workbook_record
        item = {"id": "q1", "number": "A1·02", "body": "A1-02\n\n문제", "solution": "교과서적 해법\n해설"}
        job = {"source": "다른 문제집", "subject": "수학", "format": "another_workbook", "workbook": {"nodes": []}}
        record = item_to_canonical(job, item)
        prepare_workbook_record(record, job, item)
        self.assertEqual(record.body, item["body"])
        self.assertEqual(record.solution, item["solution"])

    def test_conflicting_printed_metadata_is_not_silently_removed(self):
        from _scripts_2.apps.exam.exam_processor.domain.workbook_content import workbook_presentation
        item = {"number": "A1·02", "body": "A1-02 | 2017.6·가 3번 |\n문제",
                "solution": "A1·02 | 2017.6·가 4번 |\n해설"}
        view = workbook_presentation(item)
        self.assertEqual(view["body"], item["body"])
        self.assertEqual(view["solution"], item["solution"])
        self.assertTrue(view["warnings"])

    def test_html_header_and_repeated_problem_before_analysis(self):
        from _scripts_2.apps.exam.exam_processor.domain.workbook_content import workbook_presentation
        question = "집합 $X$와 함수 $f$에 대하여 다음 조건을 만족시키는 함수의 개수를 구하시오. [4점]"
        item = {"number": "D1·17", "body": (
            "D1·17 CHALLENGE 정답률 8% 해설 ANALYSIS Pattern 01 2023.9·확통 30번\n\n"
            + question + "\n\n> [!quote] 조건"),
            "solution": ("D1·17<br>CHALLENGE<br>정답률 8%<br>해설 ANALYSIS<br>"
                         "Pattern 01<br>| 2023.9·확통 30번|<br><br>" + question
                         + "<br><br>> [!quote] 조건<br><br>발문 분석<br>실제 풀이")}
        view = workbook_presentation(item)
        self.assertTrue(view["body"].startswith(question))
        self.assertEqual(view["solution"], "발문 분석<br>실제 풀이")
        self.assertEqual(view["metadata"]["correct_rate"], 0.08)
        self.assertEqual(view["metadata"]["print_labels"], ["CHALLENGE", "해설 ANALYSIS"])

    def test_asset_description_is_preserved_in_canonical_json(self):
        job = {"source": "교재", "subject": "수학"}
        item = {"id": "q1", "number": "1", "body": "문항", "assets": [{
            "id": "fig1", "section": "body", "path": "assets/q1/fig1.png",
            "description": "왼쪽에는 세 경우, 오른쪽에는 수형도가 있다.",
        }]}
        record = item_to_canonical(job, item)
        self.assertEqual(record.assets[0].description, "왼쪽에는 세 경우, 오른쪽에는 수형도가 있다.")
        restored = CanonicalQuestionRecord.from_dict(json.loads(record.to_json()))
        self.assertEqual(restored.assets[0].description, record.assets[0].description)

        item["assets"][0].pop("description")
        item["body"] = (
            "![[assets/q1/fig1.png]]\n\n"
            "> [!info]- 이미지 설명\n"
            "> 왼쪽에는 세 경우, 오른쪽에는 수형도가 있다.\n\n"
            "원문 전사"
        )
        legacy_record = item_to_canonical(job, item)
        self.assertEqual(legacy_record.assets[0].description, record.assets[0].description)

    def test_sidecar_export_pair(self):
        import tempfile
        from pathlib import Path
        try:
            from _scripts_2.apps.exam.exam_processor.domain.models import new_item
            from _scripts_2.apps.exam.exam_processor.exporters.set_export import write_sets
        except ImportError:
            from exam_processor.models import new_item
            from exam_processor.exporters.set_export import write_sets

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stage = root / "export"
            stage.mkdir()

            item = new_item("source-2606", "35-37", "35", "문제 본문 내용")
            item["review"] = "approved"
            item["points"] = 2
            item["answer"] = "④"
            job = {
                "id": "job-12345",
                "source": "2026학년도 6월 모의평가",
                "subject": "국어",
                "items": [item],
            }

            write_sets(stage, job, "2606", root)

            md_path = stage / "2606 1" / "260635.md"
            json_path = stage / "2606 1" / "260635.json"

            self.assertTrue(md_path.exists(), "Markdown file must be generated")
            self.assertTrue(json_path.exists(), "Micro-JSON sidecar must be generated")

            json_data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(json_data["schema_version"], "0.3.0")
            self.assertEqual(json_data["question"]["question_number"], "35")
            self.assertEqual(json_data["question"]["points"], 2)
            self.assertEqual(json_data["question"]["answer"], "④")
            self.assertEqual(json_data["source"]["title"], "2026학년도 6월 모의평가")

    def test_korean_set_export_through_v2_store(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.domain.models import new_item
        from _scripts_2.apps.exam.exam_processor.storage.store import Store

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = Store(root / "jobs", root / "exports")

            # Create passage and question for set 38-39
            passage = new_item("source-2606", "38-39", "38-39", "공통 지문 본문", kind="passage")
            passage["question_set_id"] = "38-39"
            passage["review"] = "approved"
            passage["audit"] = {"revision": passage["revision"], "status": "completed", "result": "no_difference", "issues": []}

            q38 = new_item("source-2606", "38-39", "38", "38번 문제 본문")
            q38["question_set_id"] = "38-39"
            q38["passage_id"] = passage["id"]
            q38["review"] = "approved"
            q38["audit"] = {"revision": q38["revision"], "status": "completed", "result": "no_difference", "issues": []}
            q38["points"] = 3
            q38["answer"] = "①"

            q39 = new_item("source-2606", "38-39", "39", "39번 문제 본문")
            q39["question_set_id"] = "38-39"
            q39["passage_id"] = passage["id"]
            q39["review"] = "approved"
            q39["audit"] = {"revision": q39["revision"], "status": "completed", "result": "no_difference", "issues": []}
            q39["points"] = 2
            q39["answer"] = "⑤"

            job = {
                "id": "e" * 32,
                "source": "2026학년도 6월 모의평가",
                "format": "kice_korean",
                "subject": "국어",
                "items": [passage, q38, q39],
            }

            store.save(job)

            # Check that job was saved as v2
            loaded_job = store.get(job["id"])
            self.assertEqual(loaded_job["storage_version"], 2)
            self.assertEqual(len(loaded_job["items"]), 3)
            self.assertEqual(loaded_job["items"][0]["context"], "38-39")
            self.assertEqual(loaded_job["items"][1]["context"], "38-39")
            self.assertEqual(loaded_job["items"][2]["context"], "38-39")

            # Export via store
            exported = store.export(job["id"], source_code="2606")
            batch_path = Path(exported["last_export"])

            # Verify exported files
            combined_md = batch_path / "2606 1" / "2606_1.md"
            q38_md = batch_path / "2606 1" / "260638.md"
            q38_json = batch_path / "2606 1" / "260638.json"
            q39_md = batch_path / "2606 1" / "260639.md"
            q39_json = batch_path / "2606 1" / "260639.json"

            self.assertTrue(combined_md.exists())
            self.assertTrue(q38_md.exists())
            self.assertTrue(q38_json.exists())
            self.assertTrue(q39_md.exists())
            self.assertTrue(q39_json.exists())

            # Verify content of combined note
            combined_content = combined_md.read_text(encoding="utf-8")
            self.assertIn("## 공통 지문", combined_content)
            self.assertIn("공통 지문 본문", combined_content)
            self.assertIn("38번 문제 본문", combined_content)
            self.assertIn("39번 문제 본문", combined_content)

            # Verify sidecar JSON
            q38_data = json.loads(q38_json.read_text(encoding="utf-8"))
            self.assertEqual(q38_data["question"]["question_set_id"], "38-39")
            self.assertEqual(q38_data["question"]["question_number"], "38")
            self.assertEqual(q38_data["question"]["points"], 3)
            self.assertEqual(q38_data["question"]["answer"], "①")

            q39_data = json.loads(q39_json.read_text(encoding="utf-8"))
            self.assertEqual(q39_data["question"]["question_set_id"], "38-39")
            self.assertEqual(q39_data["question"]["question_number"], "39")
            self.assertEqual(q39_data["question"]["points"], 2)
            self.assertEqual(q39_data["question"]["answer"], "⑤")


    def test_package_structure_and_imports(self):
        # Verify direct imports from domain, pipeline, storage, exporters
        from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
        from _scripts_2.apps.exam.exam_processor.domain import (
            new_item,
            structural_errors,
            normalize_markdown,
        )
        from _scripts_2.apps.exam.exam_processor.pipeline import process, WorkQueue, new_structure
        from _scripts_2.apps.exam.exam_processor.storage import Store, Conflict, inside_vault
        from _scripts_2.apps.exam.exam_processor.exporters import (
            write_sets,
            write_workbook,
            project_to_markdown,
        )

        self.assertIsNotNone(CanonicalQuestionRecord)
        self.assertIsNotNone(new_item)
        self.assertIsNotNone(structural_errors)
        self.assertIsNotNone(normalize_markdown)
        self.assertIsNotNone(process)
        self.assertIsNotNone(WorkQueue)
        self.assertIsNotNone(new_structure)
        self.assertIsNotNone(Store)
        self.assertIsNotNone(Conflict)
        self.assertIsNotNone(inside_vault)
        self.assertIsNotNone(write_sets)
        self.assertIsNotNone(write_workbook)
        self.assertIsNotNone(project_to_markdown)

    def test_source_audit_maps_runtime_results(self):
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import map_source_audit
        item = {"revision": "r1", "audit": {"revision": "r1", "status": "completed", "result": "suspected_difference"}}
        self.assertEqual(map_source_audit(item), "suspected_difference")
        item["audit"]["result"] = "unreadable"
        self.assertEqual(map_source_audit(item), "unreadable")
        item["audit"] = {"revision": "r1", "status": "completed"}
        self.assertEqual(map_source_audit(item), "no_difference")
        item["audit"] = {"revision": "old", "status": "completed", "result": "no_difference"}
        self.assertEqual(map_source_audit(item), "pending")

    def test_relation_errors_require_existing_passage_and_solution_ids(self):
        from _scripts_2.apps.exam.exam_processor.domain.job_relations import relation_errors
        from _scripts_2.apps.exam.exam_processor.domain.models import new_item
        passage = new_item("s", "35-37", "35-37", "지문", kind="passage")
        question = new_item("s", "35-37", "35", "문제")
        question["passage_id"] = "missing-passage"
        self.assertTrue(any("does not exist" in error for error in relation_errors({"items": [passage, question]})))
        question["passage_id"] = passage["id"]
        question["selected_solution_id"] = "missing-solution"
        question["solution_candidates"] = [{"id": "other"}]
        self.assertTrue(any("selected_solution_id" in error for error in relation_errors({"items": [passage, question]})))
        question["selected_solution_id"] = "other"
        self.assertEqual(relation_errors({"items": [passage, question]}), [])

    def test_number_relabel_keeps_internal_id(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = next(entry for entry in job["items"] if entry["kind"] == "question")
            original_id, original_revision = item["id"], item["revision"]
            store.update(job["id"], original_id, original_revision, {"number": "99"})
            updated = store.item(store.get(job["id"]), original_id)
            self.assertEqual(updated["id"], original_id)
            self.assertEqual(updated["number"], "99")
            self.assertNotEqual(updated["revision"], original_revision)

    def test_export_rejects_dangling_passage_id(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            for item in job["items"]:
                item["review"] = "approved"
                item["audit"] = {"revision": item["revision"], "status": "completed", "result": "no_difference"}
            job["items"][1]["passage_id"] = "missing"
            store.save(job)
            with self.assertRaisesRegex(ValueError, "정규 관계 오류"):
                store.export(job["id"], source_code="2606")


    def test_content_mutation_creates_immutable_snapshot_and_bumps_revision(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = next(entry for entry in job["items"] if entry["kind"] == "question")
            orig_id = item["id"]
            orig_rev = item["revision"]

            # Perform content mutation
            store.update(job["id"], orig_id, orig_rev, {"body": "새로운 본문 내용"})

            job_dir = store.job_dir(job["id"])
            snapshot_path = job_dir / "revisions" / orig_id / f"{orig_rev}.json"
            self.assertTrue(snapshot_path.is_file(), "Old snapshot must exist in revisions/")

            snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot_data["revision_id"], orig_rev)
            self.assertEqual(snapshot_data["canonical"]["question"]["question_id"], orig_id)

            updated = store.item(store.get(job["id"]), orig_id)
            self.assertNotEqual(updated["revision"], orig_rev)
            self.assertEqual(updated["body"], "새로운 본문 내용")
            self.assertEqual(updated["review"], "pending")

    def test_review_only_mutation_preserves_canonical_revision(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = job["items"][0]
            orig_id = item["id"]
            orig_rev = item["revision"]

            # Update note only
            store.update(job["id"], orig_id, orig_rev, {"note": "검토 의견 메모"})
            updated = store.item(store.get(job["id"]), orig_id)
            self.assertEqual(updated["revision"], orig_rev, "Note-only edit must not bump revision")
            self.assertEqual(updated["note"], "검토 의견 메모")

            # Waive audit
            store.waive_audit(job["id"], orig_id, orig_rev, "대조 생략 사유")
            updated = store.item(store.get(job["id"]), orig_id)
            self.assertEqual(updated["revision"], orig_rev, "Waive audit must not bump revision")
            self.assertEqual(updated["audit_waiver"]["status"], "waived")

    def test_passage_edit_invalidates_dependent_question_revision(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            passage = next(entry for entry in job["items"] if entry["kind"] == "passage")
            dep_question = next(entry for entry in job["items"] if entry.get("passage_id") == passage["id"])

            p_id, p_rev = passage["id"], passage["revision"]
            q_id, q_rev = dep_question["id"], dep_question["revision"]

            # Edit passage body
            store.update(job["id"], p_id, p_rev, {"body": "수정된 지문 본문"})

            updated_job = store.get(job["id"])
            updated_p = store.item(updated_job, p_id)
            updated_q = store.item(updated_job, q_id)

            self.assertNotEqual(updated_p["revision"], p_rev)
            self.assertNotEqual(updated_q["revision"], q_rev, "Dependent question revision must be invalidated")
            self.assertEqual(updated_q["review"], "pending")

            job_dir = store.job_dir(job["id"])
            dep_snap = job_dir / "revisions" / q_id / f"{q_rev}.json"
            self.assertTrue(dep_snap.is_file(), "Dependent question snapshot must be preserved")

    def test_content_mutation_resets_source_audit_to_pending(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = next(entry for entry in job["items"] if entry["kind"] == "question")
            q_id = item["id"]

            job_dir = store.job_dir(job["id"])
            record = store.records.get(job_dir, q_id)
            record.review.source_audit = "no_difference"
            record.review.human_approval = "approved"
            store.records.save(job_dir, record)

            # Edit content
            store.update(job["id"], q_id, record.provenance.revision_id, {"body": "수정된 문항 본문"})

            updated_record = store.records.get(job_dir, q_id)
            self.assertEqual(updated_record.review.source_audit, "pending", "Content mutation must reset source_audit to pending")
            self.assertEqual(updated_record.review.human_approval, "pending", "Content mutation must reset human_approval to pending")

    def test_crop_and_region_revision_paths_match_canonical_revision(self):
        import tempfile
        from pathlib import Path
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.md"
            path.write_text(NOTE, encoding="utf-8")
            store = Store(root / "data", root / "exports")
            job = store.import_file(path, "2026학년도 6월 모의평가", "화법과 작문")
            item = next(entry for entry in job["items"] if entry["kind"] == "question")
            q_id = item["id"]
            job_dir = store.job_dir(job["id"])

            # Setup fixture region and asset
            (job_dir / "regions").mkdir(parents=True, exist_ok=True)
            img_path = job_dir / "regions" / "r1.png"
            Image.new("RGB", (100, 100), color="white").save(img_path)

            record = store.records.get(job_dir, q_id)
            record.provenance.source_regions = [{"id": "r1", "image": "r1.png", "bbox": [0, 0, 100, 100], "content_role": "body", "width": 100, "height": 100}]
            from _scripts_2.apps.exam.exam_core.domain.canonical.schema import AssetDomain
            record.assets = [AssetDomain(asset_id="fig1", section="body", source_path="assets/fig1.png", path="assets/fig1.png", media_type="image/png")]
            record.body += "\n![[assets/fig1.png]]"
            store.records.save(job_dir, record)

            # Crop asset
            store.crop_source(job["id"], q_id, record.provenance.revision_id, "asset:fig1", [100, 100, 900, 900])

            updated_record = store.records.get(job_dir, q_id)
            new_rev = updated_record.provenance.revision_id
            asset_path = updated_record.assets[0].path
            self.assertIn(f"assets/{q_id}/{new_rev}/body-fig1.png", asset_path, "Asset path must contain exact new canonical revision")


if __name__ == "__main__":
    unittest.main()



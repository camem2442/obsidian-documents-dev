import copy
import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium

from _scripts_2.apps.exam.exam_processor.ingestion.formats.hanwangi_profile import profile
from _scripts_2.apps.exam.exam_processor.workbook import (
    new_structure, confirm_span, structure_errors, structure_report, split_pdfs, verify_sources,
)
from _scripts_2.apps.exam.exam_processor.store import Store, atomic_json
from _scripts_2.apps.exam.exam_processor.models import (
    structural_errors,
    link_solution,
    unlink_solution,
    new_item,
    invalidate,
)
from _scripts_2.apps.exam.exam_processor.work_queue import WorkQueue
from _scripts_2.apps.exam.exam_processor.ai import process
from _scripts_2.apps.exam.exam_processor.pipeline.ai import _extract_math_metadata
from _scripts_2.apps.exam.exam_processor.ingestion.formats import resolve_format
from _scripts_2.apps.exam.exam_processor.ingestion.formats.workbook_layout import (
    validate_layout,
    match_records,
)


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for role, width in (("problem", 200), ("solution", 210)):
            with pdfium.PdfDocument.new() as pdf:
                for i in range(3):
                    page = pdf.new_page(width + i, 300)
                    page.close()
                pdf.save(self.root / f"{role}.pdf")
        template = profile()
        template["page_counts"] = {"problem": 3, "solution": 3}
        template["nodes"] = template["nodes"][:2]
        self.structure = new_structure(template, self.root / "problem.pdf", self.root / "solution.pdf")

    def tearDown(self):
        self.temp.cleanup()

    def confirmed(self):
        result = self.structure
        for role in ("problem", "solution"):
            result = confirm_span(result, "A1", role, [1, 3], "fixture boundaries checked", 16, 18)
        return result

    def test_profile_has_full_toc_but_no_assumed_physical_pages(self):
        data = profile()
        self.assertEqual(len(data["nodes"]), 36)
        self.assertEqual(len({n["id"] for n in data["nodes"]}), 36)
        self.assertTrue(all(not span["pages"] and span["status"] == "pending"
                            for n in data["nodes"] for span in n["spans"].values()))
        self.assertEqual(data["samples"]["D1"]["problem"], list(range(42, 54)))
        nodes = {n["id"]: n for n in data["nodes"]}
        self.assertEqual(nodes["F7"]["section_title"], "통계적 추정")
        self.assertEqual(nodes["D1"]["section_code"], "1-1")
        self.assertNotIn("section_code", nodes["G"])

    def test_unconfirmed_structure_does_not_authorize_split(self):
        with self.assertRaisesRegex(ValueError, "먼저 확인"):
            split_pdfs(self.structure, self.root / "output", ["A1"])
        self.assertFalse((self.root / "output").exists())

    def test_confirmation_preserves_input_and_history(self):
        updated = self.confirmed()
        self.assertEqual(len(updated["history"]), 2)
        self.assertEqual(self.structure["nodes"][0]["spans"]["problem"]["status"], "pending")
        same = confirm_span(updated, "A1", "problem", [1, 3], "fixture boundaries checked", 16, 18)
        self.assertEqual(same, updated)
        report = structure_report(updated)
        self.assertFalse(report["structure_confirmed"])
        self.assertFalse(report["content_reviewed"])
        self.assertEqual(report["unassigned_pages"]["problem"], [2])

    def test_duplicate_out_of_order_invalid_pages_and_empty_evidence(self):
        for pages in ([0], [4], [1, 1], [3, 1], [True], []):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                confirm_span(self.structure, "A1", "problem", pages, "checked")
        with self.assertRaises(ValueError):
            confirm_span(self.structure, "A1", "problem", [1], " ")

    def inventoried(self):
        result = self.structure
        for role in ("problem", "solution"):
            result = confirm_span(result, "A1", role, [1], "checked", 10, 10)
            result = confirm_span(result, "A2", role, [2], "checked", 13, 13)
        result["page_inventory"] = {role: [
            {"page": 1, "printed_page": 10, "kind": "node", "node": "A1", "evidence": "page image"},
            {"page": 2, "printed_page": 13, "kind": "node", "node": "A2", "evidence": "page image"},
            {"page": 3, "printed_page": None, "kind": "back_matter", "evidence": "blank leaf"},
        ] for role in ("problem", "solution")}
        result["source_issues"] = [{"document": "problem", "status": "unresolved",
                                    "description": "Printed pages 11-12 are absent."}]
        return result

    def test_inventory_accounts_for_nonchapter_pages_without_approving_content(self):
        data = self.inventoried()
        report = structure_report(data)
        self.assertTrue(report["inventory_complete"])
        self.assertFalse(report["source_complete"])
        self.assertEqual(report["unassigned_pages"]["problem"], [3])
        self.assertEqual(report["unclassified_pages"]["problem"], [])
        self.assertEqual(report["source_issues"][0]["status"], "unresolved")
        self.assertFalse(report["content_reviewed"])
        data["source_issues"][0]["status"] = "resolved"
        self.assertTrue(structure_report(data)["source_complete"])
        data["page_inventory"]["problem"].pop()
        self.assertFalse(structure_report(data)["inventory_complete"])

    def test_inventory_rejects_duplicate_pages_and_conflicting_ownership(self):
        for update in ({"page": 2}, {"kind": "back_matter"}, {"node": "A2"},
                       {"printed_page": True}, {"evidence": ""}):
            data = self.inventoried()
            data["page_inventory"]["problem"][0].update(update)
            with self.subTest(update=update):
                self.assertTrue(structure_errors(data))

    def test_split_keeps_printed_page_gaps_and_source_warnings(self):
        data = self.inventoried()
        report = split_pdfs(data, self.root / "inventoried-output", ["A1", "A2"])
        self.assertEqual(report["source_issues"], data["source_issues"])
        self.assertFalse(report["source_complete"])
        self.assertEqual(report["files"][2]["page_map"],
                         [{"page": 1, "source_page": 2, "printed_page": 13}])

    def test_overlapping_chapters_are_not_silently_duplicated(self):
        with self.assertRaisesRegex(ValueError, "겹칩니다"):
            confirm_span(self.confirmed(), "A2", "problem", [3], "checked")

    def test_changed_original_blocks_split(self):
        confirmed = self.confirmed()
        with pdfium.PdfDocument.new() as pdf:
            pdf.new_page(100, 100).close()
            pdf.save(self.root / "problem.pdf")
        with self.assertRaisesRegex(ValueError, "변경"):
            split_pdfs(confirmed, self.root / "output", ["A1"])
        self.assertFalse((self.root / "output").exists())

    def test_split_preserves_physical_mapping_and_page_size(self):
        report = split_pdfs(self.confirmed(), self.root / "output", ["A1"])
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["content_reviewed"])
        self.assertEqual(len(report["files"]), 2)
        for entry in report["files"]:
            self.assertEqual(entry["page_map"], [{"page": 1, "source_page": 1}, {"page": 2, "source_page": 3}])
            with pdfium.PdfDocument(self.root / "output" / entry["path"]) as pdf:
                self.assertEqual(len(pdf), 2)
                page = pdf[1]
                try:
                    self.assertEqual(page.get_size()[0], 202 if entry["document"] == "problem" else 212)
                finally:
                    page.close()
        with self.assertRaises(FileExistsError):
            split_pdfs(self.confirmed(), self.root / "output", ["A1"])

    def test_identity_is_bound_to_source_and_not_editable_title(self):
        edited = copy.deepcopy(self.structure)
        edited["nodes"][0]["title"] = "사용자 목차 이름"
        self.assertEqual(edited["id"], self.structure["id"])
        verify_sources(edited)

    def test_same_problem_and_solution_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "같은 파일"):
            new_structure(profile(), self.root / "problem.pdf", self.root / "problem.pdf")

    def test_bad_schema_and_unsafe_node_id(self):
        self.assertTrue(structure_errors({"schema_version": 2}))
        edited = copy.deepcopy(self.structure)
        edited["nodes"][0]["id"] = "../../outside"
        self.assertTrue(structure_errors(edited))

    def layout(self):
        def record(key, kind, role):
            return {"id": key, "node": "A1", "kind": kind, "number": "A1·01",
                    "regions": [{"document": role, "page": 1, "bbox": [5, 5, 80, 100],
                                 "width": 200 if role == "problem" else 210, "height": 300}]}
        return {"schema_version": 1, "workbook_id": self.structure["id"], "status": "confirmed",
                "evidence": "synthetic region fixture", "items": [record("q1", "question", "problem"),
                                                                       record("s1", "solution", "solution")]}

    def test_matching_keeps_representative_and_exercise_distinct(self):
        layout = self.layout()
        example = copy.deepcopy(layout["items"][0])
        example.update(id="example", number="A1", subtype="example")
        layout["items"].append(example)
        matches = match_records(layout)
        self.assertEqual(matches["q1"]["status"], "matched")
        self.assertEqual(matches["example"]["status"], "unmatched")
        other = copy.deepcopy(layout["items"][1])
        other["id"] = "s2"
        layout["items"].append(other)
        self.assertEqual(match_records(layout)["q1"]["status"], "ambiguous")

    def test_unconfirmed_and_wrong_source_layouts_are_rejected(self):
        layout = self.layout()
        layout["status"] = "candidate"
        with self.assertRaisesRegex(ValueError, "先|먼저"):
            validate_layout(layout, self.confirmed())
        layout["status"] = "confirmed"
        layout["workbook_id"] = "wrong"
        with self.assertRaises(ValueError):
            validate_layout(layout, self.confirmed())

    def test_legacy_pdf_route_and_explicit_workbook_route(self):
        self.assertEqual(resolve_format("book.pdf").format_id, "kice_korean")
        self.assertEqual(resolve_format("book.pdf", "hanwangi_2026_probability").format_id,
                         "hanwangi_2026_probability")

    def test_bold_answer_label_preserves_explicit_short_answer_without_solving(self):
        item = {"body": "[4점]", "solution": "계산 결과 180\n**정답** 90"}
        _extract_math_metadata(item)
        self.assertEqual(item["answer"], "90")
        for solution in ("계산 결과 90", "**정답** 90\n정답 91"):
            ambiguous = {"body": "[4점]", "solution": solution}
            _extract_math_metadata(ambiguous)
            self.assertNotIn("answer", ambiguous)

    def test_fresh_canonical_import_and_service_link_keep_source_roles(self):
        sfile, lfile = self.root / "structure.json", self.root / "layout.json"
        atomic_json(sfile, self.confirmed())
        atomic_json(lfile, self.layout())
        store = Store(self.root / "fresh", self.root / "exports")
        job = store.import_file(self.root / "problem.pdf", "새 한완기", "확률과 통계",
                                solution=self.root / "solution.pdf", format_id="hanwangi_2026_probability",
                                structure=sfile, layout=lfile)
        jid, item = job["id"], job["items"][0]
        rec = store.records.get(store.job_dir(jid), item["id"])
        self.assertTrue(rec.provenance.source_regions)
        self.assertTrue(all(r["content_role"] == "body" for r in rec.provenance.source_regions))
        original = copy.deepcopy(rec.provenance.source_regions)
        candidate = item["solution_candidates"][0]
        linked = store.link_solution(jid, item["id"], item["revision"], candidate)["items"][0]
        rec = store.records.get(store.job_dir(jid), item["id"])
        solution_regions = [r for r in rec.provenance.source_regions if r["content_role"] == "solution"]
        self.assertEqual([r["image"] for r in solution_regions], [r["image"] for r in candidate["regions"]])
        self.assertIn("solution", linked["transcription_pending"])
        unlinked = store.unlink_solution(jid, linked["id"], linked["revision"])["items"][0]
        self.assertEqual(store.records.get(store.job_dir(jid), item["id"]).provenance.source_regions, original)
        linked = store.link_solution(jid, unlinked["id"], unlinked["revision"], candidate)["items"][0]
        for section in ("body", "solution"):
            current = store.get(jid)["items"][0]
            def caller(payload):
                expected = original if section == "body" else solution_regions
                self.assertEqual([Path(x).name for x in payload["images"]], [r["image"] for r in expected])
                return {"data": {"body": "값은 $2^3=8$이다.", "diagrams": []},
                        "provider": "fixture", "configured_model": "fixture"}
            process(store, jid, current["id"], current["revision"], "extract", section, caller)
        self.assertEqual(store.get(jid)["items"][0]["transcription_pending"], [])

    def test_import_transcribe_link_audit_export_uses_math_subject(self):
        sfile, lfile = self.root / "structure.json", self.root / "layout.json"
        atomic_json(sfile, self.confirmed())
        atomic_json(lfile, self.layout())
        store = Store(self.root / "jobs", self.root / "exports")
        kwargs = dict(format_id="hanwangi_2026_probability", structure=sfile, layout=lfile)
        job = store.import_file(self.root / "problem.pdf", "테스트 수학", "확률과 통계",
                                solution=self.root / "solution.pdf", **kwargs)
        self.assertEqual(job["subject"], "수학")
        self.assertIn("workbook_view", job["items"][0])
        self.assertNotIn("workbook_view", (store.job_dir(job["id"]) / "job.json").read_text())
        item = job["items"][0]
        self.assertTrue(structural_errors(item))
        self.assertFalse(item.get("selected_solution_id"))
        link_solution(item, item["solution_candidates"][0])
        store.save(job)
        for section, body in (("body", "값을 구하시오. $2^3$"), ("solution", "$$2^3=8$$")):
            item = store.get(job["id"])["items"][0]
            def caller(payload):
                self.assertIn("수학 교재", payload["prompt"])
                return {"data": {"body": body, "diagrams": []}, "provider": "fixture", "configured_model": "fixture"}
            process(store, job["id"], item["id"], item["revision"], "extract", section, caller)
        item = store.get(job["id"])["items"][0]
        self.assertFalse(item["transcription_pending"])
        job = store.update(job["id"], item["id"], item["revision"], {"answer": "8", "correct_rate": 0.34})
        item = job["items"][0]
        process(store, job["id"], item["id"], item["revision"], "audit", caller=lambda _: {
            "data": {"result": "no_difference", "issues": []}, "provider": "fixture", "configured_model": "fixture"})
        store.review(job["id"], item["id"], item["revision"], "approve")
        exported = store.export(job["id"], source_code="math-fixture")
        batch = Path(exported["last_export"])
        note = (batch / "A1/A1-01.md").read_text()
        json_sidecar = (batch / "A1/A1-01.json").read_text()
        self.assertIn("## 테스트 수학 A1-01번", note)
        self.assertIn("<!-- SECTION:PROBLEM_START -->\n\n값을 구하시오. $2^3$", note)
        self.assertIn("<!-- SECTION:SOLUTION_START -->\n\n**정답: 8**", note)
        self.assertIn('subject: "수학"', note)
        self.assertIn('content_type: "original"', note)
        self.assertIn("correct_rate: 0.34", note)
        self.assertIn('"schema_version": "0.3.0"', json_sidecar)
        self.assertIn('"subject": "수학"', json_sidecar)
        self.assertIn('"correct_rate": 0.34', json_sidecar)
        self.assertIn('"section_title": "순열과 조합"', json_sidecar)
        self.assertIn('"code": "ps.counting"', json_sidecar)
        self.assertIn('"taxonomy_id": "curriculum-2022-math-probability-statistics"', json_sidecar)
        from _scripts_2.apps.exam.exam_processor.exporters.rebuild import rebuild_markdown_batch
        rebuilt = self.root / "rebuilt-workbook"
        rebuild_markdown_batch(batch, rebuilt)
        self.assertEqual((rebuilt / "A1/A1-01.md").read_text(), note)
        self.assertTrue((batch / "pdfs/A1-problem.pdf").exists())
        self.assertTrue((batch / "index.md").exists())
        for code in ("../outside", "..", "/tmp/outside", "nested\\outside"):
            with self.assertRaises(ValueError):
                store.export(job["id"], source_code=code)

    def test_math_metadata_is_extracted_only_from_explicit_transcription(self):
        item = {"body": "문항 [4점]", "solution": "정답: ③", "warnings": []}
        _extract_math_metadata(item)
        self.assertEqual(item["points"], 4.0)
        self.assertEqual(item["answer"], "③")

        ambiguous = {"body": "문항 [3점]", "solution": "정답: ①\n정답: ②", "warnings": []}
        _extract_math_metadata(ambiguous)
        self.assertNotIn("answer", ambiguous)
        self.assertTrue(ambiguous["warnings"])

    def queue_fixture(self):
        store = Store(self.root / "jobs", self.root / "exports")
        item = new_item("fixture", "A1", "A1-01", "pending", regions=[{"image": "body.png"}])
        item.update(solution="pending", solution_regions=[{"image": "solution.png"}],
                    transcription_pending=["body", "solution"])
        job = {"id": "a" * 32, "workbook": {"id": "fixture"}, "items": [item]}
        store.save(job)
        return store, job, item

    def test_queue_transcribes_both_sections_and_retries_only_failure(self):
        store, job, item = self.queue_fixture()
        calls = []
        def processor(store, job_id, item_id, expected, operation, section):
            calls.append(section)
            if section == "solution" and calls.count(section) == 1:
                raise ValueError("fixture failure")
            current = store.get(job_id)
            target = store.item(current, item_id)
            invalidate(target)
            target[section] = "transcribed " + section
            target["transcription_pending"].remove(section)
            store.save(current)
        queue = WorkQueue(store, processor)
        queue.prepare(job["id"], "extract")
        queue.run(job["id"])
        self.assertEqual(calls, ["body", "solution"])
        queue.prepare(job["id"], "extract", "retry")
        queue.run(job["id"])
        self.assertEqual(calls, ["body", "solution", "solution"])
        current = store.get(job["id"])
        self.assertFalse(current["items"][0]["transcription_pending"])
        self.assertEqual([e["status"] for e in current["queue"]["entries"]], ["completed", "completed"])

    def test_queue_protects_edited_solution_while_body_runs(self):
        store, job, item = self.queue_fixture()
        calls = []
        def processor(store, job_id, item_id, expected, operation, section):
            calls.append(section)
            current = store.get(job_id)
            current["items"][0]["solution"] = "user edit"
            store.save(current)
        queue = WorkQueue(store, processor)
        queue.prepare(job["id"], "extract")
        queue.run(job["id"])
        self.assertEqual(calls, ["body"])
        self.assertEqual(store.get(job["id"])["queue"]["entries"][1]["status"], "skipped")

    def test_pending_transcription_prevents_paid_audit(self):
        store, job, item = self.queue_fixture()
        with self.assertRaisesRegex(ValueError, "전사"):
            process(store, job["id"], item["id"], item["revision"], "audit",
                    caller=lambda _: self.fail("pending content must not call AI"))
        with self.assertRaises(ValueError):
            WorkQueue(store).prepare(job["id"], "audit")

    def test_solution_unlink_restores_only_solution_pending_state(self):
        item = new_item("fixture", "A1", "1", "pending")
        item["transcription_pending"] = ["body", "solution"]
        link_solution(item, {"id": "s", "body": "pending", "transcription_required": True})
        item["transcription_pending"].clear()
        self.assertEqual(item["history"][0]["transcription_pending"], ["body", "solution"])
        unlink_solution(item)
        self.assertEqual(item["transcription_pending"], ["solution"])


if __name__ == "__main__":
    unittest.main()

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from _scripts_2.ai_core import AIClient

from _scripts_2.apps.exam.exam_processor.models import (
    audit_approval_gate, format_output_markdown, identity, link_solution, markdown_note, structural_errors, unlink_solution,
)
from _scripts_2.apps.exam.exam_processor.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.ai import process, symbol_differences
from _scripts_2.apps.exam.exam_processor.ai_worker import (
    validate, parse_json, failure_label, public_error, response_schema,
)
from _scripts_2.apps.exam.exam_processor.ingestion.formats.korean_notes import parse_notes
from _scripts_2.apps.exam.exam_processor.ingestion.formats.kice_korean import page_range
from _scripts_2.apps.exam.exam_processor.ingestion.formats import (
    BaseFormatModule,
    FORMATS,
    get_format,
    resolve_format,
)

NOTE = """# [38~39번]
## (가)
원본 지문입니다.
## 문제
### 38. 첫째 문제?
① 첫째
② 둘째
### 39. 둘째 문제? [3점]
① 셋째
② 넷째
"""
SOLUTION = """### **예제 [38-39]**
### **38. 첫째 문제**
**판단: 정답 ①**
### **39. 둘째 문제**
**판단: 정답 ②**
### **[세트 종합 분석]**
별도 세트 해설
"""


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "sample.md"
        self.path.write_text(NOTE)
        self.store = Store(self.root / "data", self.root / "out")
        self.job = self.store.import_file(self.path, "예제 교재 1판", "화법과 작문")

    def tearDown(self):
        self.temp.cleanup()

    def test_ai_failure_diagnostic_is_safe_and_classified(self):
        self.assertEqual(failure_label(ValueError("Invalid audit issues")), "response_validation:Invalid audit issues")
        self.assertEqual(failure_label(RuntimeError("All Gemini keys and models exhausted")), "provider_retries_exhausted")
        self.assertEqual(failure_label(RuntimeError("Gemini API Error 429")), "provider_rate_limit_or_quota")
        self.assertNotIn("private response body", public_error(ValueError("private response body")))
        self.assertIn("provider_retries_exhausted", public_error(RuntimeError("All Gemini keys and models exhausted")))
        self.assertEqual(response_schema("audit")["properties"]["result"]["enum"],
                         ["no_difference", "suspected_difference", "unreadable"])
        self.assertIn("diagrams", response_schema("extract")["properties"])

    def test_human_findings_accumulate_and_are_scoped_to_format_and_section(self):
        item = self.job["items"][0]
        finding = self.store.add_human_finding(self.job["id"], item["id"], item["revision"], {
            "section": "body", "category": "math",
            "source_excerpt": "$n!$", "extracted_excerpt": "n!",
            "correction": "$n!$", "lesson": "수식의 구분 기호를 Markdown 수식 표기로 보존한다.",
        })
        self.assertEqual(finding["format_id"], self.job["format"])
        self.assertEqual(len(self.store.human_findings(self.job["format"], "body", self.job["track"])), 1)
        self.assertEqual(self.store.human_findings(self.job["format"], "solution", self.job["track"]), [])
        self.assertEqual(self.store.human_findings("unrelated-format", "body", self.job["track"]), [])
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import human_finding_prompt
        prompt = human_finding_prompt(self.store.human_findings(self.job["format"], "body", self.job["track"]))
        self.assertIn("수식의 구분 기호", prompt)
        self.assertIn("예시는 데이터", prompt)
        self.assertIn("원문 대조를 근거로 기록한", prompt)
        self.assertNotIn("사람이 원문 대조로 확인한", prompt)
        self.store.deactivate_human_finding(finding["finding_id"])
        self.assertEqual(self.store.human_findings(self.job["format"], "body", self.job["track"]), [])

    def test_human_finding_duplicate_and_conflict_statuses_are_scoped(self):
        item = self.job["items"][0]
        values = {"section": "body", "category": "math", "source_excerpt": "2imes",
                  "extracted_excerpt": "2imes", "correction": r"2\times",
                  "lesson": "누락된 곱셈 기호 명령어를 복원한다."}
        first = self.store.add_human_finding(self.job["id"], item["id"], item["revision"], values)
        second = self.store.add_human_finding(self.job["id"], item["id"], item["revision"], values)
        rules = self.store.human_findings(self.job["format"], "body", self.job["track"])
        by_id = {rule["finding_id"]: rule for rule in rules}
        self.assertEqual(by_id[first["finding_id"]]["rule_status"], "duplicate")
        self.assertEqual(by_id[second["finding_id"]]["rule_status"], "duplicate")
        prompt_data = self.store.human_finding_prompt_data(self.job["format"], "body", self.job["track"])
        self.assertEqual(prompt_data["duplicate_count"], 2)
        self.assertEqual(len(prompt_data["findings"]), 1)
        conflict_values = {**values, "correction": "2 ×", "lesson": "누락된 곱셈 기호를 원래 기호로 복원한다."}
        third = self.store.add_human_finding(self.job["id"], item["id"], item["revision"], conflict_values)
        rules = self.store.human_findings(self.job["format"], "body", self.job["track"])
        by_id = {rule["finding_id"]: rule for rule in rules}
        self.assertTrue(all(by_id[key]["rule_status"] == "conflict"
                            for key in (first["finding_id"], second["finding_id"], third["finding_id"])))
        prompt_data = self.store.human_finding_prompt_data(self.job["format"], "body", self.job["track"])
        self.assertEqual(prompt_data["conflict_count"], 3)
        self.assertEqual(prompt_data["findings"], [])
        resolution = self.store.resolve_human_finding_conflict(
            first["finding_id"], "원본 PDF에서 곱셈 기호가 확인되어 이 표기를 기준으로 선택한다.")
        rules = self.store.human_findings(self.job["format"], "body", self.job["track"])
        by_id = {rule["finding_id"]: rule for rule in rules}
        self.assertEqual(by_id[first["finding_id"]]["rule_status"], "resolved_selected")
        self.assertTrue(all(by_id[key]["rule_status"] == "resolved_excluded"
                            for key in (second["finding_id"], third["finding_id"])))
        prompt_data = self.store.human_finding_prompt_data(self.job["format"], "body", self.job["track"])
        self.assertEqual(prompt_data["conflict_count"], 0)
        self.assertEqual(prompt_data["resolved_count"], 1)
        self.assertEqual([rule["finding_id"] for rule in prompt_data["findings"]], [first["finding_id"]])
        self.assertIn("원본 PDF", resolution["rationale"])
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import human_finding_prompt
        prompt = human_finding_prompt(prompt_data["findings"])
        self.assertIn("누락된 곱셈 기호 명령어를 복원한다", prompt)
        self.assertNotIn("누락된 곱셈 기호를 원래 기호로 복원한다", prompt)
        job = self.store.get(self.job["id"])
        item = self.store.item(job, item["id"])
        item["regions"] = [{"image": "fixture.png", "page": 1, "document": "problem"}]
        self.store.save(job)
        captured = {}

        def caller(request):
            captured.update(request)
            return {"data": {"body": "검증용 전사 결과", "diagrams": []},
                    "provider": "fixture", "configured_model": "fixture"}

        from _scripts_2.apps.exam.exam_processor.pipeline.ai import process
        process(self.store, job["id"], item["id"], item["revision"], "extract", caller=caller)
        self.assertIn(first["finding_id"], captured["prompt"])
        self.assertNotIn(second["finding_id"], captured["prompt"])
        self.assertNotIn(third["finding_id"], captured["prompt"])
        run = self.store.item(self.store.get(job["id"]), item["id"])["ai_runs"][-1]
        self.assertEqual(run["human_rule_stats"]["included_finding_ids"], [first["finding_id"]])
        self.assertEqual(run["human_rule_stats"]["included_rules"][0]["finding_id"], first["finding_id"])
        self.assertIn("누락된 곱셈 기호 명령어를 복원한다",
                      run["human_rule_stats"]["included_rules"][0]["lesson"])
        self.assertEqual(set(run["human_rule_stats"]["excluded_resolved_finding_ids"]),
                         {second["finding_id"], third["finding_id"]})

    def test_new_rule_invalidates_old_conflict_resolution(self):
        item = self.job["items"][0]
        values = {"section": "body", "category": "math", "source_excerpt": "ximes",
                  "extracted_excerpt": "ximes", "correction": r"x\times",
                  "lesson": "수식 명령어 누락을 원본과 대조해 복원한다."}
        first = self.store.add_human_finding(self.job["id"], item["id"], item["revision"], values)
        other = self.store.add_human_finding(self.job["id"], item["id"], item["revision"],
                                             {**values, "correction": "x ×"})
        self.store.resolve_human_finding_conflict(first["finding_id"], "원본 표기와 일치하는 규칙을 기준으로 선택한다.")
        self.store.add_human_finding(self.job["id"], item["id"], item["revision"],
                                     {**values, "correction": "x\\cdot"})
        data = self.store.human_finding_prompt_data(self.job["format"], "body", self.job["track"])
        self.assertEqual(data["conflict_count"], 3)
        self.assertEqual(data["findings"], [])

    def test_human_finding_rejects_stale_revision_and_short_guidance(self):
        item = self.job["items"][0]
        values = {"section": "body", "category": "other", "lesson": "규칙"}
        with self.assertRaisesRegex(ValueError, "10~1000자"):
            self.store.add_human_finding(self.job["id"], item["id"], item["revision"], values)
        values["lesson"] = "재사용 가능한 확인 규칙입니다."
        with self.assertRaisesRegex(ValueError, "버전이 바뀌었습니다"):
            self.store.add_human_finding(self.job["id"], item["id"], "stale", values)

    def test_matching_human_finding_is_sent_on_later_extraction_only(self):
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import process
        item = self.job["items"][0]
        self.store.add_human_finding(self.job["id"], item["id"], item["revision"], {
            "section": "body", "category": "math",
            "lesson": "수식은 원문의 수식 범위를 포함해 LaTeX 구분자로 보존한다.",
        })
        job = self.store.get(self.job["id"])
        item = job["items"][0]
        item["regions"] = [{"image": "fixture.png", "page": 1, "document": "problem"}]
        self.store.save(job)
        captured = {}
        def caller(request):
            captured.update(request)
            return {"data": {"body": "추출 결과", "diagrams": []},
                    "provider": "fixture", "configured_model": "fixture"}
        process(self.store, job["id"], item["id"], item["revision"], "extract", caller=caller)
        self.assertIn("수식은 원문의 수식 범위", captured["prompt"])
        self.assertEqual(captured["operation"], "extract")

    def test_gemini_vision_requests_json_output_mode(self):
        image_path = self.root / "vision.png"
        Image.new("RGB", (8, 8), "white").save(image_path)

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode()

        # Install fixture keys before construction: AIClient eagerly creates
        # every provider, so replacing api_keys afterwards still reads Keychain.
        with patch("_scripts_2.ai_core.keychain.get_api_keys", return_value=["fixture-key"]):
            client = AIClient(primary_provider="gemini", gemini_model="gemini-3.6-flash")
        with patch("_scripts_2.ai_core.providers.gemini.urllib.request.urlopen", return_value=Response()) as request, \
             patch("_scripts_2.ai_core.usage_tracker.record_usage"):
            client.generate_images([image_path], "JSON only", model_override="gemini-3.6-flash",
                                   response_mime_type="application/json",
                                   response_schema=response_schema("extract"), max_retries=1)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("diagrams", payload["generationConfig"]["responseSchema"]["properties"])


    def test_format_registry_contract_and_resolution(self):
        self.assertEqual(set(FORMATS), {"korean_notes", "kice_korean", "hanwangi_2026_probability", "kice_english"})
        self.assertTrue(all(isinstance(module, BaseFormatModule) for module in FORMATS.values()))
        self.assertEqual(resolve_format("sample.md").format_id, "korean_notes")
        self.assertEqual(resolve_format("sample.pdf").format_id, "kice_korean")
        self.assertIs(get_format("kice_korean"), FORMATS["kice_korean"])
        with self.assertRaises(ValueError):
            resolve_format("sample.png")
        with self.assertRaises(ValueError):
            resolve_format("sample.md", "kice_korean")

    def test_store_import_runs_through_registered_format(self):
        solution_path = self.root / "solution.md"
        solution_path.write_text(SOLUTION, encoding="utf-8")
        job = self.store.import_file(
            self.path,
            "해설 포함 예제",
            "화법과 작문",
            solution=str(solution_path),
        )
        self.assertEqual(job["format"], "korean_notes")
        self.assertEqual([item["answer"] for item in job["items"][1:]], ["①", "②"])

    def audit(self, record):
        return process(self.store, self.job["id"], record["id"], record["revision"], "audit", caller=lambda _: {"data": {"result": "no_difference", "issues": []}, "provider": "test", "configured_model": "fixture"})

    def test_split_and_missing_metadata(self):
        passage, first, second = self.job["items"]
        self.assertNotIn("## 문제", passage["body"])
        self.assertEqual(first["number"], "38")
        self.assertEqual(first["passage_id"], second["passage_id"])
        self.assertIsNone(first["points"])
        self.assertEqual(second["points"], 3)
        self.assertEqual(first["answer"], "")

    def test_bold_numbered_passage_not_questions(self):
        text = "[40~41] 자료\n**1. 활동명**\n본문\n**40. 질문**\n① 가\n**41. 질문**\n① 나"
        items, warnings = parse_notes(text, "s", "매체")
        self.assertEqual([i["number"] for i in items], ["40-41", "40", "41"])
        self.assertIn("활동명", items[0]["body"])

    def test_solution_matching_preserves_source(self):
        items, warnings = parse_notes(NOTE, "s", "화법과 작문", SOLUTION)
        self.assertEqual(items[1]["answer"], "①")
        self.assertEqual(items[2]["answer"], "②")
        self.assertNotIn("세트 종합", items[2]["solution"])

    def test_solution_link_and_unlink_restore_previous_content(self):
        item = self.job["items"][1]
        item.update({
            "solution": "기존 해설", "solution_reference": "기존 원문", "answer": "⑤",
            "solution_candidates": [{
                "id": "candidate", "number": "38", "body": "EBS 해설",
                "answer": "①", "regions": [],
            }],
            "solution_match": {
                "status": "matched",
                "basis": ["academic_year", "track", "question_number"],
            },
        })
        link_solution(item, item["solution_candidates"][0])
        self.assertEqual(
            (item["solution"], item["answer"], item["selected_solution_id"]),
            ("EBS 해설", "①", "candidate"),
        )
        self.assertTrue(item["solution_match"]["confirmed_by_user"])
        self.assertNotIn("해설 후보의 연결을 확인하세요.", structural_errors(item))
        unlink_solution(item)
        self.assertEqual(
            (item["solution"], item["solution_reference"], item["answer"]),
            ("기존 해설", "기존 원문", "⑤"),
        )
        self.assertFalse(item["selected_solution_id"])
        self.assertIn("해설 후보의 연결을 확인하세요.", structural_errors(item))

    def test_selected_ambiguous_solution_is_explicitly_resolved(self):
        item = self.job["items"][1]
        item["solution_candidates"] = [
            {"id": "one", "number": "38", "body": "첫 해설", "regions": []},
            {"id": "two", "number": "38", "body": "둘째 해설", "regions": []},
        ]
        item["solution_match"] = {
            "status": "ambiguous",
            "basis": ["question_number"],
        }
        self.assertIn("해설 매칭 상태를 확인하세요.", structural_errors(item))
        link_solution(item, item["solution_candidates"][1])
        self.assertNotIn("해설 매칭 상태를 확인하세요.", structural_errors(item))

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            parse_notes(NOTE + "\n### 38. 중복\n", "s", "화법과 작문")

    def test_missing_detected(self):
        items, warnings = parse_notes(NOTE.replace("[38~39번]", "[38~40번]"), "s", "화법과 작문")
        self.assertTrue(warnings)

    def test_same_input_keeps_edits(self):
        i = self.job["items"][1]
        edited = self.store.update(self.job["id"], i["id"], i["revision"], {"body": "사용자 수정"})
        again = self.store.import_file(self.path, "예제 교재 1판", "화법과 작문")
        self.assertEqual(again["items"][1]["body"], "사용자 수정")
        self.assertEqual(self.path.read_text(), NOTE)

    def test_source_track_isolation(self):
        other = self.store.import_file(self.path, "예제 교재 1판", "매체")
        self.assertNotEqual(other["items"][1]["id"], self.job["items"][1]["id"])

    def test_revision_conflict(self):
        i = self.job["items"][1]
        self.store.update(self.job["id"], i["id"], i["revision"], {"unit": "수정"})
        with self.assertRaises(Conflict):
            self.store.update(self.job["id"], i["id"], i["revision"], {"body": "오래된 수정"})

    def test_edit_invalidates_audit_and_approval(self):
        shared = self.job["items"][0]
        self.audit(shared)
        approved = self.store.review(self.job["id"], shared["id"], shared["revision"], "approve")
        updated = self.store.update(self.job["id"], shared["id"], shared["revision"], {"body": "새 지문"})
        self.assertEqual(updated["items"][0]["review"], "pending")
        self.assertNotEqual(updated["items"][0]["audit"]["revision"], updated["items"][0]["revision"])
        self.assertNotEqual(updated["items"][1]["revision"], self.job["items"][1]["revision"])

    def test_unreviewed_and_missing_passage_block_approval(self):
        i = self.job["items"][1]
        with self.assertRaises(ValueError):
            self.store.review(self.job["id"], i["id"], i["revision"], "approve")
        self.audit(i)
        with self.assertRaises(ValueError):
            self.store.review(self.job["id"], i["id"], i["revision"], "approve")

    def test_audit_waiver_allows_approve_without_ai(self):
        shared = self.job["items"][0]
        self.store.waive_audit(
            self.job["id"], shared["id"], shared["revision"], "육안으로 원본과 일치 확인",
        )
        updated = self.store.review(self.job["id"], shared["id"], shared["revision"], "approve")
        self.assertEqual(self.store.item(updated, shared["id"])["review"], "approved")

    def test_audit_waiver_default_reason_when_empty(self):
        shared = self.job["items"][0]
        self.store.waive_audit(self.job["id"], shared["id"], shared["revision"], "")
        waiver = self.store.get(self.job["id"])["items"][0]["audit_waiver"]
        self.assertEqual(waiver["reason"], "원본 대조 생략")

    def test_late_ai_does_not_overwrite(self):
        i = self.job["items"][1]
        def caller(_):
            self.store.update(self.job["id"], i["id"], i["revision"], {"body": "new"})
            return {"data": {"result": "no_difference", "issues": []}, "provider": "test", "configured_model": "test"}
        with self.assertRaises(Conflict):
            process(self.store, self.job["id"], i["id"], i["revision"], "audit", caller=caller)
        self.assertEqual(self.store.get(self.job["id"])["items"][1]["body"], "new")

    def test_ai_failure_keeps_content(self):
        i = self.job["items"][1]
        def fail(_):
            raise ValueError("fixture failure")
        with self.assertRaises(ValueError):
            process(self.store, self.job["id"], i["id"], i["revision"], "audit", caller=fail)
        self.assertEqual(self.store.get(self.job["id"])["items"][1], i)

    def test_immutable_export_and_shared_links(self):
        for i in self.job["items"]:
            self.audit(i)
            self.store.review(self.job["id"], i["id"], i["revision"], "approve")
        export = self.store.export(self.job["id"])
        folder = Path(export["last_export"])
        first = folder / "38-39" / (self.job["items"][1]["id"] + ".md")
        text = first.read_text()
        self.assertIn('q_number: "38"', text)
        self.assertIn("points: null", text)
        self.assertEqual(text.count("SECTION:PROBLEM_START"), 1)
        self.assertIn("![[" + self.job["items"][0]["id"] + ".md]]", text)
        from _scripts_2.apps.exam.exam_processor.exporters.rebuild import rebuild_markdown_batch
        self.assertTrue((folder / "canonical-manifest.json").is_file())
        rebuilt = self.root / "canonical-rebuilt"
        rebuilt_files = rebuild_markdown_batch(folder, rebuilt)
        self.assertEqual(len(rebuilt_files), len(self.job["items"]))
        rebuilt_question = (rebuilt / "38-39" / (self.job["items"][1]["id"] + ".md")).read_text()
        self.assertIn("![[" + self.job["items"][0]["id"] + ".md]]", rebuilt_question)
        first.write_text(text + "\n사용자 필기")
        self.store.export(self.job["id"])
        self.assertTrue(first.read_text().endswith("사용자 필기"))

    def test_placeholder_and_unknown_asset(self):
        i = copy.deepcopy(self.job["items"][1])
        i["body"] = "원본 이미지 확인 필요"
        self.assertTrue(structural_errors(i))
        i["body"] = "![[assets/nonexistent.png]]"
        self.assertTrue(structural_errors(i))

    def test_pages_and_response_validation(self):
        self.assertEqual(page_range("1-2,4", 4), [1, 2, 4])
        for text in ("0", "3-2", "1-8", "a"):
            with self.assertRaises(ValueError):
                page_range(text, 4)
        for text in (" ", "[]", "not json"):
            with self.assertRaises(ValueError):
                parse_json(text)
        with self.assertRaises(ValueError):
            validate({"body": "x", "diagrams": [{"id": "fig1", "region": 0, "box": [0, 0, 1001, 100]}]}, "extract", 1)
        with self.assertRaises(ValueError):
            validate({"result": "no_difference", "issues": [{"location": "x", "original": "x", "extracted": "y", "message": "z", "suggestion": "x"}]}, "audit", 0)

    def test_symbol_error_survives_ai_false_negative(self):
        issues = symbol_differences("① ㉠\n② 자료를 ㉣에 제시하였다.", "① ㉠\n② 자료를 ㉡에 제시하였다.")
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]["suggestion"],"② 자료를 ㉣에 제시하였다.")
        self.assertEqual(symbol_differences("① ㉠", "① ㉠"),[])

    def test_audit_combines_symbol_check_with_model_result(self):
        record = self.job["items"][1]
        record["reference_text"] = "① 원문\n② 자료를 ㉣에 제시하였다."
        record["body"] = "① 원문\n② 자료를 ㉡에 제시하였다."
        self.store.save(self.job)
        result = self.audit(record)
        audit = self.store.item(result, record["id"])["audit"]
        self.assertEqual(audit["result"], "suspected_difference")
        self.assertEqual(len(audit["issues"]), 1)

    def test_review_note_saves_without_invalidating_audit(self):
        record = self.job["items"][1]
        self.audit(record)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"note": "직접 확인 근거"})
        saved = self.store.item(updated, record["id"])
        self.assertEqual(saved["note"], "직접 확인 근거")
        self.assertEqual(saved["revision"], record["revision"])
        self.assertEqual(saved["audit"]["revision"], record["revision"])

    def test_ai_usage_is_kept_with_audit(self):
        record = self.job["items"][1]
        response = lambda _: {"data": {"result": "no_difference", "issues": []},
                              "provider": "gemini", "configured_model": "gemini-test",
                              "usage": {"input_tokens": 120, "output_tokens": 30,
                                         "total_tokens": 150, "model": "gemini-test", "retry_count": 1}}
        result = process(self.store, self.job["id"], record["id"], record["revision"], "audit", caller=response)
        self.assertEqual(self.store.item(result, record["id"])["audit"]["usage"]["total_tokens"], 150)

    def test_audit_splits_many_images_and_aggregates_only_complete_batches(self):
        record = self.job["items"][1]
        job_dir = self.store.job_dir(self.job["id"])
        (job_dir / "regions").mkdir(parents=True, exist_ok=True)
        region_names = []
        for index in range(6):
            name = f"source-{index}.png"
            Image.new("RGB", (20, 20), "white").save(job_dir / "regions" / name)
            region_names.append({"image": name})
        asset_path = "assets/question/fig1.png"
        (job_dir / asset_path).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 20), "white").save(job_dir / asset_path)
        record.update(regions=region_names, solution_regions=[], assets=[{"path": asset_path}],
                      transcription_pending=[], body="문항 전사", solution="해설 전사")
        self.store.save(self.job)
        calls = []

        def caller(payload):
            calls.append(payload)
            return {"data": {"result": "no_difference", "issues": []},
                    "provider": "test", "configured_model": "fixture",
                    "usage": {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13,
                              "retry_count": 0, "attempts": 1, "model": "fixture"}}

        result = process(self.store, self.job["id"], record["id"], record["revision"], "audit", caller=caller)
        audit = self.store.item(result, record["id"])["audit"]
        self.assertEqual([len(call["images"]) for call in calls], [5, 2])
        self.assertIn("묶음 1/2", calls[0]["prompt"])
        self.assertEqual(audit["batch_count"], 2)
        self.assertEqual(audit["batches_completed"], 2)
        self.assertEqual(audit["result"], "no_difference")
        self.assertEqual(audit["usage"]["total_tokens"], 26)
        self.assertEqual(audit["usage"]["models"], ["fixture"])
        self.assertEqual(audit["providers"], ["test"])

        result = process(self.store, self.job["id"], record["id"], record["revision"], "audit",
                         caller=lambda _: {"data": {"result": "suspected_difference", "issues": []},
                                           "provider": "test", "configured_model": "fixture"})
        self.assertEqual(self.store.item(result, record["id"])["audit"]["result"], "suspected_difference")

    def test_audit_batch_failure_does_not_mark_partial_result_complete(self):
        record = self.job["items"][1]
        job_dir = self.store.job_dir(self.job["id"])
        (job_dir / "regions").mkdir(parents=True, exist_ok=True)
        for index in range(6):
            Image.new("RGB", (20, 20), "white").save(job_dir / "regions" / f"source-{index}.png")
        record.update(regions=[{"image": f"source-{index}.png"} for index in range(6)],
                      solution_regions=[], assets=[], transcription_pending=[], body="문항", solution="해설")
        self.store.save(self.job)
        calls = 0

        def caller(_payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fixture batch failure")
            return {"data": {"result": "no_difference", "issues": []},
                    "provider": "test", "configured_model": "fixture"}

        with self.assertRaisesRegex(RuntimeError, "fixture batch failure"):
            process(self.store, self.job["id"], record["id"], record["revision"], "audit", caller=caller)
        self.assertIsNone(self.store.item(self.store.get(self.job["id"]), record["id"]).get("audit"))

    def test_applied_suggestion_is_marked_and_requires_reaudit(self):
        record = self.job["items"][1]
        record["body"] = "원문 잘못된 표현"
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                            "issues": [{"location": "본문", "original": "원문 올바른 표현", "extracted": "잘못된 표현",
                                        "message": "차이", "suggestion": "올바른 표현"}]}
        self.store.save(self.job)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"body": "원문 올바른 표현"})
        saved = self.store.item(updated, record["id"])
        self.assertTrue(saved["audit"]["issues"][0]["applied"])
        self.assertNotEqual(saved["audit"]["revision"], saved["revision"])

    def test_revert_applied_audit_suggestion(self):
        record = self.job["items"][1]
        record["body"] = "원문 잘못된 표현"
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                            "issues": [{"location": "본문", "original": "원문 올바른 표현", "extracted": "잘못된 표현",
                                        "message": "차이", "suggestion": "올바른 표현"}]}
        self.store.save(self.job)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"body": "원문 올바른 표현"})
        saved = self.store.item(updated, record["id"])
        self.assertTrue(saved["audit"]["issues"][0]["applied"])
        reverted = self.store.revert_audit_suggestion(self.job["id"], record["id"], saved["revision"], 0)
        final = self.store.item(reverted, record["id"])
        self.assertEqual(final["body"], "원문 잘못된 표현")
        self.assertFalse(final["audit"]["issues"][0].get("applied"))

    def test_skip_and_unskip_audit_issue(self):
        record = self.job["items"][1]
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                           "issues": [{"location": "본문", "extracted": "오자", "suggestion": "정자"}]}
        self.store.save(self.job)
        skipped = self.store.skip_audit_issue(
            self.job["id"], record["id"], record["revision"], 0,
            note="원본 문구가 맞으므로 AI 교정을 적용하지 않음",
        )
        skipped_item = self.store.item(skipped, record["id"])
        self.assertTrue(skipped_item["audit"]["issues"][0]["skipped"])
        self.assertEqual(skipped_item["audit"]["issues"][0]["skip_note"],
                         "원본 문구가 맞으므로 AI 교정을 적용하지 않음")
        decisions = json.loads(self.store.audit_skip_decisions_path().read_text(encoding="utf-8"))["decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0]["active"])
        self.assertEqual(decisions[0]["note"], "원본 문구가 맞으므로 AI 교정을 적용하지 않음")
        self.assertIsNone(audit_approval_gate(skipped_item))
        restored = self.store.skip_audit_issue(self.job["id"], record["id"], record["revision"], 0, False)
        restored_item = self.store.item(restored, record["id"])
        self.assertNotIn("skipped", restored_item["audit"]["issues"][0])
        decisions = json.loads(self.store.audit_skip_decisions_path().read_text(encoding="utf-8"))["decisions"]
        self.assertFalse(decisions[0]["active"])
        self.assertIn("unskipped_at", decisions[0])
        self.assertIn("판단 근거", audit_approval_gate(restored_item))

    def test_applied_audit_issue_cannot_be_skipped(self):
        record = self.job["items"][1]
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                           "issues": [{"extracted": "오자", "suggestion": "정자", "applied": True}]}
        self.store.save(self.job)
        with self.assertRaisesRegex(ValueError, "되돌린 뒤 스킵"):
            self.store.skip_audit_issue(self.job["id"], record["id"], record["revision"], 0)

    def test_solution_suggestion_marks_only_changed_target(self):
        record = self.job["items"][1]
        record["solution"] = "해설의 잘못된 표현"
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                           "issues": [{"extracted": "잘못된 표현", "suggestion": "올바른 표현"}]}
        self.store.save(self.job)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"solution": "해설의 올바른 표현"})
        saved = self.store.item(updated, record["id"])
        self.assertTrue(saved["audit"]["issues"][0]["applied"])
        self.assertEqual(saved["body"], record["body"])
        self.assertNotEqual(saved["audit"]["revision"], saved["revision"])

    def test_unrelated_edit_does_not_mark_suggestion_applied(self):
        record = self.job["items"][1]
        record["body"] = "올바른 표현"
        record["solution"] = "해설의 잘못된 표현"
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                           "issues": [{"extracted": "잘못된 표현", "suggestion": "올바른 표현"}]}
        self.store.save(self.job)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"body": "이미 있는 올바른 표현"})
        self.assertFalse(self.store.item(updated, record["id"])["audit"]["issues"][0].get("applied"))

    def test_ambiguous_suggestion_is_not_marked_applied(self):
        record = self.job["items"][1]
        record["body"] = record["solution"] = "잘못된 표현"
        record["audit"] = {"revision": record["revision"], "status": "completed", "result": "suspected_difference",
                           "issues": [{"extracted": "잘못된 표현", "suggestion": "올바른 표현"}]}
        self.store.save(self.job)
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"body": "올바른 표현"})
        self.assertFalse(self.store.item(updated, record["id"])["audit"]["issues"][0].get("applied"))

    def test_body_edit_rechecks_geometry_styles_and_boxes(self):
        record = self.job["items"][1]
        record["source_styles"] = [
            {"type": "underline", "text": "기대할 수 있는 효과"},
            {"type": "box", "bbox": [0, 0, 100, 100], "box_detection": "likely",
             "text_preview": "친구들의 의견 문단과 문단이 자연스럽게 연결"},
        ]
        record["style_mapping_errors"] = [{"type": "underline", "text": "기대할 수 있는 효과"}]
        record["box_checks"] = [{"status": "missing"}]
        self.store.save(self.job)

        body = "> [!quote] 보기\n> 친구들의 의견: 문단과 문단이 자연스럽게 연결\n\n기대할 수 있는 효과"
        updated = self.store.update(self.job["id"], record["id"], record["revision"], {"body": body})
        saved = self.store.item(updated, record["id"])
        self.assertIn("<u>기대할 수 있는 효과</u>", saved["body"])
        self.assertEqual(saved["style_mapping_errors"], [])
        self.assertEqual(saved["box_checks"][0]["status"], "preserved")

    def test_math_quote_conditions_export_as_separate_lines(self):
        source = "> [!quote]\n> (가) $n(A) \\le 3$ (나) $n(A)=n(B)$ (다) $f(x) \\ne x$"
        rendered = format_output_markdown(source, "수학")
        self.assertEqual(rendered, "> [!quote]\n> (가) $n(A) \\le 3$\n> (나) $n(A)=n(B)$\n> (다) $f(x) \\ne x$")

    def test_math_text_circled_markers_do_not_render_as_raw_latex(self):
        source = (
            "\\textcircled{A}에서 확인하고 $\\textcircled{B}$는 수식으로 둔다.\n\n"
            "`\\textcircled{C}`와 ```text\n\\textcircled{1}\n```도 보존한다."
        )
        rendered = format_output_markdown(source, "수학")
        self.assertIn("Ⓐ에서 확인", rendered)
        self.assertIn("$\\textcircled{B}$", rendered)
        self.assertIn("`\\textcircled{C}`", rendered)
        self.assertIn("```text\n\\textcircled{1}\n```", rendered)

    def test_manual_asset_crop_rewrites_png_and_invalidates_review(self):
        item = self.job["items"][0]
        region = {"id": "r1", "document": "problem", "page": 1, "bbox": [0, 0, 100, 50],
                  "width": 100, "height": 50, "image": "r1.png"}
        item["regions"] = [region]
        source = self.store.job_dir(self.job["id"]) / "regions" / "r1.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (100, 50), "blue")
        for x in range(50):
            for y in range(50):
                image.putpixel((x, y), (255, 0, 0))
        image.save(source)
        old_path = f"assets/{item['id']}/{item['revision']}/fig1.png"
        target = self.store.job_dir(self.job["id"]) / old_path
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        item["assets"] = [{
            "id": "fig1", "path": old_path, "section": "body",
            "structured": {"title": "그림"},
            "crop": {"proposed_box": [0, 0, 1000, 1000], "source_region": [0, 0, 100, 50],
                     "document": "problem", "page": 1},
        }]
        item["body"] += f"\n![[{old_path}]]\n"
        item["review"] = "approved"
        old_revision = item["revision"]
        self.store.save(self.job)
        job = self.store.crop_source(self.job["id"], item["id"], old_revision, "asset:fig1", [0, 0, 1000, 500])
        item = self.store.item(job, item["id"])
        self.assertNotEqual(item["revision"], old_revision)
        self.assertEqual(item["review"], "pending")
        self.assertEqual(item["assets"][0]["crop"]["proposed_box"], [0, 0, 1000, 500])
        self.assertNotIn(old_path, item["body"])
        self.assertIn(item["assets"][0]["path"], item["body"])
        cropped = Image.open(self.store.job_dir(self.job["id"]) / item["assets"][0]["path"])
        self.addCleanup(cropped.close)
        self.assertEqual(cropped.size, (50, 50))
        self.assertEqual(cropped.getpixel((10, 10)), (255, 0, 0))

    def test_crop_selects_solution_fig1_when_body_fig1_also_exists(self):
        from PIL import Image
        item = self.job["items"][0]
        region = {"id": "r1", "document": "problem", "page": 1, "bbox": [0, 0, 100, 50],
                  "width": 100, "height": 50, "image": "r1.png"}
        item["regions"] = [region]
        item["solution_regions"] = [dict(region)]
        image = Image.new("RGB", (100, 50), "blue")
        region_path = self.store.job_dir(self.job["id"]) / "regions" / "r1.png"
        region_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(region_path)
        revision = item["revision"]
        body_path = f"assets/{item['id']}/{revision}/body/fig1.png"
        solution_path = f"assets/{item['id']}/{revision}/solution/fig1.png"
        for path in (body_path, solution_path):
            destination = self.store.job_dir(self.job["id"]) / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination)
        item["assets"] = [
            {"id": "fig1", "path": body_path, "section": "body", "crop": {"source_region": region["bbox"]}},
            {"id": "fig1", "path": solution_path, "section": "solution", "crop": {"source_region": region["bbox"]}},
        ]
        item["body"] = f"![[{body_path}]]"
        item["solution"] = f"![[{solution_path}]]"
        self.store.save(self.job)
        with self.assertRaisesRegex(ValueError, "같은 이름"):
            self.store.crop_source(self.job["id"], item["id"], revision, "asset:fig1", [0, 0, 1000, 500])
        job = self.store.crop_source(self.job["id"], item["id"], revision, "asset-index:1", [0, 0, 1000, 500])
        updated = self.store.item(job, item["id"])
        self.assertEqual(updated["assets"][0]["path"], body_path)
        self.assertEqual(updated["body"], f"![[{body_path}]]")
        self.assertNotEqual(updated["assets"][1]["path"], solution_path)
        self.assertIn(updated["assets"][1]["path"], updated["solution"])
        self.assertIn("solution-fig1.png", updated["assets"][1]["path"])

    def test_reapply_diagram_refine_shrinks_mixed_figure_box(self):
        from PIL import ImageDraw
        item = self.job["items"][0]
        region = {"id": "r1", "document": "problem", "page": 1, "bbox": [0, 0, 400, 500],
                  "width": 400, "height": 500, "image": "r1.png"}
        item["regions"] = [region]
        source = self.store.job_dir(self.job["id"]) / "regions" / "r1.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (400, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 30, 180, 120), outline="black", width=3)
        for y in (280, 310, 340):
            draw.rectangle((30, y, 370, y + 14), fill="black")
        image.save(source)
        old_path = f"assets/{item['id']}/{item['revision']}/fig1.png"
        target = self.store.job_dir(self.job["id"]) / old_path
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        vision = [50, 30, 900, 900]
        item["assets"] = [{
            "id": "fig1", "path": old_path, "section": "body", "structured": {"title": "그림"},
            "crop": {"proposed_box": vision, "vision_proposed_box": vision,
                     "source_region": [0, 0, 400, 500], "document": "problem", "page": 1},
        }]
        item["body"] = f"<!-- MATERIAL:fig1 START -->\n![[{old_path}]]\n<!-- MATERIAL:fig1 END -->"
        item["review"] = "approved"
        old_revision = item["revision"]
        self.store.save(self.job)
        preview = self.store.reapply_diagram_refine(self.job["id"], item["id"], "fig1", dry_run=True)
        self.assertEqual(preview["status"], "would_update")
        self.assertLess(preview["refined_proposed_box"][2], vision[2])
        record = self.store.reapply_diagram_refine(self.job["id"], item["id"], "fig1")
        self.assertEqual(record["status"], "updated")
        job = self.store.get(self.job["id"])
        item = self.store.item(job, item["id"])
        self.assertEqual(self.store.reapply_diagram_refine(self.job["id"], item["id"], "fig1")["status"], "unchanged")
        self.assertNotEqual(item["revision"], old_revision)
        self.assertEqual(item["review"], "pending")
        self.assertLess(item["assets"][0]["crop"]["proposed_box"][2], vision[2])


if __name__ == "__main__":
    unittest.main()

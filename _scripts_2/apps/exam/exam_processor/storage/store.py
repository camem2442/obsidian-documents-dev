"""Atomic job snapshots and optimistic revisions; no edits to imported sources."""
import copy
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from _scripts_2.apps.exam.exam_core.domain.canonical import canonical_relation_errors
from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical as legacy_item_to_canonical
from ..domain.job_manifest import JobManifest
from ..domain.models import (
    identity,
    revision,
    digest,
    invalidate,
    structural_errors,
    markdown_note,
    audit_approval_gate,
    normalize_audit_waiver_reason,
)
from ..domain.review_state import ReviewState
from ..ingestion.formats import FormatRequest, resolve_format
from .boundary import attach_relation_errors, export_boundary_errors
from .ephemeral_views import (
    attach_display_titles,
    attach_workbook_views,
    build_legacy_job_view,
    attach_legacy_review_projection,
)
from .job_repository import JobRepository
from .job_runtime_repository import JobRuntimeRepository
from .record_repository import RecordRepository
from .review_repository import ReviewRepository
from .revision_repository import RevisionRepository


class Conflict(ValueError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, root=None, output=None):
        self.root = Path(root or config.DATA_ROOT)
        self.output = Path(output or config.OUTPUT_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.records = RecordRepository()
        self.reviews = ReviewRepository()
        self.revisions = RevisionRepository()
        self.jobs = JobRepository()
        self.runtime = JobRuntimeRepository()
        # Canonical-native mutation services (P1-2)
        from ..domain.services import RecordService, ReviewService
        self._record_svc = RecordService(self.records, self.reviews, self.revisions)
        self._review_svc = ReviewService(self.records, self.reviews)

    def job_dir(self, job_id):
        if re.fullmatch(r"study-[a-f0-9]{32}", job_id):
            return self.root / "ingestion" / "jobs" / job_id
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("잘못된 작업 ID입니다.")
        return self.root / "jobs" / job_id

    def get(self, job_id):
        with self.lock:
            job_dir = self.job_dir(job_id)
            manifest_path = job_dir / "job.json"
            if not manifest_path.is_file():
                raise ValueError("작업을 찾지 못했습니다.")
            raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if raw_data.get("storage_version", 1) < 2 or "items" in raw_data:
                job = copy.deepcopy(raw_data)
                attach_legacy_review_projection(job)
                attach_workbook_views(job)
                attach_display_titles(job)
                return job

            manifest = JobManifest.from_dict(raw_data)
            record_ids = manifest.record_ids
            records = self.records.get_many(job_dir, record_ids)
            review_states = self.reviews.get_many(job_dir, record_ids)
            runtime_state = self.runtime.load(job_dir, job_id)
            # Migration check: if runtime.json didn't exist yet but job.json had runtime keys
            if (not (job_dir / "runtime.json").is_file() and
                    any(k in raw_data for k in ("task", "queue", "queue_history", "ai_log", "ai_progress"))):
                for k in ("task", "queue", "queue_history", "ai_log", "ai_progress"):
                    if k in raw_data:
                        setattr(runtime_state, k, raw_data[k])
                # Read compatibility is ephemeral; only explicit writes persist runtime.

            job = build_legacy_job_view(manifest, records, review_states, runtime_state)
            return job

    # Tests and legacy call sites
    _attach_workbook_views = staticmethod(attach_workbook_views)
    _attach_display_titles = staticmethod(attach_display_titles)

    def save(self, job):
        attach_relation_errors(job)
        with self.lock:
            job_dir = self.job_dir(job["id"])
            job_dir.mkdir(parents=True, exist_ok=True)
            manifest_dict = copy.deepcopy(job)
            items = manifest_dict.pop("items", [])
            manifest_dict.pop("review_queue_summary", None)
            
            # Save runtime state if present in job dict
            runtime_fields = {k: manifest_dict.pop(k) for k in ("task", "queue", "queue_history", "ai_log", "ai_progress") if k in manifest_dict}
            if runtime_fields:
                current_runtime = self.runtime.load(job_dir, job["id"])
                for k, v in runtime_fields.items():
                    setattr(current_runtime, k, v)
                self.runtime.save(job_dir, current_runtime)
            record_ids = []
            for item in items:
                item.pop("standard_preview", None)
                item.pop("workbook_view", None)
                item.pop("display_title", None)
                item.pop("review_decision", None)
                item.pop("approval_gate", None)
                item_id = item["id"]
                record_ids.append(item_id)

                record = legacy_item_to_canonical(manifest_dict, item)
                self.records.save(job_dir, record)

                rev_id = item.get("revision") or record.provenance.revision_id
                review_state = ReviewState(
                    record_id=item_id,
                    applies_to_revision_id=rev_id,
                    published_revision_id=item.get("published_revision", ""),
                    note=item.get("note", ""),
                    audit=item.get("audit"),
                    audit_waiver=item.get("audit_waiver"),
                    warnings=list(item.get("warnings", [])),
                    quality_checks=list(item.get("quality_checks", [])),
                    transcription_pending=list(item.get("transcription_pending", [])),
                    source_styles=list(item.get("source_styles", [])),
                    style_mapping_errors=list(item.get("style_mapping_errors", [])),
                    group_mapping_errors=list(item.get("group_mapping_errors", [])),
                    box_checks=list(item.get("box_checks", [])),
                    solution_candidates=list(item.get("solution_candidates", [])),
                    selected_solution_id=item.get("selected_solution_id", ""),
                    solution_match=item.get("solution_match"),
                    solution_link_backup=item.get("solution_link_backup"),
                    ai_runs=list(item.get("ai_runs", [])),
                    material_validation_errors=list(item.get("material_validation_errors", [])),
                    history=list(item.get("history", [])),
                    extracted_revision=item.get("extracted_revision", ""),
                    reference_text=item.get("reference_text", ""),
                    assets=list(item.get("assets", [])),
                )
                self.reviews.save(job_dir, review_state)
                self.revisions.save_snapshot(job_dir, item_id, rev_id, record, review_state)

            manifest_dict["storage_version"] = 2
            manifest_dict["record_ids"] = record_ids
            manifest = JobManifest.from_dict(manifest_dict)
            self.jobs.save_manifest(job_dir, manifest)
            attach_workbook_views(job)
            attach_display_titles(job)

    def human_findings_path(self):
        return self.root / "learning" / "human-findings.json"

    def audit_skip_decisions_path(self):
        return self.root / "learning" / "audit-skip-decisions.json"

    def _record_audit_skip_decision(self, job, item, issue_index, issue, active):
        """Keep audit decisions separate from reusable extraction rules."""
        path = self.audit_skip_decisions_path()
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "schema_version": 1, "decisions": []
        }
        decision_id = identity(
            "audit-skip", job["id"], item["id"], item["revision"], issue_index,
            issue.get("location", ""), issue.get("extracted", ""), issue.get("suggestion", ""),
        )
        decision = next((entry for entry in data["decisions"]
                         if entry.get("decision_id") == decision_id), None)
        now = datetime.now(timezone.utc).isoformat()
        values = {
            "decision_id": decision_id, "updated_at": now, "active": bool(active),
            "job_id": job["id"], "item_id": item["id"], "revision": item["revision"],
            "issue_index": issue_index, "format_id": job.get("format", ""),
            "track": job.get("track", ""), "item_number": item.get("number", ""),
            "item_kind": item.get("kind", ""), "location": issue.get("location", ""),
            "category": issue.get("category", ""), "original": issue.get("original", ""),
            "extracted": issue.get("extracted", ""), "suggestion": issue.get("suggestion", ""),
            "message": issue.get("message", ""), "note": issue.get("skip_note", ""),
        }
        if decision is None:
            decision = {"created_at": now, **values}
            data["decisions"].append(decision)
        else:
            decision.update(values)
        if active:
            decision.pop("unskipped_at", None)
        else:
            decision["unskipped_at"] = now
        atomic_json(path, data)

    def human_findings(self, format_id, section, track="", limit=6):
        path = self.human_findings_path()
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        findings = [copy.deepcopy(finding) for finding in data.get("findings", [])
                    if finding.get("active", True)
                    and finding.get("format_id") == format_id
                    and finding.get("track", "") == track
                    and finding.get("section") in (section, "both")]
        groups = {}
        for finding in findings:
            source = self._rule_key(finding.get("source_excerpt", ""))
            correction = self._rule_key(finding.get("correction", ""))
            finding["rule_status"] = "unique"
            if not source or not correction:
                continue
            key = (finding.get("section"), finding.get("category"), source)
            groups.setdefault(key, []).append(finding)
        for group in groups.values():
            if len(group) < 2:
                continue
            corrections = {self._rule_key(entry.get("correction", "")) for entry in group}
            status = "conflict" if len(corrections) > 1 else "duplicate"
            canonical = group[0]["finding_id"]
            resolution = next((entry.get("conflict_resolution") for entry in group
                               if entry.get("conflict_resolution")), None)
            selected_id = resolution.get("selected_finding_id") if resolution else None
            member_ids = {entry["finding_id"] for entry in group}
            resolution_valid = (status == "conflict" and selected_id in member_ids
                                and set(resolution.get("member_finding_ids", [])) == member_ids)
            for entry in group:
                entry["rule_status"] = ("resolved_selected" if entry["finding_id"] == selected_id
                                        else "resolved_excluded") if resolution_valid else status
                if resolution_valid:
                    entry["conflict_resolution"] = resolution
                if entry["finding_id"] != canonical:
                    entry["related_finding_id"] = canonical
        return findings[-limit:] if limit else findings

    def human_finding_prompt_data(self, format_id, section, track=""):
        findings = self.human_findings(format_id, section, track, limit=None)
        conflicted = [finding for finding in findings if finding.get("rule_status") == "conflict"]
        resolved = [finding for finding in findings if finding.get("rule_status") == "resolved_selected"]
        safe, seen, deduplicated = [], set(), []
        for finding in findings:
            if finding.get("rule_status") in ("conflict", "resolved_excluded"):
                continue
            key = (finding.get("section"), finding.get("category"),
                   self._rule_key(finding.get("source_excerpt", "")),
                   self._rule_key(finding.get("correction", "")))
            if not key[2] or not key[3]:
                key = (finding["finding_id"],)
            if key in seen:
                deduplicated.append(finding["finding_id"])
                continue
            seen.add(key)
            safe.append(finding)
        included = safe[-6:]
        excluded_conflicts = [finding["finding_id"] for finding in findings
                              if finding.get("rule_status") == "conflict"]
        excluded_resolved = [finding["finding_id"] for finding in findings
                             if finding.get("rule_status") == "resolved_excluded"]
        excluded_limit = [finding["finding_id"] for finding in safe[:-6]]
        return {"findings": included, "active_count": len(findings),
                "conflict_count": len(conflicted),
                "resolved_count": len(resolved),
                "duplicate_count": sum(1 for finding in findings if finding.get("rule_status") == "duplicate"),
                "included_finding_ids": [finding["finding_id"] for finding in included],
                "excluded_conflict_finding_ids": excluded_conflicts,
                "excluded_resolved_finding_ids": excluded_resolved,
                "deduplicated_finding_ids": deduplicated,
                "excluded_limit_finding_ids": excluded_limit}

    @staticmethod
    def _rule_key(value):
        return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

    def resolve_human_finding_conflict(self, finding_id, rationale):
        rationale = str(rationale or "").strip()
        if not 10 <= len(rationale) <= 500:
            raise ValueError("충돌 해소 근거를 10~500자로 입력하세요.")
        with self.lock:
            path = self.human_findings_path()
            if not path.is_file():
                raise ValueError("누적 규칙을 찾지 못했습니다.")
            data = json.loads(path.read_text(encoding="utf-8"))
            findings = data.get("findings", [])
            selected = next((finding for finding in findings
                             if finding.get("finding_id") == finding_id and finding.get("active", True)), None)
            if selected is None:
                raise ValueError("활성 상태의 규칙을 찾지 못했습니다.")
            source = self._rule_key(selected.get("source_excerpt", ""))
            if not source:
                raise ValueError("원문 예시가 없는 규칙은 충돌 해소에 사용할 수 없습니다.")
            group = [finding for finding in findings if finding.get("active", True)
                     and finding.get("format_id") == selected.get("format_id")
                     and finding.get("track", "") == selected.get("track", "")
                     and finding.get("section") == selected.get("section")
                     and finding.get("category") == selected.get("category")
                     and self._rule_key(finding.get("source_excerpt", "")) == source]
            if len({self._rule_key(finding.get("correction", "")) for finding in group}) < 2:
                raise ValueError("현재 규칙 그룹에 해소할 충돌이 없습니다.")
            resolution = {"selected_finding_id": finding_id,
                          "member_finding_ids": [finding["finding_id"] for finding in group],
                          "rationale": rationale,
                          "resolved_at": datetime.now(timezone.utc).isoformat()}
            for finding in group:
                finding["conflict_resolution"] = resolution
            atomic_json(path, data)
            return resolution

    def add_human_finding(self, job_id, item_id, expected, values):
        categories = {"transcription", "omission", "math", "layout", "asset", "structure", "other"}
        section = values.get("section")
        category = values.get("category")
        lesson = str(values.get("lesson", "")).strip()
        if section not in ("body", "solution") or category not in categories:
            raise ValueError("판정 영역 또는 유형을 확인하세요.")
        if not 10 <= len(lesson) <= 1000:
            raise ValueError("재사용 지침은 10~1000자로 입력하세요.")
        excerpts = {key: str(values.get(key, "")).strip()
                    for key in ("source_excerpt", "extracted_excerpt", "correction")}
        if any(len(value) > 500 for value in excerpts.values()):
            raise ValueError("예시 문구는 각각 500자 이내로 입력하세요.")
        with self.lock:
            job = self.get(job_id)
            item = self.item(job, item_id)
            if item["revision"] != expected:
                raise Conflict("문항 버전이 바뀌었습니다. 새로고침 후 판정을 기록하세요.")
            finding = {
                "finding_id": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "job_id": job_id, "item_id": item_id, "revision": expected,
                "format_id": job.get("format", ""), "track": job.get("track", ""),
                "section": section, "category": category, "active": True,
                **excerpts, "lesson": lesson,
            }
            path = self.human_findings_path()
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1, "findings": []}
            data.setdefault("findings", []).append(finding)
            atomic_json(path, data)
            return finding

    def approve_quality_check(self, job_id, item_id, expected, index):
        """Promote one reviewed automatic repair into the reusable rule log."""
        with self.lock:
            job = self.get(job_id)
            item = self.item(job, item_id)
            if item["revision"] != expected:
                raise Conflict("문항 버전이 바뀌었습니다. 새로고침 후 승인하세요.")
            checks = item.get("quality_checks", [])
            if not isinstance(index, int) or not 0 <= index < len(checks):
                raise ValueError("품질 검사 항목을 찾지 못했습니다.")
            check = checks[index]
            if check.get("promoted_finding_id"):
                return check
            source, correction = str(check.get("source", "")).strip(), str(check.get("normalized", "")).strip()
            finding = self.add_human_finding(job_id, item_id, expected, {
                "section": check.get("section", "body"), "category": "math",
                "source_excerpt": source, "extracted_excerpt": source,
                "correction": correction,
                "lesson": f"수식 전사에서 `{source}`처럼 명령어가 누락된 경우 `{correction}`처럼 TeX 명령어를 복원한다.",
            })
            job_dir = self.job_dir(job_id)
            return self._review_svc.approve_quality_check(job_dir, item_id, expected, index, finding["finding_id"])


    def deactivate_human_finding(self, finding_id):
        if not re.fullmatch(r"[a-f0-9]{32}", finding_id):
            raise ValueError("잘못된 판정 ID입니다.")
        with self.lock:
            path = self.human_findings_path()
            if not path.is_file():
                raise ValueError("누적 판정을 찾지 못했습니다.")
            data = json.loads(path.read_text(encoding="utf-8"))
            finding = next((entry for entry in data.get("findings", [])
                            if entry.get("finding_id") == finding_id), None)
            if finding is None:
                raise ValueError("누적 판정을 찾지 못했습니다.")
            finding["active"] = False
            finding["deactivated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json(path, data)
            return finding

    def list(self):
        jobs = []
        paths = list((self.root / "jobs").glob("*/job.json")) + list((self.root / "ingestion" / "jobs").glob("*/job.json"))
        for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
            job = self.get(path.parent.name)
            items = job.get("items", [])
            approved = sum(1 for i in items if i.get("review") == "approved")
            held = sum(1 for i in items if i.get("review") == "held")
            issues = sum(1 for i in items if (i.get("audit") or {}).get("result") == "suspected_difference")
            mtime = path.stat().st_mtime
            jobs.append({
                "id": job["id"],
                "source": job.get("source", ""),
                "track": job.get("track", ""),
                "format": job.get("format", ""),
                "warnings": job.get("warnings", []),
                "count": len(items),
                "approved_count": approved,
                "held_count": held,
                "issue_count": issues,
                "updated_at": mtime,
            })
        return jobs


    def import_file(self, path, source, track, pages="", solution="", *, format_id="", structure="", layout="", script="", answer="", creation_receipt=None):
        if (track not in ("매체", "화법과 작문")
                and not (track == "확률과 통계" and format_id == "hanwangi_2026_probability")
                and not (track == "영어" and format_id == "kice_english")):
            raise ValueError("지원하지 않는 과목 또는 입력 형식입니다.")
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in (".pdf", ".md"):
            raise ValueError("PDF 또는 Markdown 파일을 선택하세요.")
        source = source.strip()
        if not source or len(source) > 180:
            raise ValueError("출처명을 입력하세요. (180자 이내)")
        solution_path = Path(solution).expanduser().resolve() if solution else None
        if solution_path and (not solution_path.is_file() or solution_path.suffix.lower() not in (".md", ".pdf")):
            raise ValueError("해설 파일을 확인하세요.")
        if (script or answer) and format_id != "kice_english":
            raise ValueError("대본·정답 이미지 연결은 현재 평가원 영어 형식만 지원합니다.")
        script_path = Path(script).expanduser().resolve() if script else None
        answer_path = Path(answer).expanduser().resolve() if answer else None
        if script_path and (not script_path.is_file() or script_path.suffix.lower() != ".pdf"):
            raise ValueError("듣기 대본 PDF를 확인하세요.")
        if answer_path and (not answer_path.is_file() or answer_path.suffix.lower() not in (".png", ".pdf")):
            raise ValueError("정답 이미지/PDF를 확인하세요.")
        format_module = resolve_format(path, format_id)
        structure_data, layout_data = None, None
        if format_id == "hanwangi_2026_probability":
            from ..pipeline.workbook import read_structure
            if not structure or not layout:
                raise ValueError("교재 구조 기록과 확인된 영역 기록 경로를 지정하세요.")
            structure_data = read_structure(structure)
            with open(layout, encoding="utf-8") as stream:
                layout_data = json.load(stream)
        elif structure or layout:
            raise ValueError("이 형식은 교재 구조·영역 기록을 사용하지 않습니다.")
        source_id = (identity("source", source, track, digest(path))
                     if format_id == "kice_english" else identity("source", source, track))
        parser_version = format_module.parser_version
        job_id = identity(source_id, digest(path), digest(solution_path) if solution_path else "",
                          pages, parser_version)
        if format_id == "kice_english":
            job_id = identity(job_id, format_id, digest(script_path) if script_path else "",
                              digest(answer_path) if answer_path else "")
        if structure_data is not None:
            job_id = identity(job_id, format_id, structure_data, layout_data)
        if creation_receipt is not None:
            job_id = creation_receipt["job_id"]
        with self.lock:
            if (self.job_dir(job_id) / "job.json").exists():
                return self.get(job_id)
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        regions_dir = directory / "regions"
        regions_dir.mkdir(exist_ok=True)
        original = directory / ("original" + path.suffix.lower())
        shutil.copy2(path, original)
        documents = {"problem": original.name}
        if solution_path:
            saved_solution = directory / ("solution" + solution_path.suffix.lower())
            shutil.copy2(solution_path, saved_solution)
            documents["solution"] = saved_solution.name
        else:
            saved_solution = None
        saved_script = None
        if script_path:
            saved_script = directory / "script.pdf"
            shutil.copy2(script_path, saved_script)
            documents["script"] = saved_script.name
        saved_answer = None
        if answer_path:
            saved_answer = directory / ("answer" + answer_path.suffix.lower())
            shutil.copy2(answer_path, saved_answer)
            documents["answer"] = saved_answer.name
        result = format_module.parse(FormatRequest(
            problem_path=original,
            solution_path=saved_solution,
            source_id=source_id,
            track=track,
            pages=pages,
            workdir=regions_dir,
            source=source,
            structure=structure_data,
            layout=layout_data,
            script_path=saved_script,
            answer_path=saved_answer,
        ))
        format_id = format_module.format_id
        if not hasattr(result, "records") or not result.records:
            raise ValueError(f"Parser '{format_id}' must return an IngestBatch with non-empty records.")

        records = result.records
        review_states = result.review_states
        warnings = result.warnings
        meta = dict(result.metadata)
        if structure_data is not None:
            meta.update(subject=result.metadata.get("subject", ""),
                        workbook=result.metadata.get("workbook", ""),
                        layout=result.metadata.get("layout", ""))
        elif format_id == "kice_english":
            meta.update(subject="영어", source_type="exam",
                        source_set_id=result.metadata.get("source_set_id", ""),
                        exam_code=result.metadata.get("exam_code", ""),
                        exam_form=result.metadata.get("exam_form", ""),
                        input_documents={role: str(candidate) for role, candidate in {
                            "problem": path, "solution": solution_path,
                            "script": script_path, "answer": answer_path,
                        }.items() if candidate},
                        source_hashes=[digest(candidate) for candidate in
                                       (path, solution_path, script_path, answer_path) if candidate])
        if creation_receipt is not None:
            meta["input_creation"] = copy.deepcopy(creation_receipt)
        meta["input_path"] = str(path)
        subject = meta.pop("subject", "") or (records[0].question.subject if records else "")
        source_type = meta.pop("source_type", "") or (records[0].source.type if records else "")
        solution_format = meta.pop("solution_format", "")
        manifest = JobManifest(
            id=job_id,
            source_id=source_id,
            source=source,
            track=track,
            format=format_id,
            record_ids=[r.question.question_id for r in records],
            documents=documents,
            warnings=warnings,
            subject=subject,
            source_type=source_type,
            solution_format=solution_format,
            metadata=meta,
        )
        with self.lock:
            if (directory / "job.json").exists():
                return self.get(job_id)
            self.records.save_many(directory, records)
            self.reviews.save_many(directory, review_states)
            self.jobs.save_manifest(directory, manifest)
            return self.get(job_id)

    def item(self, job, item_id):
        for item in job["items"]:
            if item["id"] == item_id:
                return item
        raise ValueError("문항을 찾지 못했습니다.")

    def update(self, job_id, item_id, expected, values):
        allowed = {"body", "solution", "answer", "points", "correct_rate", "unit", "q_type", "regions", "solution_regions", "note", "number",
                   "modality", "question_set_id", "listening_script_id", "strategy_tags", "content_type"}
        with self.lock:
            job_dir = self.job_dir(job_id)
            manifest = self.jobs.load_manifest(job_dir)
            records = self.records.get_many(job_dir, manifest.record_ids)
            reviews = self.reviews.get_many(job_dir, manifest.record_ids)

            target_record = records.get(item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            if target_record.provenance.revision_id != expected:
                raise Conflict("다른 수정본이 있습니다. 새로고침 후 확인하세요.")
            if any(k not in allowed for k in values):
                raise ValueError("지원하지 않는 수정 항목입니다.")

            target_review = reviews.get(item_id) or ReviewState(
                record_id=item_id,
                applies_to_revision_id=target_record.provenance.revision_id,
            )
            reviews[item_id] = target_review

            # Build ephemeral view for validation
            from .ephemeral_views import build_legacy_item_view
            candidate = build_legacy_item_view(target_record, target_review, manifest)
            candidate.update(values)

            for key in ("body", "solution", "answer", "unit", "q_type", "note", "number",
                        "modality", "question_set_id", "listening_script_id", "content_type"):
                if key in candidate and not isinstance(candidate[key], str):
                    raise ValueError("본문과 메타데이터는 문자열이어야 합니다.")
            if candidate.get("modality", "") not in ("", "listening", "reading"):
                raise ValueError("영어 문항 구분이 올바르지 않습니다.")
            if candidate.get("content_type", "") not in ("", "past_exam", "original", "variant"):
                raise ValueError("문항 성격이 올바르지 않습니다.")
            if (not isinstance(candidate.get("strategy_tags", []), list)
                    or any(not isinstance(tag, str) for tag in candidate.get("strategy_tags", []))):
                raise ValueError("풀이 전략 태그는 문자열 목록이어야 합니다.")
            if candidate.get("listening_script_id"):
                script_record = records.get(candidate["listening_script_id"])
                if not script_record or script_record.question.content_kind != "script":
                    raise ValueError("연결된 듣기 대본을 찾을 수 없습니다.")
            if "number" in values:
                from ..domain.models import relabel_number
                relabel_number(candidate, candidate["number"])
            if candidate.get("correct_rate") is not None and (
                    isinstance(candidate["correct_rate"], bool)
                    or not isinstance(candidate["correct_rate"], (int, float))
                    or not 0 <= candidate["correct_rate"] <= 1):
                raise ValueError("정답률은 0 이상 1 이하의 비율로 입력하세요.")

            old_body_regions = [r for r in target_record.provenance.source_regions if r.get("content_role") == "body"]
            old_sol_regions = [r for r in target_record.provenance.source_regions if r.get("content_role") == "solution"]
            old_region_map = {"regions": old_body_regions, "solution_regions": old_sol_regions}
            for key in ("regions", "solution_regions"):
                if key in values:
                    old_list = old_region_map[key]
                    if len(values[key]) != len(old_list):
                        raise ValueError("초기 버전은 기존 영역의 경계 수정만 지원합니다.")
                    for old, new in zip(old_list, values[key]):
                        if {k: v for k, v in old.items() if k != "bbox"} != {k: v for k, v in new.items() if k != "bbox"}:
                            raise ValueError("영역의 파일 참조를 변경할 수 없습니다.")

            errors = structural_errors(candidate)
            if any("좌표" in e or "배점" in e for e in errors):
                raise ValueError("; ".join(errors))

            has_content_change = any(
                (k == "number" and target_record.question.question_number != str(v))
                or (k == "body" and target_record.body != v)
                or (k == "solution" and target_record.solution != v)
                or (k == "answer" and (target_record.question.answer or "") != (v or ""))
                or (k == "points" and target_record.question.points != v)
                or (k == "correct_rate" and target_record.question.correct_rate != v)
                or (k == "unit" and (target_record.unit.display_name or "") != (v or ""))
                or (k == "q_type" and (target_record.question.question_type or "") != (v or ""))
                or (k == "modality" and target_record.question.modality != v)
                or (k == "question_set_id" and target_record.question.question_set_id != v)
                or (k == "content_type" and target_record.question.content_type != v)
                or (k == "strategy_tags" and list(target_record.question.strategy_tags) != list(v))
                or (k == "listening_script_id" and (
                    target_record.relations.listening_script_ids != ([v] if v else [])
                ))
                or (k == "regions")
                or (k == "solution_regions")
                for k, v in values.items()
                if k != "note"
            )

            if not has_content_change:
                if "note" in values and target_review.note != values["note"]:
                    self._review_svc.update_note(job_dir, item_id, values["note"])
                return self.get(job_id)

            canonical_values = {}
            previous_text = {"body": target_record.body, "solution": target_record.solution}

            # Map legacy update keys to canonical values
            for k, v in values.items():
                if k == "number":
                    canonical_values["question_number"] = str(v)
                elif k == "unit":
                    canonical_values["unit"] = v
                elif k == "q_type":
                    canonical_values["q_type"] = v
                elif k == "listening_script_id":
                    canonical_values["listening_script_ids"] = [v] if v else []
                elif k in ("body", "solution", "answer", "points", "correct_rate",
                           "modality", "question_set_id", "content_type", "strategy_tags",
                           "regions", "solution_regions", "note"):
                    canonical_values[k] = v

            # PDF styles check on body
            if "body" in values and target_review.source_styles:
                from ..pipeline.ai import check_boxes
                from ..ingestion.pdf_styles import apply_group_ranges, apply_underlines
                canonical_values["body"], target_review.style_mapping_errors = apply_underlines(
                    canonical_values["body"], target_review.source_styles
                )
                canonical_values["body"], target_review.group_mapping_errors = apply_group_ranges(
                    canonical_values["body"], target_review.source_styles
                )
                target_review.box_checks = check_boxes(canonical_values["body"], target_review.source_styles)

            # Audit issue applied tracking
            applied_issues = []
            if "body" in values or "solution" in values:
                audit = target_review.audit or {}
                for issue in (audit.get("issues", []) if audit.get("revision") == expected else []):
                    suggestion = issue.get("suggestion", "")
                    extracted = issue.get("extracted", "")
                    if (suggestion and extracted and suggestion != extracted
                            and sum(text.count(extracted) for text in previous_text.values()) == 1
                            and any(key in values and extracted in previous_text[key]
                                    and suggestion in canonical_values.get(key, target_record.body if key == "body" else target_record.solution)
                                    and extracted not in canonical_values.get(key, target_record.body if key == "body" else target_record.solution)
                                    for key in ("body", "solution"))):
                        for key in ("body", "solution"):
                            if key in values and extracted in previous_text.get(key, ""):
                                applied_issues.append((issue, key))
                                break

            # Subject math quality normalization
            subject = target_record.question.subject or manifest.metadata.get("subject") or manifest.to_dict().get("subject")
            if subject == "수학" and ("body" in values or "solution" in values):
                from ..domain.math_quality import normalize_math_section
                for key in ("body", "solution"):
                    if key not in values:
                        continue
                    canonical_values[key], math_checks = normalize_math_section(canonical_values[key], key)
                    if math_checks:
                        target_review.quality_checks.extend(math_checks)

            def _apply_update(rec, rev, new_rev):
                from ..domain.services.record_service import _apply_content_values
                for issue, key in applied_issues:
                    issue["applied"] = True
                    issue["applied_section"] = key
                if "regions" in values or "solution_regions" in values:
                    from ..ingestion.region_render import render_region
                    import pypdfium2 as pdfium
                    for key in ("regions", "solution_regions"):
                        if key not in values:
                            continue
                        for region in canonical_values[key]:
                            doc_rel = manifest.documents.get(region["document"]) or manifest.metadata.get("documents", {}).get(region["document"])
                            if doc_rel:
                                doc_path = job_dir / doc_rel
                                if doc_path.is_file():
                                    with pdfium.PdfDocument(str(doc_path)) as pdf:
                                        region["image"] = f"{region['id']}-{new_rev}.png"
                                        render_region(pdf, region, job_dir / "regions" / region["image"])
                _apply_content_values(rec, canonical_values)
                if "note" in values:
                    rev.note = values["note"]

            self._record_svc.mutate(
                job_dir,
                item_id,
                expected,
                _apply_update,
                all_records_by_id=records,
                all_reviews_by_id=reviews,
            )
            return self.get(job_id)

    def link_solution(self, job_id: str, item_id: str, expected_revision: str, candidate: dict) -> dict:
        with self.lock:
            job_dir = self.job_dir(job_id)
            self._record_svc.link_solution(job_dir, item_id, expected_revision, candidate)
            return self.get(job_id)

    def unlink_solution(self, job_id: str, item_id: str, expected_revision: str) -> dict:
        with self.lock:
            job_dir = self.job_dir(job_id)
            self._record_svc.unlink_solution(job_dir, item_id, expected_revision)
            return self.get(job_id)

    def crop_source(self, job_id, item_id, expected, target, box):
        from ..ingestion.asset_crops import (
            normalize_proposed_box, pixel_box_from_proposed, region_bbox_from_proposed, region_for_asset,
        )
        box = normalize_proposed_box(box)
        if not isinstance(target, str) or not target:
            raise ValueError("크롭 대상이 없습니다.")
        if target.startswith("body-region:") or target.startswith("solution-region:"):
            key = "regions" if target.startswith("body-region:") else "solution_regions"
            try:
                index = int(target.rsplit(":", 1)[1])
            except ValueError:
                raise ValueError("크롭 대상이 없습니다.") from None
            job_dir = self.job_dir(job_id)
            target_record = self.records.get(job_dir, item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            pool = [r for r in target_record.provenance.source_regions if r.get("content_role") == ("body" if key == "regions" else "solution")]
            if index < 0 or index >= len(pool):
                raise ValueError("원본 영역을 찾지 못했습니다.")
            if box == [0, 0, 1000, 1000]:
                return self.get(job_id)
            regions = copy.deepcopy(pool)
            regions[index]["bbox"] = region_bbox_from_proposed(regions[index]["bbox"], box)
            return self.update(job_id, item_id, expected, {key: regions})

        if not target.startswith(("asset:", "asset-index:")):
            raise ValueError("크롭 대상이 없습니다.")

        with self.lock:
            job_dir = self.job_dir(job_id)
            manifest = self.jobs.load_manifest(job_dir)
            records = self.records.get_many(job_dir, manifest.record_ids)
            reviews = self.reviews.get_many(job_dir, manifest.record_ids)

            target_record = records.get(item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            if target_record.provenance.revision_id != expected:
                raise Conflict("다른 수정본이 있습니다. 새로고침 후 확인하세요.")

            target_review = reviews.get(item_id) or ReviewState(
                record_id=item_id, applies_to_revision_id=target_record.provenance.revision_id
            )
            reviews[item_id] = target_review

            from .ephemeral_views import build_legacy_item_view
            item_view = build_legacy_item_view(target_record, target_review, manifest)
            legacy_assets = item_view.get("assets") or []

            if target.startswith("asset-index:"):
                try:
                    asset_index = int(target[len("asset-index:"):])
                except ValueError:
                    raise ValueError("삽입된 그림을 찾지 못했습니다.") from None
                legacy_asset = legacy_assets[asset_index] if 0 <= asset_index < len(legacy_assets) else None
            else:
                target_name = target[6:]
                matches = [entry for entry in legacy_assets if entry.get("id") == target_name or entry.get("path") == target_name]
                if len(matches) > 1:
                    raise ValueError("같은 이름의 그림이 여러 개입니다. 문제·해설을 구분해 다시 선택하세요.")
                legacy_asset = matches[0] if matches else None
            if legacy_asset is None:
                raise ValueError("삽입된 그림을 찾지 못했습니다.")

            asset = next((a for a in target_record.assets if a.path == legacy_asset.get("path")), None)
            if asset is None:
                asset = next((a for a in target_record.assets if a.asset_id == legacy_asset.get("id") or a.asset_id.endswith(":" + str(legacy_asset.get("id")))), None)
            if asset is None:
                raise ValueError("삽입된 그림을 찾지 못했습니다.")

            if list((legacy_asset.get("crop") or {}).get("proposed_box") or []) == box:
                return self.get(job_id)

            region = region_for_asset(item_view, legacy_asset)
            pool = item_view.get("solution_regions" if asset.section == "solution" else "regions") or []
            region_index = pool.index(region) if region in pool else (legacy_asset.get("crop") or {}).get("region_index")
            if not region:
                raise ValueError("원본 영역을 찾지 못했습니다.")
            source = job_dir / "regions" / region["image"]
            if not source.is_file():
                raise ValueError("원본 영역 이미지가 없습니다.")

            from PIL import Image
            with Image.open(source) as image:
                pixels = pixel_box_from_proposed(image.size, box)
                cropped = image.crop(tuple(pixels))

            def _apply_crop(rec, rev, new_rev):
                clean_asset_id = legacy_asset.get("id") or asset.asset_id.split(":", 1)[-1]
                relative = f"assets/{item_id}/{new_rev}/{asset.section}-{clean_asset_id}.png"
                destination = job_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                cropped.save(destination)

                old_path = asset.path
                if asset.section == "solution":
                    rec.solution = rec.solution.replace(old_path, relative)
                else:
                    rec.body = rec.body.replace(old_path, relative)

                canon_asset = next((a for a in rec.assets if a.path == old_path or a.asset_id == asset.asset_id), None)
                if canon_asset:
                    canon_asset.path = relative

                crop_meta = {
                    "proposed_box": box, "pixel_box": pixels, "completed_image_boxes": [],
                    "source_region": list(region.get("bbox") or []), "review_required": False,
                    "document": region.get("document"), "page": region.get("page"),
                    "region_index": region_index,
                    "method": "manual_overlay",
                }
                rev_assets = rev.assets or []
                existing_ra = next((a for a in rev_assets if a.get("id") == asset.asset_id), None)
                if existing_ra:
                    existing_ra["path"] = relative
                    existing_ra["crop"] = crop_meta
                else:
                    rev_assets.append({"id": asset.asset_id, "section": asset.section, "path": relative, "crop": crop_meta})
                rev.assets = rev_assets

            self._record_svc.mutate(
                job_dir,
                item_id,
                expected,
                _apply_crop,
                all_records_by_id=records,
                all_reviews_by_id=reviews,
            )
            return self.get(job_id)

    def reapply_diagram_refine(self, job_id, item_id, asset_id, skip_manual=True, dry_run=False):
        from ..ingestion.asset_crops import (
            diagram_crop_from_vision, embedded_image_boxes, normalize_proposed_box, region_for_asset,
        )
        from PIL import Image

        with self.lock:
            job_dir = self.job_dir(job_id)
            manifest = self.jobs.load_manifest(job_dir)
            records = self.records.get_many(job_dir, manifest.record_ids)
            reviews = self.reviews.get_many(job_dir, manifest.record_ids)

            target_record = records.get(item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            target_review = reviews.get(item_id) or ReviewState(
                record_id=item_id, applies_to_revision_id=target_record.provenance.revision_id
            )
            reviews[item_id] = target_review

            from .ephemeral_views import build_legacy_item_view
            item_view = build_legacy_item_view(target_record, target_review, manifest)
            asset = next((entry for entry in item_view.get("assets") or [] if entry.get("id") == asset_id), None)
            if asset is None:
                raise ValueError("삽입된 그림을 찾지 못했습니다.")
            crop = asset.get("crop") or {}
            if skip_manual and crop.get("method") == "manual_overlay":
                return {"status": "skipped_manual", "item_id": item_id, "asset_id": asset_id}
            vision = crop.get("vision_proposed_box") or crop.get("proposed_box")
            if not vision:
                return {"status": "skipped_no_box", "item_id": item_id, "asset_id": asset_id}
            region = region_for_asset(item_view, asset)
            pool = item_view.get("solution_regions" if asset.get("section") == "solution" else "regions") or []
            source = job_dir / "regions" / region["image"]
            if not source.is_file():
                raise ValueError("원본 영역 이미지가 없습니다.")
            document = crop.get("document") or region.get("document")
            document_path = None
            documents_map = manifest.documents or manifest.metadata.get("documents", {})
            if document and documents_map.get(document):
                document_path = job_dir / documents_map[document]
            page = crop.get("page") if crop.get("page") is not None else region.get("page")
            image_boxes = []
            if document_path and document_path.is_file() and page:
                image_boxes = embedded_image_boxes(document_path, page)
            with Image.open(source) as image:
                cropped, refined, pixels, provenance = diagram_crop_from_vision(
                    image, vision, region["bbox"], document_path, page, image_boxes,
                )
            previous_box = normalize_proposed_box(crop.get("proposed_box") or vision)
            refined_box = normalize_proposed_box(refined)
            if refined_box == previous_box and list(crop.get("pixel_box") or []) == list(pixels):
                return {
                    "status": "unchanged", "item_id": item_id, "asset_id": asset_id,
                    "number": target_record.question.question_number, "box_refine": provenance.get("box_refine"),
                }
            record_info = {
                "status": "would_update" if dry_run else "updated",
                "item_id": item_id, "asset_id": asset_id, "number": target_record.question.question_number,
                "vision_proposed_box": list(normalize_proposed_box(vision)),
                "previous_proposed_box": list(previous_box),
                "refined_proposed_box": list(refined_box),
                "box_refine": provenance.get("box_refine"),
            }
            if dry_run:
                return record_info

            def _apply_refine(rec, rev, new_rev):
                relative = f"assets/{item_id}/{new_rev}/{asset_id}.png"
                destination = job_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                cropped.save(destination)

                old_path = asset["path"]
                rec.body = rec.body.replace(old_path, relative)
                rec.solution = rec.solution.replace(old_path, relative)

                canon_asset = next((a for a in rec.assets if a.asset_id == asset_id or a.path == old_path), None)
                if canon_asset:
                    canon_asset.path = relative

                crop_data = {
                    **provenance,
                    "proposed_box": refined_box,
                    "pixel_box": pixels,
                    "source_region": list(region.get("bbox") or []),
                    "document": region.get("document"),
                    "page": region.get("page"),
                    "region_index": pool.index(region) if region in pool else crop.get("region_index"),
                    "method": "extract_refine_reapply",
                }
                rev_assets = rev.assets or []
                existing_ra = next((a for a in rev_assets if a.get("id") == asset_id), None)
                if existing_ra:
                    existing_ra["path"] = relative
                    existing_ra["crop"] = crop_data
                else:
                    rev_assets.append({"id": asset_id, "section": asset.get("section", "body"), "path": relative, "crop": crop_data})
                rev.assets = rev_assets

            self._record_svc.mutate(
                job_dir,
                item_id,
                target_record.provenance.revision_id,
                _apply_refine,
                all_records_by_id=records,
                all_reviews_by_id=reviews,
            )
            return record_info

    def invalidate_dependents(self, job, changed):
        # Kept for backward compatibility with external callers
        if changed.get("kind") == "passage":
            for other in job.get("items", []):
                if other.get("passage_id") == changed.get("id"):
                    invalidate(other)
        elif changed.get("kind") == "script":
            for other in job.get("items", []):
                if other.get("listening_script_id") == changed.get("id"):
                    invalidate(other)

    def review(self, job_id, item_id, expected, action, note=""):
        with self.lock:
            job_dir = self.job_dir(job_id)
            manifest = self.jobs.load_manifest(job_dir)
            records = self.records.get_many(job_dir, manifest.record_ids)

            target_record = records.get(item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            if target_record.provenance.revision_id != expected:
                raise Conflict("문항 버전이 바뀌었습니다.")

            if action == "approve":
                passage_status = None
                if target_record.relations.passage_ids:
                    prec = records.get(target_record.relations.passage_ids[0])
                    passage_status = prec.review.human_approval if prec else None

                script_status = None
                if target_record.relations.listening_script_ids:
                    srec = records.get(target_record.relations.listening_script_ids[0])
                    script_status = srec.review.human_approval if srec else None

                self._review_svc.approve(
                    job_dir,
                    item_id,
                    expected,
                    note,
                    passage_approval_status=passage_status,
                    script_approval_status=script_status,
                    job_warnings=manifest.warnings,
                )
            elif action == "hold":
                self._review_svc.hold(job_dir, item_id, expected, note)
            else:
                raise ValueError("알 수 없는 검수 동작입니다.")
            return self.get(job_id)

    def revert_audit_suggestion(self, job_id, item_id, expected, issue_index):
        with self.lock:
            job_dir = self.job_dir(job_id)
            target_record = self.records.get(job_dir, item_id)
            if not target_record:
                raise ValueError("문항을 찾지 못했습니다.")
            if target_record.provenance.revision_id != expected:
                raise Conflict("문항 버전이 바뀌었습니다.")
            review_state = self.reviews.get(job_dir, item_id) or ReviewState(
                record_id=item_id, applies_to_revision_id=target_record.provenance.revision_id
            )
            audit = review_state.audit or {}
            issues = audit.get("issues") or []
            if issue_index < 0 or issue_index >= len(issues):
                raise ValueError("지적을 찾지 못했습니다.")
            issue = issues[issue_index]
            if not issue.get("applied"):
                raise ValueError("적용된 AI 제안만 되돌릴 수 있습니다.")
            extracted = issue.get("extracted", "")
            suggestion = issue.get("suggestion", "")
            if not extracted or not suggestion or extracted == suggestion:
                raise ValueError("되돌릴 제안이 없습니다.")
            target = issue.get("applied_section")
            if target not in ("body", "solution"):
                target = None
                for key in ("body", "solution"):
                    text = getattr(target_record, key, "") or ""
                    if text.count(suggestion) == 1:
                        target = key
                        break
            if not target:
                raise ValueError("되돌릴 위치를 유일하게 찾지 못했습니다. 편집에서 확인하세요.")
            text = getattr(target_record, target, "") or ""
            if text.count(suggestion) != 1:
                raise ValueError("제안 문구가 한 곳에만 있어야 되돌릴 수 있습니다. 편집에서 확인하세요.")
            reverted = text.replace(suggestion, extracted, 1)

        job = self.update(job_id, item_id, expected, {target: reverted})
        with self.lock:
            self._review_svc.mark_audit_issue_applied(self.job_dir(job_id), item_id, issue_index, False)
            return self.get(job_id)

    def skip_audit_issue(self, job_id, item_id, expected, issue_index, skipped=True, note=None):
        with self.lock:
            job_dir = self.job_dir(job_id)
            self._review_svc.skip_audit_issue(job_dir, item_id, expected, issue_index, skipped, note)
            target_review = self.reviews.get(job_dir, item_id)
            issue = (target_review.audit.get("issues") or [])[issue_index] if target_review and target_review.audit else {}
            target_record = self.records.get(job_dir, item_id)
            job = self.get(job_id)
            item = self.item(job, item_id)
            self._record_audit_skip_decision(job, item, issue_index, issue, skipped)
            return job

    def focused_review(self, job_id, item_id):
        from ..domain.focused_review import focused_review
        with self.lock:
            job = self.get(job_id)
            folder = self.job_dir(job_id)
            def image_exists(region):
                path = (folder / "regions" / region["image"]).resolve()
                return path.is_relative_to((folder / "regions").resolve()) and path.is_file()
            return focused_review(self.item(job, item_id),
                                  writable=job.get("storage_version") == 2,
                                  image_exists=image_exists)

    def resolve_focused_issue(self, job_id, item_id, payload):
        """R3 optimistic preflight; reuse content/review services under the Store lock.

        Restore scoped bytes on caught write failures. This is not a power-loss
        journal or a lock against other processes writing files directly.
        """
        from .focused_transaction import restore_on_failure
        with self.lock:
            view = self.focused_review(job_id, item_id)
            if (payload.get("revision") != view["revision"]
                    or payload.get("review_revision") != view["review_revision"]):
                raise Conflict("문항 또는 검수 기록이 바뀌었습니다. 카드를 새로 불러오세요.")
            card = next((c for c in view["cards"] if c.get("token") == payload.get("token")
                         and c["kind"] == "issue"), None)
            if not card:
                raise Conflict("지적이 바뀌었습니다. 카드를 새로 불러오세요.")
            action = payload.get("action")
            if action not in ("apply", "edit", "skip"):
                raise ValueError("지원하지 않는 카드 동작입니다.")
            if not card.get("can_" + action):
                raise Conflict("이 카드는 수정할 수 없습니다. 상세 검수에서 현재 상태를 확인하세요.")
            note = payload.get("note")
            if action == "skip" and (not isinstance(note, str) or not note.strip() or len(note.strip()) > 500):
                raise ValueError("지적 스킵 사유를 1~500자로 남겨주세요.")
            folder = self.job_dir(job_id)
            record = self.records.get(folder, item_id)
            section = card.get("section")
            if action in ("apply", "edit"):
                text = getattr(record, section)
                value = text.replace(card["extracted"], card["suggestion"], 1) if action == "apply" else payload.get("text")
                if not isinstance(value, str) or not value.strip() or value == text:
                    raise ValueError("변경할 본문을 입력하세요.")
            # R3 is restricted to questions: body/solution edits have no dependent
            # passage/script invalidations. Preserve existing immutable snapshots.
            paths = [folder / "records" / f"{item_id}.json",
                     folder / "review" / f"{item_id}.json",
                     folder / "revisions" / item_id,
                     self.audit_skip_decisions_path(), folder / "runtime.json"]
            with restore_on_failure(paths):
                if action == "skip":
                    result = self.skip_audit_issue(job_id, item_id, view["revision"], card["index"], True, note.strip())
                else:
                    result = self.update(job_id, item_id, view["revision"], {section: value})
                from .evaluation import record_focused_action
                record_focused_action(self, folder, item_id, payload, view["revision"], self.item(result, item_id)["revision"])
                return result

    def waive_audit(self, job_id, item_id, expected, reason):
        with self.lock:
            job_dir = self.job_dir(job_id)
            self._review_svc.waive_audit(job_dir, item_id, expected, reason)
            return self.get(job_id)

    def export(self, job_id, destination=None, source_code=None, *, export_receipt=None):
        with self.lock:
            job_dir = self.job_dir(job_id)
            if not (job_dir / "job.json").exists():
                raise ValueError("작업 파일을 찾지 못했습니다.")

            manifest = self.jobs.load_manifest(job_dir)
            records_map = self.records.get_many(job_dir, manifest.record_ids)
            review_states = self.reviews.get_many(job_dir, manifest.record_ids)
            ordered_records = [records_map[rid] for rid in manifest.record_ids]

            selected_records = [
                rec for rec in ordered_records
                if rec.review.human_approval == "approved"
            ]
            if not selected_records:
                raise ValueError("승인된 항목이 없습니다.")
            if manifest.warnings:
                raise ValueError("작업 범위 경고를 먼저 해결하세요.")

            # Validate boundary and relation consistency
            boundary = canonical_relation_errors(ordered_records)
            if boundary:
                raise ValueError("정규 관계 오류: " + "; ".join(boundary))

            for rec in selected_records:
                rev_state = review_states.get(rec.question.question_id)
                if rev_state:
                    waiver = rev_state.audit_waiver or {}
                    has_waiver = (waiver.get("revision") == rec.provenance.revision_id and waiver.get("status") == "waived")
                    audit = rev_state.audit or {}
                    audit_ok = (audit.get("revision") == rec.provenance.revision_id and audit.get("status") == "completed")
                    if not has_waiver and not audit_ok:
                        raise ValueError("현재 버전의 대조와 승인이 필요합니다.")

                if rec.relations.passage_ids:
                    for pid in rec.relations.passage_ids:
                        prec = next((r for r in ordered_records if r.question.question_id == pid), None)
                        if not prec or prec.review.human_approval != "approved":
                            raise ValueError("공통 지문 승인이 필요합니다.")

            output = Path(destination) if destination else self.output
            batch_code = source_code or manifest.source_id
            source_folder = source_code or manifest.source_id
            if (not isinstance(source_folder, str) or source_folder in (".", "..")
                    or any(c in source_folder for c in ("/", "\\", "\x00"))):
                raise ValueError("출력 코드는 하나의 안전한 폴더 이름이어야 합니다.")
            batch = output / manifest.track / source_folder / (
                f"{batch_code}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            )
            batch.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".export-", dir=batch.parent))
            try:
                if manifest.metadata.get("workbook") or manifest.to_dict().get("workbook"):
                    from ..exporters.workbook_export import write_workbook
                    write_workbook(stage, manifest, job_dir, records=ordered_records, review_states=review_states)
                elif manifest.format == "kice_english":
                    from ..exporters.english_export import write_english_batch
                    write_english_batch(stage, manifest, job_dir, records=ordered_records, review_states=review_states)
                elif source_code is not None:
                    from ..exporters.set_export import write_sets
                    write_sets(stage, manifest, source_code, job_dir, records=ordered_records, review_states=review_states)
                else:
                    canonical_records = []
                    for rec in selected_records:
                        context_folder = (
                            rec.question.question_set_id
                            or rec.source.source_set_id
                            or rec.source.section_code
                            or "general"
                        )
                        folder = stage / context_folder
                        folder.mkdir(parents=True, exist_ok=True)
                        passage_link = f"{rec.relations.passage_ids[0]}.md" if rec.relations.passage_ids else ""
                        plan = {(a.source_path or a.path): a.path for a in rec.assets}
                        from ..exporters.set_export import apply_export_asset_refs
                        apply_export_asset_refs(rec, plan, job_dir)
                        from ..exporters.profiles.markdown import project_to_markdown
                        content = project_to_markdown(rec, passage_file=passage_link)
                        for asset in rec.assets:
                            asset_path = asset.source_path or asset.path
                            src = job_dir / asset_path
                            target = folder / asset.path
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, target)
                        (folder / f"{rec.question.question_id}.md").write_text(content, encoding="utf-8")
                        (folder / f"{rec.question.question_id}.json").write_text(rec.to_json(), encoding="utf-8")
                        canonical_records.append({
                            "record_id": rec.question.question_id,
                            "json_path": str((folder / f"{rec.question.question_id}.json").relative_to(stage)),
                            "markdown_path": str((folder / f"{rec.question.question_id}.md").relative_to(stage)),
                        })
                    if canonical_records:
                        from ..exporters.rebuild import write_canonical_manifest
                        write_canonical_manifest(stage, canonical_records)

                manifest_items = [
                    {"id": rec.question.question_id, "revision": rec.provenance.revision_id}
                    for rec in selected_records
                ]
                export_manifest = {"job_id": job_id, "source_code": source_code, "items": manifest_items}
                if export_receipt is not None:
                    from .export_evidence import payload_files
                    export_manifest["export_receipt"] = copy.deepcopy(export_receipt)
                    export_manifest["files"] = payload_files(stage)
                atomic_json(stage / "manifest.json", export_manifest)
                os.rename(stage, batch)
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise

            for rec in selected_records:
                rev_state = review_states.get(rec.question.question_id)
                if rev_state:
                    rev_state.published_revision_id = rec.provenance.revision_id
                    self.reviews.save(job_dir, rev_state)

            manifest.last_export = str(batch)
            self.jobs.save_manifest(job_dir, manifest)
            return self.get(job_id)

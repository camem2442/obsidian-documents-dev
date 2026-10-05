"""Persistent, explicit per-job AI queues. Never approve automatically."""
import hashlib
import json
import copy
import uuid

from ..domain.review_state import ReviewState

from .ai import process
from .ai_activity import append, begin, label_item
from ..storage.store import Conflict


def snapshot_canonical(record, review_state, section=None):
    body_regions = [r for r in record.provenance.source_regions if r.get("content_role") == "body"]
    sol_regions = [r for r in record.provenance.source_regions if r.get("content_role") == "solution"]
    assets = [{"path": a.path, "section": a.section, "id": a.asset_id} for a in record.assets]

    if section:
        values = {
            section: record.solution if section == "solution" else record.body,
            "solution_regions" if section == "solution" else "regions": (
                sol_regions if section == "solution" else body_regions
            ),
            "answer": record.question.answer,
            "points": record.question.points,
            "unit": record.unit.display_name,
            "q_type": record.question.question_type,
            "review": record.review.human_approval,
            "note": review_state.note,
            "assets": [a for a in assets if a.get("section") == section],
        }
        if section == "solution":
            values["selected_solution_id"] = review_state.selected_solution_id
    else:
        values = {
            "body": record.body,
            "solution": record.solution,
            "regions": body_regions,
            "solution_regions": sol_regions,
            "assets": assets,
            "answer": record.question.answer,
            "points": record.question.points,
            "unit": record.unit.display_name,
            "q_type": record.question.question_type,
            "review": record.review.human_approval,
            "note": review_state.note,
        }
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def select_entries(manifest, records, reviews, operation):
    """One eligibility policy for normal queues and read-only multi-job plans."""
    if operation not in ("extract", "audit"):
        raise ValueError("지원하지 않는 순차 작업입니다.")
    entries, excluded = [], []
    ids = sorted(manifest.record_ids, key=lambda rid: records[rid].question.content_kind == "passage", reverse=True)
    workbook = bool(manifest.metadata.get("workbook") if isinstance(manifest.metadata, dict) else False)
    for rid in ids:
        rec = records[rid]
        rev = reviews.get(rid) or ReviewState(record_id=rid, applies_to_revision_id=rec.provenance.revision_id)
        regions = {section: [r for r in rec.provenance.source_regions if r.get("content_role") == section]
                   for section in ("body", "solution")}
        reasons = []
        if rec.review.human_approval != "pending":
            reasons.append("not_pending_review")
        elif operation == "extract" and workbook:
            for section in rev.transcription_pending or []:
                if section in regions and regions[section]:
                    entries.append({"item": rid, "section": section, "snapshot": snapshot_canonical(rec, rev, section),
                                    "status": "pending", "message": ""})
                else:
                    excluded.append({"item": rid, "section": section, "reasons": ["missing_or_unsupported_source_section"]})
            if not rev.transcription_pending:
                reasons.append("no_pending_transcription")
        elif operation == "audit" and rev.transcription_pending:
            reasons.append("transcription_pending")
        elif operation == "extract" and (not regions["body"] or rev.history or rev.extracted_revision):
            reasons.append("missing_source_or_existing_transcription")
        elif operation == "audit" and (rev.audit or {}).get("status") == "completed" and rev.audit.get("revision") == rec.provenance.revision_id:
            reasons.append("current_audit_exists")
        else:
            entries.append({"item": rid, "snapshot": snapshot_canonical(rec, rev), "status": "pending", "message": ""})
        if reasons:
            excluded.append({"item": rid, "section": None, "reasons": reasons})
    return entries, excluded


class WorkQueue:
    def __init__(self, store, processor=process):
        self.store = store
        self.processor = processor

    def execute_entry(self, job_id, entry, operation):
        """Common dispatch for per-job queues and frozen multi-job entries."""
        with self.store.lock:
            folder = self.store.job_dir(job_id)
            rec = self.store.records.get(folder, entry['item'])
            rev = self.store.reviews.get(folder, entry['item'])
            if not rec or not rev or snapshot_canonical(rec, rev, entry.get('section')) != entry['snapshot']:
                raise Conflict('대기 중 내용이 변경되어 보호했습니다.')
            expected = rec.provenance.revision_id
        kwargs = {'section': entry['section']} if entry.get('section') else {}
        return self.processor(self.store, job_id, entry['item'], expected, operation, **kwargs)

    def preview(self, job_id, operation):
        with self.store.lock:
            folder = self.store.job_dir(job_id)
            manifest = self.store.jobs.load_manifest(folder)
            records = self.store.records.get_many(folder, manifest.record_ids)
            reviews = self.store.reviews.get_many(folder, manifest.record_ids)
            entries, excluded = select_entries(manifest, records, reviews, operation)
            return {"entries": copy.deepcopy(entries), "excluded": excluded}

    def prepare(self, job_id, operation, mode="new"):
        if operation not in ("extract", "audit") or mode not in ("new", "resume", "retry"):
            raise ValueError("지원하지 않는 순차 작업입니다.")
        from ..storage.batch_jobs import execution_lock, ensure_job_unclaimed, ensure_no_job_worker
        with execution_lock(self.store):
            ensure_job_unclaimed(self.store, job_id)
            ensure_no_job_worker(self.store, job_id)
            job_dir = self.store.job_dir(job_id)
            manifest = self.store.jobs.load_manifest(job_dir)
            all_records = self.store.records.get_many(job_dir, manifest.record_ids)
            all_reviews = self.store.reviews.get_many(job_dir, manifest.record_ids)
            runtime_state = self.store.runtime.load(job_dir, job_id)

            old = runtime_state.queue
            if mode != "new":
                if old and old.get("operation") != operation:
                    previous = next((q for q in reversed(runtime_state.queue_history or []) if q.get("operation") == operation), None)
                    if previous:
                        runtime_state.queue_history.append(old)
                        old = previous
                if not old or old.get("operation") != operation:
                    raise ValueError("이어서 실행할 작업이 없습니다.")
                for entry in old.get("entries", []):
                    if entry.get("status") in ("running", "uncertain"):
                        raise Conflict("이전 호출의 결과가 불명확합니다. 자동 재호출할 수 없습니다.")
                    if mode == "retry" and entry.get("status") == "failed":
                        entry["status"] = "pending"
                queue = old
            else:
                if old and any(e.get("status") in ("pending", "running") for e in old.get("entries", [])):
                    raise Conflict("남은 순차 작업을 먼저 이어서 실행하세요.")
                if old:
                    runtime_state.queue_history.append(old)
                entries, _ = select_entries(manifest, all_records, all_reviews, operation)
                queue = {"id": uuid.uuid4().hex, "operation": operation, "entries": entries}

            if not any(e["status"] == "pending" for e in queue["entries"]):
                raise ValueError("실행할 항목이 없습니다. 수정본은 개별 변환을 사용하세요.")
            queue["status"] = "running"
            queue["stop_requested"] = False
            runtime_state.queue = queue
            self.store.runtime.save(job_dir, runtime_state)

            if mode == "new":
                begin(self.store, job_id, operation, source="queue")
            pending = sum(1 for e in queue["entries"] if e["status"] == "pending")
            op_label = "AI 변환" if operation == "extract" else "AI 원본 대조"
            append(
                self.store,
                job_id,
                f"일괄 {op_label} · {pending}건 실행 예정 (전체 {len(queue['entries'])}건)",
                operation=operation,
                source="queue",
                queue_total=len(queue["entries"]),
                queue_done=sum(1 for e in queue["entries"] if e["status"] == "completed"),
            )
            return self.store.get(job_id)

    def pause(self, job_id):
        with self.store.lock:
            job_dir = self.store.job_dir(job_id)
            runtime_state = self.store.runtime.load(job_dir, job_id)
            if runtime_state.queue:
                runtime_state.queue["stop_requested"] = True
                self.store.runtime.save(job_dir, runtime_state)

    def run(self, job_id):
        from ..storage.batch_jobs import execution_lock, ensure_job_unclaimed, job_worker_lease
        with execution_lock(self.store):
            ensure_job_unclaimed(self.store, job_id)
            lease = job_worker_lease(self.store, job_id)
        try:
            return self._run(job_id)
        finally:
            lease.close()

    def _run(self, job_id):
        job_dir = self.store.job_dir(job_id)
        while True:
            with self.store.lock:
                runtime_state = self.store.runtime.load(job_dir, job_id)
                queue = runtime_state.queue
                if not queue:
                    return
                if queue.get("stop_requested"):
                    queue["status"] = "paused"
                    self.store.runtime.save(job_dir, runtime_state)
                    return
                entry = next((e for e in queue["entries"] if e["status"] == "pending"), None)
                if not entry:
                    queue["status"] = "finished"
                    self.store.runtime.save(job_dir, runtime_state)
                    return
                item_id = entry["item"]
                section = entry.get("section")
                rec = self.store.records.get(job_dir, item_id)
                rev = self.store.reviews.get(job_dir, item_id)
                if not rec or not rev:
                    entry.update(status="failed", message="문항을 찾을 수 없습니다.")
                    self.store.runtime.save(job_dir, runtime_state)
                    continue

                if snapshot_canonical(rec, rev, section) != entry["snapshot"]:
                    entry.update(status="skipped", message="대기 중 내용이 변경되어 보호했습니다. 개별 작업을 사용하세요.")
                    self.store.runtime.save(job_dir, runtime_state)
                    continue
                entry["status"] = "running"
                expected = rec.provenance.revision_id
                operation = queue["operation"]
                done = sum(1 for e in queue["entries"] if e["status"] == "completed")
                total = len(queue["entries"])
                sec_label = "해설" if section == "solution" else ("본문" if section else "")
                append(
                    self.store,
                    job_id,
                    f"{label_item(self.store, job_id, item_id)}"
                    f"{(' · ' + sec_label) if sec_label else ''} · {done + 1}/{total} 실행 중…",
                    operation=operation,
                    source="queue",
                    item_id=item_id,
                    item_label=label_item(self.store, job_id, item_id),
                    section=section,
                    queue_done=done,
                    queue_total=total,
                    phase="queue_item",
                )
                self.store.runtime.save(job_dir, runtime_state)

            try:
                self.execute_entry(job_id, entry, operation)
                status, message = "completed", "완료"
            except Exception as exc:
                status, message = "failed", str(exc) if isinstance(exc, ValueError) else "호출 실패 또는 시간 초과. 수정본은 보존됩니다."
                append(
                    self.store,
                    job_id,
                    f"{label_item(self.store, job_id, item_id)} · 실패 · {message}",
                    level="error",
                    operation=operation,
                    source="queue",
                    item_id=item_id,
                )
            with self.store.lock:
                runtime_state = self.store.runtime.load(job_dir, job_id)
                if runtime_state.queue:
                    entry = next(e for e in runtime_state.queue["entries"]
                                 if e["item"] == item_id and e.get("section") == section)
                    entry.update(status=status, message=message)
                    self.store.runtime.save(job_dir, runtime_state)

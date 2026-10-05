import json
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .domain.models import structural_errors
from .pipeline.ai import process
from .pipeline.work_queue import WorkQueue
from .storage.store import Store, Conflict
from .storage.vault import inside_vault, pdf_files


def create_app(store=None, batch_processor=None):
    store = store or Store()
    app = FastAPI(title="Exam Processor", docs_url=None, redoc_url=None)
    token = secrets.token_urlsafe(32)
    busy = set()
    queue = WorkQueue(store)
    from .storage.batch_jobs import BatchJobs, execution_lock, ensure_job_unclaimed, job_worker_lease
    batches = BatchJobs(store, processor=batch_processor, busy=busy)
    from .storage.input_jobs import InputJobs
    inputs = InputJobs(store)
    from .storage.export_jobs import ExportJobs
    exports = ExportJobs(store)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def local_access(request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"error": "Local access only"}, 403)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-exam-token") != token:
            return JSONResponse({"error": "요청 인증이 만료되었습니다. 새로고침하세요."}, 403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path == "/api/config" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"error": str(exc)}, 409 if isinstance(exc, Conflict) else 400)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/config")
    def settings():
        return {"token": token, "samples": {k:v["title"] for k,v in config.SAMPLES.items()},
                "sample_pdf": str(config.KS_ROOT / config.SAMPLE_PDF), "output": str(store.output),
                "ks_root": str(config.KS_ROOT)}

    @app.get("/api/health")
    def health():
        from .storage.ephemeral_views import verify_store_views_contract

        ready = True
        store_error = ""
        try:
            verify_store_views_contract()
        except Exception as exc:
            ready = False
            store_error = str(exc)
        payload = {
            "app": "exam-processor",
            "data_root": str(store.root.resolve()),
            "ready": ready,
            "capabilities": [
                "human_evaluation_v1",
                "offline_evaluation_v1",
                "multi_job_batch_v1",
                "multi_input_v1",
                "safe_bulk_approval_v1",
                "audit_waiver",
                "audit_revert",
                "audit_skip_notes",
                "ai_log",
                "review_queue",
                "focused_review",
                "sample_review",
                "approval_gate",
            ],
        }
        if store_error:
            payload["store_error"] = store_error
        if not ready:
            return JSONResponse(payload, status_code=503)
        return payload

    @app.get("/api/vault/files")
    def files(folder: str):
        return pdf_files(folder, store)

    @app.get("/api/jobs")
    def jobs():
        return store.list()

    @app.get("/api/batches")
    def batch_list():
        return batches.list()

    @app.get("/api/batches/{batch_id}")
    def batch_get(batch_id: str):
        return batches.get(batch_id)

    @app.post("/api/batches/preview")
    def batch_preview(payload: dict):
        return batches.preview(payload.get('job_ids'), payload.get('operation'))

    @app.post("/api/batches")
    def batch_action(payload: dict):
        try:
            result = batches.act(payload)
        except OSError:
            return JSONResponse({'error': '배치 기록 저장 실패. 입력을 보존하고 같은 요청으로 재시도하세요.'}, 500)
        if payload.get('action') in ('execute','resume','retry'):
            def worker():
                try:
                    batches.run(result['id'])
                except (OSError, ValueError):
                    # Durable running remains uncertain when a checkpoint fails.
                    pass
            threading.Thread(target=worker, daemon=True).start()
        return result

    @app.get("/api/export-jobs")
    def export_job_status():
        return exports.dashboard()

    @app.get("/api/export-plans")
    def export_plan_list():
        return exports.list()

    @app.get("/api/export-plans/{plan_id}")
    def export_plan_get(plan_id: str):
        return exports.get(plan_id)

    @app.post("/api/export-plans/preview")
    def export_plan_preview(payload: dict):
        destination = inside_vault(payload["destination"]) if payload.get("destination") else None
        return exports.preview(payload.get("job_ids"), destination)

    @app.post("/api/export-plans")
    def export_plan_action(payload: dict):
        try:
            action = payload.get("action")
            if action == "plan":
                destination = inside_vault(payload["destination"]) if payload.get("destination") else None
                return exports.plan(payload.get("job_ids"), payload.get("preview_hash"),
                                    payload.get("request_id"), destination)
            if action == "execute":
                return exports.execute(payload.get("plan_id"), payload.get("revision"),
                                       payload.get("confirmed_plan_hash"))
            if action == "recover":
                return exports.recover(payload.get("plan_id"))
            raise ValueError("알 수 없는 출력 동작입니다.")
        except OSError:
            return JSONResponse({"error": "출력 기록 저장 실패. 새 요청을 만들지 말고 기록을 조회·복구하세요."}, 500)

    @app.get("/api/input-formats")
    def input_formats():
        from .ingestion.input_plan import catalog
        return catalog()

    @app.get("/api/input-plans")
    def input_list():
        return inputs.list()

    @app.get("/api/input-plans/{plan_id}")
    def input_get(plan_id: str):
        return inputs.get(plan_id)

    @app.post("/api/input-plans/preview")
    def input_preview(payload: dict):
        return inputs.preview(payload.get('inputs'))

    @app.post("/api/input-plans/{plan_id}/batch-preview")
    def input_batch_preview(plan_id: str, payload: dict):
        return inputs.batch_preview(plan_id, payload.get('entry_ids'), payload.get('operation'), batches)

    @app.post("/api/input-plans")
    def input_action(payload: dict):
        try:
            result = inputs.act(payload)
        except OSError:
            return JSONResponse({'error': '입력 기록 저장 실패. 같은 요청으로 재시도하세요.'}, 500)
        if payload.get('action') in ('execute', 'resume', 'retry'):
            def worker():
                try:
                    inputs.run(result['id'])
                except (OSError, ValueError):
                    pass  # Durable running is projected uncertain until explicit recovery.
            threading.Thread(target=worker, daemon=True).start()
        return result

    @app.post("/api/import")
    def import_job(payload: dict):
        if payload.get("sample"):
            if payload["sample"] not in config.SAMPLES:
                raise ValueError("알 수 없는 참조 샘플입니다.")
            sample = config.SAMPLES[payload["sample"]]
            return store.import_file(config.KS_ROOT / sample["path"], "2026학년도 6월 모의평가", sample["track"],
                                     solution=str(config.KS_ROOT / sample["solution"]) if sample.get("solution") else "")
        return store.import_file(payload.get("path", ""), payload.get("source", ""), payload.get("track", ""), payload.get("pages", ""), payload.get("solution", ""),
                                 format_id=payload.get("format", ""), structure=payload.get("structure", ""), layout=payload.get("layout", ""),
                                 script=payload.get("script", ""), answer=payload.get("answer", ""))

    def serialize_job_response(job: dict) -> dict:
        if (job.get("queue") or {}).get("status") == "running" and job["id"] not in busy:
            job["queue"]["status"] = "paused"
        if (job.get("task") or {}).get("status") == "running":
            if job["id"] not in busy:
                job["task"] = {"status": "failed", "message": "이전 실행이 중단되었습니다. 다시 실행할 수 있습니다."}
        for item in job.get("items", []):
            item["errors"] = structural_errors(item)
            item.pop("history", None)
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.get(job_id)
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}")
    def edit(job_id: str, item_id: str, payload: dict):
        job = store.update(job_id, item_id, payload.get("revision"), payload.get("values", {}))
        return serialize_job_response(job)

    @app.get("/api/jobs/{job_id}/items/{item_id}/focused-review")
    def focused_review(job_id: str, item_id: str):
        return store.focused_review(job_id, item_id)

    @app.post("/api/jobs/{job_id}/items/{item_id}/focused-review")
    def resolve_focused_issue(job_id: str, item_id: str, payload: dict):
        try:
            job = store.resolve_focused_issue(job_id, item_id, payload)
        except OSError:
            return JSONResponse({"error": "카드 저장에 실패했습니다. 수정본을 유지하고 다시 불러와 저장 상태를 확인하세요."}, 500)
        return serialize_job_response(job)

    @app.get("/api/jobs/{job_id}/evaluation")
    def evaluation(job_id: str):
        from .storage.evaluation import Evaluation
        return Evaluation(store).get(job_id)

    @app.get("/api/jobs/{job_id}/evaluation/datasets/{dataset_id}")
    def evaluation_dataset(job_id: str, dataset_id: str):
        from .storage.evaluation import Evaluation
        return Evaluation(store).dataset(job_id, dataset_id)

    @app.post("/api/jobs/{job_id}/evaluation")
    def evaluation_action(job_id: str, payload: dict):
        from .storage.evaluation import Evaluation
        try:
            return Evaluation(store).act(job_id, payload)
        except OSError:
            return JSONResponse({"error": "검수 기록 저장 실패. 입력을 유지하고 재조회하세요. 같은 요청 재시도는 중복 저장하지 않습니다."}, 500)

    @app.get("/api/jobs/{job_id}/offline-evaluation")
    def offline_evaluation(job_id: str):
        from .storage.offline_evaluation import OfflineEvaluation
        return OfflineEvaluation(store).list(job_id)

    @app.get("/api/jobs/{job_id}/offline-evaluation/{run_id}/inputs")
    def offline_evaluation_inputs(job_id: str, run_id: str):
        from .storage.offline_evaluation import OfflineEvaluation
        return OfflineEvaluation(store).inputs(job_id, run_id)

    @app.get("/api/jobs/{job_id}/offline-evaluation/{run_id}")
    def offline_evaluation_run(job_id: str, run_id: str):
        from .storage.offline_evaluation import OfflineEvaluation
        return OfflineEvaluation(store).get(job_id, run_id)

    @app.post("/api/jobs/{job_id}/offline-evaluation")
    def offline_evaluation_action(job_id: str, payload: dict):
        from .storage.offline_evaluation import OfflineEvaluation
        try:
            return OfflineEvaluation(store).act(job_id, payload)
        except OSError:
            return JSONResponse({"error": "평가 실행 저장 실패. 응답 입력을 보존합니다. 같은 요청으로 재시도하세요."}, 500)

    @app.get("/api/jobs/{job_id}/bulk-approval")
    def bulk_approval(job_id: str):
        from .storage.bulk_approval import BulkApproval
        return BulkApproval(store).get(job_id)

    @app.post("/api/jobs/{job_id}/bulk-approval")
    def bulk_action(job_id: str, payload: dict):
        from .storage.bulk_approval import BulkApproval
        try:
            return BulkApproval(store).act(job_id, payload)
        except OSError:
            return JSONResponse({"error": "일괄 승인 저장 실패. 일부 승인이 저장되었을 수 있습니다. 다시 불러온 뒤 복구 확인하세요. 자동 재승인하지 않습니다."}, 500)

    @app.get("/api/jobs/{job_id}/sample-review")
    def sample_review(job_id: str):
        from .storage.sample_review import SampleReview
        return SampleReview(store).get(job_id)

    @app.post("/api/jobs/{job_id}/sample-review")
    def sample_action(job_id: str, payload: dict):
        from .storage.sample_review import SampleReview
        try:
            return SampleReview(store).act(job_id, payload)
        except OSError:
            return JSONResponse({"error": "표본 기록 저장에 실패했습니다. 입력을 유지하고 다시 불러와 저장 상태를 확인하세요."}, 500)

    @app.post("/api/jobs/{job_id}/items/{item_id}/preview")
    def preview_draft(job_id: str, item_id: str, payload: dict):
        # Read-only projection of unsaved text. No Store.update or AI calls.
        from .storage.ephemeral_views import attach_display_titles
        job = store.get(job_id)
        item = next((i for i in job["items"] if i["id"] == item_id), None)
        if item is None:
            raise ValueError("문항을 찾을 수 없습니다.")
        if payload.get("revision") != item.get("revision"):
            raise Conflict("문항 버전이 바뀌었습니다.")
        for key, value in payload.get("values", {}).items():
            if key in {"body", "solution", "answer"}:
                if not isinstance(value, str):
                    raise ValueError("미리보기 본문과 정답은 문자열이어야 합니다.")
                item[key] = value
        attach_display_titles(job)
        return item.get("standard_preview", {"status": "unavailable", "warnings": ["표준 표시 미지원"]})

    @app.post("/api/jobs/{job_id}/items/{item_id}/crop")
    def crop(job_id: str, item_id: str, payload: dict):
        job = store.crop_source(job_id, item_id, payload.get("revision"), payload.get("target"), payload.get("box"))
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/review")
    def review(job_id: str, item_id: str, payload: dict):
        job = store.review(job_id, item_id, payload.get("revision"), payload.get("action"), payload.get("note", ""))
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/audit-waiver")
    def audit_waiver(job_id: str, item_id: str, payload: dict):
        job = store.waive_audit(job_id, item_id, payload.get("revision"), payload.get("reason", ""))
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/audit-issues/{issue_index}/revert")
    def revert_audit_issue(job_id: str, item_id: str, issue_index: int, payload: dict):
        job = store.revert_audit_suggestion(job_id, item_id, payload.get("revision"), issue_index)
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/audit-issues/{issue_index}/skip")
    def skip_audit_issue(job_id: str, item_id: str, issue_index: int, payload: dict):
        job = store.skip_audit_issue(
            job_id, item_id, payload.get("revision"), issue_index,
            payload.get("skipped", True), payload.get("note")
        )
        return serialize_job_response(job)

    @app.get("/api/human-findings")
    def human_findings(format_id: str, section: str, track: str = ""):
        data = store.human_finding_prompt_data(format_id, section, track)
        return {"findings": store.human_findings(format_id, section, track),
                "conflict_count": data["conflict_count"],
                "resolved_count": data["resolved_count"],
                "duplicate_count": data["duplicate_count"],
                "prompt_rule_count": len(data["findings"])}

    @app.post("/api/jobs/{job_id}/items/{item_id}/human-findings")
    def add_human_finding(job_id: str, item_id: str, payload: dict):
        return store.add_human_finding(job_id, item_id, payload.get("revision"), payload.get("values", {}))

    @app.post("/api/jobs/{job_id}/items/{item_id}/quality-checks/{check_index}/promote")
    def promote_quality_check(job_id: str, item_id: str, check_index: int, payload: dict):
        return store.approve_quality_check(job_id, item_id, payload.get("revision"), check_index)

    @app.post("/api/human-findings/{finding_id}/deactivate")
    def deactivate_human_finding(finding_id: str):
        return store.deactivate_human_finding(finding_id)

    @app.post("/api/human-findings/{finding_id}/resolve-conflict")
    def resolve_human_finding_conflict(finding_id: str, payload: dict):
        return store.resolve_human_finding_conflict(finding_id, payload.get("rationale", ""))

    @app.post("/api/jobs/{job_id}/items/{item_id}/normalize")
    def normalize(job_id: str, item_id: str, payload: dict):
        from .text_layout import normalize_markdown
        section = payload.get("section", "body")
        if section not in ("body", "solution"):
            raise ValueError("지원하지 않는 본문 영역입니다.")
        job = store.get(job_id)
        item = store.item(job, item_id)
        normalized = normalize_markdown(item[section])
        if section == "body" and item["regions"] and item["body"] == item.get("reference_text"):
            from .ingestion.formats.korean.kice_korean import reflow_saved_draft
            normalized = reflow_saved_draft(job, item, store.job_dir(job_id))
        job = store.update(job_id, item_id, payload.get("revision"), {section: normalized})
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/link")
    def link(job_id: str, item_id: str, payload: dict):
        job = store.get(job_id)
        item = store.item(job, item_id)
        candidates = item.get("solution_candidates", [])
        candidate = next((c for c in candidates if c["id"] == payload.get("candidate")), None)
        if candidate is None:
            raise ValueError("해설 후보를 찾지 못했습니다.")
        job = store.link_solution(job_id, item_id, payload.get("revision"), candidate)
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/unlink")
    def unlink(job_id: str, item_id: str, payload: dict):
        job = store.unlink_solution(job_id, item_id, payload.get("revision"))
        return serialize_job_response(job)

    @app.post("/api/jobs/{job_id}/items/{item_id}/ai")
    def ai(job_id: str, item_id: str, payload: dict):
        from .pipeline.ai_activity import begin

        operation = payload.get("operation")
        section = payload.get("section", "body")
        if operation not in ("audit", "extract") or section not in ("body", "solution"):
            raise ValueError("지원하지 않는 AI 작업입니다.")
        job_dir = store.job_dir(job_id)
        with execution_lock(store):
            ensure_job_unclaimed(store, job_id)
            rec = store.records.get(job_dir, item_id)
            if not rec:
                raise ValueError("문항을 찾지 못했습니다.")
            if job_id in busy:
                raise Conflict("이 작업에서 AI가 실행 중입니다.")
            if rec.provenance.revision_id != payload.get("revision"):
                raise Conflict("문항 버전이 바뀌었습니다.")
            lease = job_worker_lease(store, job_id)
            try:
                busy.add(job_id)
                begin(store, job_id, operation, source="single")
                runtime_state = store.runtime.load(job_dir, job_id)
                runtime_state.task = {
                    "status": "running",
                    "item": item_id,
                    "operation": operation,
                    "section": section,
                }
                store.runtime.save(job_dir, runtime_state)
            except BaseException:
                busy.discard(job_id)
                lease.close()
                raise
        def worker():
            result = {"status": "uncertain", "message": "호출 완료를 확인할 수 없습니다."}
            try:
                process(store, job_id, item_id, payload["revision"], operation, section)
                result = {"status": "completed", "message": "AI 작업 완료"}
            except Exception as exc:
                result = {"status": "failed", "message": str(exc) if isinstance(exc, ValueError) else "AI 작업이 중단되었거나 시간을 초과했습니다. 수정본은 보존됩니다."}
            finally:
                try:
                    with store.lock:
                        runtime_state = store.runtime.load(job_dir, job_id)
                        runtime_state.task = result
                        store.runtime.save(job_dir, runtime_state)
                finally:
                    busy.discard(job_id)
                    lease.close()
        try:
            threading.Thread(target=worker, daemon=True).start()
        except BaseException:
            busy.discard(job_id)
            lease.close()
            raise
        return {"started": True}

    @app.post("/api/jobs/{job_id}/export")
    def export(job_id: str, payload: dict):
        destination = inside_vault(payload["destination"]) if payload.get("destination") else None
        return store.export(job_id, destination, payload.get("source_code"))

    @app.post("/api/jobs/{job_id}/queue")
    def start_queue(job_id: str, payload: dict):
        with store.lock:
            if job_id in busy:
                raise Conflict("AI 작업이 실행 중입니다.")
            result = queue.prepare(job_id, payload.get("operation"), payload.get("mode", "new"))
            busy.add(job_id)
        def worker():
            try:
                queue.run(job_id)
            finally:
                with store.lock:
                    busy.discard(job_id)
        threading.Thread(target=worker, daemon=True).start()
        return result

    @app.post("/api/jobs/{job_id}/queue/pause")
    def pause_queue(job_id: str):
        queue.pause(job_id)
        return {"requested": True}

    @app.get("/api/jobs/{job_id}/file/{filename:path}")
    def file(job_id: str, filename: str):
        job = store.get(job_id)
        allowed = set(job["documents"].values())
        for item in job["items"]:
            allowed.update("regions/" + r["image"] for r in item["regions"] + item["solution_regions"])
            allowed.update(a["path"] for a in item["assets"])
        if filename not in allowed:
            raise ValueError("등록되지 않은 파일입니다.")
        return FileResponse(store.job_dir(job_id) / filename)

    from .storage.ephemeral_views import verify_store_views_contract

    verify_store_views_contract()
    return app

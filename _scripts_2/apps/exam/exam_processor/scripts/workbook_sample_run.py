"""Explicit, resumable sample transcription; never approves or publishes items."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ..pipeline.ai import invoke, process
from ..domain.models import digest, link_solution, markdown_note, structural_errors
from ..storage.store import Store, atomic_json


def run(root, operation, limit):
    store = Store()
    job_id = json.loads((root / "sample-job.json").read_text())["job_id"]
    job = store.get(job_id)
    if not job.get("workbook") or set(job["layout"].get("scope", [])) != {"A1", "D1"}:
        raise ValueError("Only the reviewed A1/D1 sample is in scope.")
    if any(i["review"] != "pending" for i in job["items"]):
        raise ValueError("Reviewed items must not be changed by the sample runner.")
    for item in job["items"]:
        if item["kind"] == "question" and not item.get("selected_solution_id"):
            candidates = item.get("solution_candidates", [])
            if len(candidates) != 1:
                raise ValueError("Resolve ambiguous or missing solutions first.")
            link_solution(item, candidates[0])
            item["solution_match"]["confirmed_by_user"] = False
            item["solution_match"]["confirmed_by"] = "assistant_source_boundary_review"
    store.save(job)
    cache = root / "responses"
    cache.mkdir(exist_ok=True)
    log_path = root / "sample-run-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    calls = 0

    def caller(payload):
        nonlocal calls
        signature = {**payload, "images": [digest(p) for p in payload.get("images", [])],
                     "adapter": digest(Path(__file__).parents[1] / "ai_worker.py")}
        key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        saved = cache / f"{key}.json"
        if saved.exists():
            return json.loads(saved.read_text())
        if calls >= limit:
            raise ValueError("Sample call limit reached; saved work is preserved.")
        calls += 1
        response = invoke(payload)
        atomic_json(saved, response)
        return response

    failures = 0
    for original in job["items"]:
        for section in (("body", "solution") if operation == "extract" else ("body",)):
            current = store.get(job_id)
            item = store.item(current, original["id"])
            if operation == "extract" and section not in item.get("transcription_pending", []):
                continue
            if operation == "audit":
                audit = item.get("audit") or {}
                if item.get("transcription_pending") or (audit.get("status") == "completed" and audit.get("revision") == item["revision"]):
                    continue
            started = time.monotonic()
            record = {"item": item["id"], "number": item["number"], "operation": operation,
                      "section": section, "input_revision": item["revision"]}
            try:
                process(store, job_id, item["id"], item["revision"], operation, section, caller)
                record["status"] = "completed"
                failures = 0
            except Exception as exc:
                record.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
                failures += 1
            record["seconds"] = round(time.monotonic() - started, 1)
            log.append(record)
            atomic_json(log_path, log)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if failures >= 2 or calls >= limit:
                break
        if failures >= 2 or calls >= limit:
            break
    current = store.get(job_id)
    report = {"job_id": job_id, "items": len(current["items"]), "calls_this_run": calls,
              "human_approved": False, "items_status": [
                  {"id": i["id"], "number": i["number"], "pending": i.get("transcription_pending", []),
                   "structural_errors": structural_errors(i), "audit": i.get("audit"), "review": i["review"]}
                  for i in current["items"]]}
    atomic_json(root / "sample-report.json", report)
    print(json.dumps({"calls": calls, "pending_sections": sum(len(i.get("transcription_pending", [])) for i in current["items"])}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("operation", choices=["extract", "audit"])
    parser.add_argument("--max-calls", type=int, default=70)
    args = parser.parse_args()
    if args.max_calls < 1:
        raise ValueError("Call limit must be positive.")
    run(args.root, args.operation, args.max_calls)


if __name__ == "__main__":
    main()

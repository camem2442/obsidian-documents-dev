"""Append live AI processing messages to the job runtime state for UI polling."""
import time

MAX_ENTRIES = 400


def _item_label(store, job_id, item_id):
    job_dir = store.job_dir(job_id)
    rec = store.records.get(job_dir, item_id)
    if rec:
        kind = rec.question.content_kind
        number = rec.question.question_number or item_id[:8]
        if kind == "passage":
            return f"지문 {number}"
        if kind == "concept":
            return f"개념 {number}"
        return f"{number}번"
    return item_id[:8]


def begin(store, job_id, operation, *, source="single", reset=True):
    with store.lock:
        job_dir = store.job_dir(job_id)
        runtime_state = store.runtime.load(job_dir, job_id)
        if reset:
            runtime_state.ai_log = []
        runtime_state.ai_progress = {
            "operation": operation,
            "source": source,
            "started_at": time.time(),
            "updated_at": time.time(),
            "message": "준비 중…",
        }
        store.runtime.save(job_dir, runtime_state)


def append(store, job_id, message, *, level="info", **fields):
    with store.lock:
        job_dir = store.job_dir(job_id)
        runtime_state = store.runtime.load(job_dir, job_id)
        entry = {"t": time.time(), "level": level, "message": message}
        entry.update({k: v for k, v in fields.items() if v is not None})
        runtime_state.ai_log.append(entry)
        if len(runtime_state.ai_log) > MAX_ENTRIES:
            runtime_state.ai_log = runtime_state.ai_log[-MAX_ENTRIES:]
        progress = runtime_state.ai_progress
        progress["updated_at"] = entry["t"]
        progress["message"] = message
        for key in (
            "operation", "source", "item_id", "item_label", "section", "phase",
            "queue_done", "queue_total", "batch", "batch_total",
        ):
            if key in fields and fields[key] is not None:
                progress[key] = fields[key]
        store.runtime.save(job_dir, runtime_state)


def label_item(store, job_id, item_id):
    return _item_label(store, job_id, item_id)


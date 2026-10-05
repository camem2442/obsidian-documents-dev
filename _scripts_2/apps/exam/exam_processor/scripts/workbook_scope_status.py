"""Summarize A1/D1 workbook sample job status for rerun checklists."""
import argparse
import json
from pathlib import Path

from ..storage.store import Store


def summarize(root: Path):
    root = root.expanduser().resolve()
    meta = json.loads((root / "sample-job.json").read_text(encoding="utf-8"))
    layout = json.loads((root / "layout.json").read_text(encoding="utf-8"))
    scope = set(layout.get("scope") or [])
    job = Store().get(meta["job_id"])
    items = [i for i in job["items"] if i.get("context") in scope]
    pending_sections = sum(len(i.get("transcription_pending") or []) for i in items)
    audited = sum(
        1 for i in items
        if (i.get("audit") or {}).get("status") == "completed"
        and (i.get("audit") or {}).get("revision") == i.get("revision")
    )
    split_audits = sum(
        1 for i in items
        if (i.get("audit") or {}).get("batch_count", 0) > 1
    )
    from ..domain.models import structural_errors as se
    with_errors = [i["number"] for i in items if se(i)]
    return {
        "job_id": job["id"],
        "scope": sorted(scope),
        "items": len(items),
        "pending_sections": pending_sections,
        "audited_current_revision": audited,
        "split_audit_items": split_audits,
        "structural_error_items": with_errors,
        "approved": sum(1 for i in items if i.get("review") != "pending"),
        "queue_status": (job.get("queue") or {}).get("status"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(summarize(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

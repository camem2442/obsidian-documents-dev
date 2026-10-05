"""Reset transcription and audit for a scoped workbook job; keep region geometry."""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..domain.models import link_solution, revision
from ..storage.store import Store, atomic_json


def _reset_item(item):
    """Return a fresh transcription state while preserving regions and matching metadata."""
    item.setdefault("solution_candidates", [])
    item.pop("selected_solution_id", None)
    item.pop("solution_link_backup", None)
    item.pop("extracted_revision", None)
    item.pop("section_validation", None)
    item.pop("solution_source_styles", None)

    item["revision"] = revision()
    item["body"] = "[전사 대기]"
    item["solution"] = ""
    item["answer"] = ""
    item["points"] = None
    item["correct_rate"] = None
    item["assets"] = []
    item["source_styles"] = []
    item["style_mapping_errors"] = []
    item["group_mapping_errors"] = []
    item["box_checks"] = []
    item["material_validation_errors"] = []
    item["quality_checks"] = []
    item["ai_runs"] = []
    item["audit"] = None
    item["note"] = ""
    item["review"] = "pending"
    item["published_revision"] = ""
    item["history"] = []

    for candidate in item["solution_candidates"]:
        candidate["body"] = "[해설 전사 대기]"
        candidate["answer"] = ""

    item["warnings"] = [
        warning for warning in item.get("warnings", [])
        if warning in ("해설 후보를 확인해 연결하세요.",)
        or "해설 매칭" in warning
    ]

    item["transcription_pending"] = ["body"]
    if item["kind"] == "question":
        item["transcription_pending"].append("solution")
        candidates = item.get("solution_candidates", [])
        if len(candidates) == 1:
            link_solution(item, candidates[0])
            match = item.setdefault("solution_match", {})
            match["confirmed_by_user"] = False
            match.pop("confirmed_by", None)
        elif len(candidates) != 1:
            item["warnings"] = list(dict.fromkeys(
                item["warnings"] + ["해설 후보를 확인해 연결하세요."]
            ))


def reset_workbook_scope(root: Path, *, dry_run: bool = False):
    root = root.expanduser().resolve()
    meta = json.loads((root / "sample-job.json").read_text(encoding="utf-8"))
    layout = json.loads((root / "layout.json").read_text(encoding="utf-8"))
    scope = set(layout.get("scope") or [])
    if not scope:
        raise ValueError("layout.json에 scope가 없습니다.")

    store = Store()
    job = store.get(meta["job_id"])
    if not job.get("workbook") or set(job["layout"].get("scope", [])) != scope:
        raise ValueError("sample-job과 layout scope가 일치하는 한완기 작업만 reset할 수 있습니다.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = root / "backups" / f"pre-reset-{stamp}"
    job_path = store.job_dir(job["id"]) / "job.json"
    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(job_path, backup_dir / "job.json")
        for name in ("sample-report.json", "sample-run-log.json"):
            source = root / name
            if source.exists():
                shutil.copy2(source, backup_dir / name)

    targeted = [item for item in job["items"] if item.get("context") in scope]
    if not targeted:
        raise ValueError("scope에 해당하는 항목이 없습니다.")

    summary = {
        "job_id": job["id"],
        "scope": sorted(scope),
        "items_reset": len(targeted),
        "backup_dir": str(backup_dir),
        "dry_run": dry_run,
    }
    if dry_run:
        summary["item_numbers"] = [item["number"] for item in targeted]
        return summary

    for item in targeted:
        _reset_item(item)
    job.pop("queue", None)
    job.pop("queue_history", None)
    store.save(job)

    atomic_json(root / "reset-manifest.json", {
        **summary,
        "reset_at": stamp,
        "items": [{"id": i["id"], "context": i["context"], "number": i["number"], "kind": i["kind"]}
                  for i in targeted],
    })
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="workbook dir (sample-job.json 포함)")
    parser.add_argument("--dry-run", action="store_true", help="백업·저장 없이 대상만 표시")
    args = parser.parse_args(argv)
    result = reset_workbook_scope(args.root, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

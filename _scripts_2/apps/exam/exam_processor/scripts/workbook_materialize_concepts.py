"""Flatten concept items: MATERIAL fig blocks become body text only."""
import argparse
import json
from pathlib import Path

from ..domain.models import invalidate
from ..pipeline.concept_extract import flatten_concept_material_body
from ..storage.store import Store


def materialize_item(item, dry_run):
    if item.get("kind") != "concept":
        return {"status": "skipped_not_concept", "number": item.get("number")}
    body = item.get("body") or ""
    flattened = flatten_concept_material_body(body)
    body_assets = [a for a in item.get("assets") or [] if a.get("section") == "body"]
    if flattened == body.strip() and not body_assets and "<!-- MATERIAL:" not in body:
        return {"status": "unchanged", "number": item.get("number")}
    record = {
        "status": "would_update" if dry_run else "updated",
        "number": item.get("number"),
        "removed_assets": [a.get("id") for a in body_assets],
        "had_material": "<!-- MATERIAL:" in body,
    }
    if dry_run:
        return record
    invalidate(item)
    item["body"] = flattened
    item["assets"] = [a for a in item.get("assets") or [] if a.get("section") != "body"]
    item["material_validation_errors"] = []
    item.setdefault("warnings", []).append("개념 블록을 fig 없이 본문 Markdown으로 정리했습니다.")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Workbook root with sample-job.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    meta = json.loads((root / "sample-job.json").read_text(encoding="utf-8"))
    layout = json.loads((root / "layout.json").read_text(encoding="utf-8"))
    scope = set(layout.get("scope") or [])
    store = Store()
    with store.lock:
        job = store.get(meta["job_id"])
        results = []
        for item in job["items"]:
            if scope and item.get("context") not in scope:
                continue
            if item.get("kind") != "concept":
                continue
            results.append(materialize_item(item, args.dry_run))
        if not args.dry_run and any(r.get("status") == "updated" for r in results):
            store.save(job)
    summary = {"job_id": meta["job_id"], "dry_run": args.dry_run, "counts": {}}
    for record in results:
        summary["counts"][record["status"]] = summary["counts"].get(record["status"], 0) + 1
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

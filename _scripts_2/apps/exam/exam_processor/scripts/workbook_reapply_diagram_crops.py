"""Re-run extract-style diagram box refine on saved assets without Vision API calls."""
import argparse
import json
from pathlib import Path

from ..storage.store import Store


def iter_assets(job, scope):
    for item in job["items"]:
        if scope and item.get("context") not in scope:
            continue
        for asset in item.get("assets") or []:
            yield item, asset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Workbook root with sample-job.json")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without saving")
    parser.add_argument("--number", action="append", default=[], help="Limit to item number(s)")
    parser.add_argument("--include-manual", action="store_true", help="Also reapply manual_overlay crops")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    meta = json.loads((root / "sample-job.json").read_text(encoding="utf-8"))
    layout = json.loads((root / "layout.json").read_text(encoding="utf-8"))
    scope = set(layout.get("scope") or [])
    store = Store()
    job = store.get(meta["job_id"])
    numbers = set(args.number)
    results = []
    for item, asset in iter_assets(job, scope):
        if numbers and item.get("number") not in numbers:
            continue
        try:
            record = store.reapply_diagram_refine(
                job["id"], item["id"], asset["id"],
                skip_manual=not args.include_manual,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            record = {
                "status": "error", "item_id": item["id"], "asset_id": asset["id"],
                "number": item.get("number"), "error": str(exc),
            }
        results.append(record)
        if not args.dry_run and record.get("status") == "updated":
            job = store.get(job["id"])

    summary = {
        "job_id": job["id"],
        "scope": sorted(scope),
        "dry_run": args.dry_run,
        "counts": {},
    }
    for record in results:
        summary["counts"][record["status"]] = summary["counts"].get(record["status"], 0) + 1
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Read-only source-backed checks for agent review loops, not approval or R6 labels.

Keep expected values outside AI prompts. Each run writes a new evidence file;
the next run compares the same checks rather than silently redefining success.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..storage.store import Store

SCHEMA = "agent-source-review/1"
FIELDS = {"body", "solution", "answer", "points", "correct_rate"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def evaluate_case(item, case):
    checks, seen = [], set()
    if not case.get("checks") or not case.get("reviewer") or not case.get("evidence"):
        raise ValueError("Source reviewer, evidence and explicit checks are required")
    for check in case["checks"]:
        key, field = check.get("id"), check.get("field")
        operators = set(check) & {"equals", "matches", "absent"}
        if not key or key in seen or field not in FIELDS or len(operators) != 1:
            raise ValueError("Invalid or duplicate source check")
        seen.add(key)
        operation = next(iter(operators))
        actual, expected = item.get(field), check[operation]
        if operation == "equals":
            passed = actual == expected
        else:
            if not isinstance(expected, str) or not expected:
                raise ValueError("A nonempty source pattern is required")
            found = re.search(expected, str(actual or ""), re.MULTILINE) is not None
            passed = found if operation == "matches" else not found
        checks.append({"id": key, "contract_hash": digest(check), "passed": passed,
                       "field": field, "actual": actual})
    return {"id": case["id"], "revision": item["revision"],
            "reviewer": case["reviewer"], "evidence": case["evidence"],
            "content_hash": digest({key: item.get(key) for key in sorted(FIELDS)}),
            "checks": checks,
            "rules_used": [run for run in item.get("ai_runs", [])
                           if run.get("operation") == "extract"]}


def compare(previous, current):
    def rows(report):
        return {(case["id"], check["id"]): check
                for case in report["cases"] for check in case["checks"]}
    old, new = rows(previous), rows(current)
    missing, changed, regressions, fixed = [], [], [], []
    for key, before in old.items():
        if key not in new:
            missing.append(key)
        elif before["contract_hash"] != new[key]["contract_hash"]:
            changed.append(key)
        elif before["passed"] and not new[key]["passed"]:
            regressions.append(key)
        elif not before["passed"] and new[key]["passed"]:
            fixed.append(key)
    changed_sources = []
    for field in ("source_hashes", "crop_hashes"):
        before, after = previous.get(field), current.get(field)
        if field == "source_hashes" and before != after:
            changed_sources.append(field)
        if field == "crop_hashes" and any(after.get(k) != v for k, v in (before or {}).items()):
            changed_sources.append(field)
    return {"missing_checks": missing, "changed_checks": changed, "changed_sources": changed_sources,
            "regressions": regressions, "fixed": fixed}


def run(spec, previous=None):
    if previous is not None and previous.get("schema") != SCHEMA:
        raise ValueError("Unsupported previous review checkpoint")
    if spec.get("schema") != SCHEMA or not spec.get("cases"):
        raise ValueError("A nonempty review specification is required")
    ids = [case["id"] for case in spec["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate review case")
    sources = spec.get("sources", [])
    if not sources or any(file_hash(s["path"]) != s["sha256"] for s in sources):
        raise ValueError("Original source missing or changed")
    # Explicit roots only; this tool never selects the default live data root.
    stores, cases, crops = {}, [], {}
    for case in spec["cases"]:
        root = str(Path(case["data_root"]).resolve())
        if not Path(root).is_dir():
            raise ValueError("Review data root does not exist")
        store = stores.setdefault(root, Store(root))
        job = store.get(case["job_id"])
        item = store.item(job, case["item_id"])
        if item.get("transcription_pending"):
            raise ValueError("Incomplete transcription cannot enter a review checkpoint")
        if not case.get("source_regions"):
            raise ValueError("Pinned source crops are required")
        actual_regions = {r["image"] for r in item.get("regions", []) + item.get("solution_regions", [])}
        if actual_regions != set(case["source_regions"]):
            raise ValueError("Source crop membership changed")
        for name, expected in case["source_regions"].items():
            folder = (store.job_dir(job["id"]) / "regions").resolve()
            path = (folder / name).resolve()
            if not path.is_relative_to(folder) or file_hash(path) != expected:
                raise ValueError("Source crop changed")
            crops[str(path)] = expected
        cases.append(evaluate_case(item, case))
    report = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
              "spec_hash": digest(spec), "source_hashes": sources, "crop_hashes": crops,
              "cases": cases, "boundary": "Checks of declared source facts only; no semantic completeness or approval"}
    comparison = compare(previous, report) if previous else None
    basis_change = bool(previous and previous.get("basis_change_requires_review")) or bool(
        comparison and any(comparison[k] for k in ("missing_checks", "changed_checks", "changed_sources")))
    failed = sum(not check["passed"] for case in cases for check in case["checks"])
    report.update(failed_checks=failed, checked=sum(len(c["checks"]) for c in cases),
                  previous_hash=digest(previous) if previous else None, comparison=comparison,
                  basis_change_requires_review=basis_change,
                  passed=not failed and not basis_change and not (comparison and any(comparison[k] for k in
                           ("missing_checks", "changed_checks", "changed_sources", "regressions"))))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    previous = json.loads(args.previous.read_text()) if args.previous else None
    report = run(spec, previous)
    # Exclusive creation preserves every earlier checkpoint, including failures.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("passed", "checked", "failed_checks", "comparison")}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

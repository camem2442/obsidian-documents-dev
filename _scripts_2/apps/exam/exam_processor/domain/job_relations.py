"""Job and batch relation ID existence checks."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional



def relation_errors(job: Dict[str, Any], item_ids: Optional[Iterable[str]] = None) -> List[str]:
    """Return missing passage/solution relation IDs inside a runtime job."""
    items = [item for item in job.get("items", []) if isinstance(item, dict)]
    by_id = {}
    errors = []
    for item in items:
        item_id = item.get("id")
        if not item_id:
            errors.append("item id is required")
            continue
        if item_id in by_id:
            errors.append(f"duplicate item id: {item_id}")
        by_id[item_id] = item

    selected = set(item_ids) if item_ids is not None else set(by_id)
    for item_id in selected:
        item = by_id.get(item_id)
        if item is None:
            errors.append(f"missing item: {item_id}")
            continue
        passage_id = item.get("passage_id") or ""
        if passage_id:
            passage = by_id.get(passage_id)
            if passage is None:
                errors.append(f"{item_id}: passage_id {passage_id} does not exist")
            elif passage.get("kind") != "passage":
                errors.append(f"{item_id}: passage_id {passage_id} is not a passage")
        solution_id = item.get("selected_solution_id") or ""
        if solution_id:
            candidates = {
                candidate.get("id")
                for candidate in item.get("solution_candidates") or []
                if candidate.get("id")
            }
            if candidates and solution_id not in candidates:
                errors.append(f"{item_id}: selected_solution_id {solution_id} does not exist")
        script_id = item.get("listening_script_id") or ""
        if script_id:
            script = by_id.get(script_id)
            if script is None:
                errors.append(f"{item_id}: listening_script_id {script_id} does not exist")
            elif script.get("kind") != "script":
                errors.append(f"{item_id}: listening_script_id {script_id} is not a script")
    return errors

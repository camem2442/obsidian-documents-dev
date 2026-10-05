"""Structural relations between canonical records."""
from typing import Iterable, List
from .schema import CanonicalQuestionRecord


def canonical_relation_errors(records: Iterable[CanonicalQuestionRecord]) -> List[str]:
    """Return missing passage/solution IDs among canonical records."""
    by_id = {}
    errors = []
    for record in records:
        record_id = record.question.question_id
        if not record_id:
            errors.append("question.question_id is required")
            continue
        if record_id in by_id:
            errors.append(f"duplicate question_id: {record_id}")
        by_id[record_id] = record
    for record in by_id.values():
        for passage_id in record.relations.passage_ids:
            passage = by_id.get(passage_id)
            if passage is None:
                errors.append(f"{record.question.question_id}: passage {passage_id} is missing")
            elif passage.question.content_kind != "passage":
                errors.append(f"{record.question.question_id}: {passage_id} is not a passage")
        for solution_id in record.relations.solution_ids:
            solution = by_id.get(solution_id)
            if solution is None:
                errors.append(f"{record.question.question_id}: solution {solution_id} is missing")
            elif solution.question.content_kind != "solution":
                errors.append(f"{record.question.question_id}: {solution_id} is not a solution")
        for script_id in record.relations.listening_script_ids:
            script = by_id.get(script_id)
            if script is None:
                errors.append(f"{record.question.question_id}: script {script_id} is missing")
            elif script.question.content_kind != "script":
                errors.append(f"{record.question.question_id}: {script_id} is not a script")
    return errors

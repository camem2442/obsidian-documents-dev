"""Store-boundary canonical and relation checks."""
from _scripts_2.apps.exam.exam_core.domain.canonical import canonical_relation_errors, validate_canonical_dict
from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
from _scripts_2.apps.exam.exam_processor.domain.job_relations import relation_errors


def attach_relation_errors(job):
    errors = relation_errors(job)
    if errors:
        job["relation_errors"] = errors
    else:
        job.pop("relation_errors", None)
    return errors


def export_boundary_errors(job, items):
    """Validate approved export items as canonical records with existing relations."""
    errors = relation_errors(job, [item["id"] for item in items])
    records = []
    for item in items:
        record = item_to_canonical(job, item)
        records.append(record)
        errors.extend(
            f"{item['id']}: {message}"
            for message in validate_canonical_dict(record.to_dict())
        )
    errors.extend(canonical_relation_errors(records))
    return errors

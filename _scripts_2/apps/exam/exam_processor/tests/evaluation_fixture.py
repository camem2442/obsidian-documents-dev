"""Explicit synthetic R6 evidence. Never real human judgments."""
import json
from .sample_fixture import create_fixture as sample_fixture, JOB_ID


def create_fixture(root):
    store = sample_fixture(root)
    folder = store.job_dir(JOB_ID)
    (folder / 'evaluation-fixture.json').write_text(json.dumps({'origin': 'synthetic_fixture', 'purpose': 'R6 acceptance only'}))
    review = store.reviews.get(folder, 'q2')
    review.note = 'Synthetic fixture workflow note; not an evaluation label'
    review.audit.update(result='suspected_difference', issues=[{'message': 'Synthetic mismatch', 'original': 'alpha', 'extracted': 'beta', 'suggestion': 'alpha', 'section': 'body'}])
    store.reviews.save(folder, review)
    record = store.records.get(folder, 'q2');record.review.source_audit='suspected_difference';store.records.save(folder, record)
    return store

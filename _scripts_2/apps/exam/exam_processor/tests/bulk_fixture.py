"""R5 isolated fixture with explicit synthetic R4 judgments, never real data."""
from .sample_fixture import create_fixture as sample_fixture, JOB_ID
from ..storage.sample_review import SampleReview


def create_fixture(root):
    store = sample_fixture(root)
    store._review_svc.update_note(store.job_dir(JOB_ID), 'q1', 'preserve existing review note')
    svc = SampleReview(store)
    view = svc.get(JOB_ID)
    view = svc.act(JOB_ID, {'action': 'start', 'count': 2, 'seed': 'r5-explicit-fixture',
                           'revision': view['revision'], 'basis_token': view['basis_token']})
    session = view['sessions'][-1]
    for rid in session['selected']:
        view = svc.act(JOB_ID, {'action': 'verdict', 'verdict': 'pass', 'note': 'Synthetic fixture judgment only',
                               'session_id': session['id'], 'item_id': rid,
                               'revision': view['revision'], 'basis_token': view['basis_token']})
    return store

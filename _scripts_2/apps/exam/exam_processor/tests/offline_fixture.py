"""Explicit synthetic labels/responses, never a real human or model evaluation."""
import uuid

from .evaluation_fixture import create_fixture as base_fixture, JOB_ID
from ..storage.evaluation import Evaluation, SCOPES


def create_fixture(root):
    store = base_fixture(root)
    folder = store.job_dir(JOB_ID)
    review = store.reviews.get(folder, 'q2')
    review.audit['issues'] = [{'section': 'body', 'message': f'synthetic_case_{n}',
                               'original': 'alpha', 'extracted': 'beta'} for n in range(6)]
    store.reviews.save(folder, review)
    svc = Evaluation(store)

    def act(action, **extra):
        view = svc.get(JOB_ID)
        return svc.act(JOB_ID, dict(action=action, revision=view['revision'], basis_token=view['basis_token'],
                                   request_id=uuid.uuid4().hex, **extra))

    item = next(x for x in svc.get(JOB_ID)['items'] if x['id'] == 'q2')
    verdicts = ['actual_error', 'false_positive', 'actual_error', 'false_positive', 'deferred', 'actual_error']
    for n, issue in enumerate(item['issues']):
        act('label', item_id='q2', target='issue', issue_token=issue['token'], verdict=verdicts[n],
            scope=['body', 'source'] if n != 5 else ['answer'], note=f'SYNTHETIC ground truth {n}; HUMAN_NOTE_SECRET', supersedes=None)
    act('label', item_id='q1', target='question', verdict='error_found', scope=list(SCOPES),
        note='SYNTHETIC question judgment, not an issue label', supersedes=None)
    act('dataset', selection=dict(origin='synthetic_fixture', tiers=['GREEN','YELLOW','RED','NOT_READY'],
                                  targets=['question','issue'], full_scope_only=False))
    return store


def response_bundle(run):
    outcomes = {'synthetic_case_0': 'actual_error', 'synthetic_case_1': 'false_positive',
                'synthetic_case_2': 'false_positive', 'synthetic_case_3': 'actual_error'}
    return {'schema': 'offline-responses/1', 'input_manifest_hash': run['plan']['input_manifest_hash'],
            'response_origin': 'synthetic_fixture', 'producer': 'fixed-matrix-fixture/1',
            'responses': [{'case_id': c['id'], 'input_hash': c['input_hash'],
                           'outcome': outcomes[c['input']['issue']['message']], 'reason': 'Fixed synthetic response; no AI call'}
                          for c in run['plan']['cases']]}

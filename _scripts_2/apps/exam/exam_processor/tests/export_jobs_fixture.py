"""Synthetic jobs ready for explicit EX-N01 export; no real source/provider."""
import argparse
from pathlib import Path
import uvicorn
from .batch_jobs_fixture import create_fixture, JOB_IDS, fixture_processor
from ..server import create_app
from ..storage.store import Store


def fixture(root):
    store = create_fixture(root)
    for jid in JOB_IDS:
        folder = store.job_dir(jid)
        for item in store.get(jid)['items']:
            review = store.reviews.get(folder, item['id'])
            review.audit = dict(revision=item['revision'], status='completed', result='no_difference', issues=[])
            store.reviews.save(folder, review)
            store.review(jid, item['id'], item['revision'], 'approve')
    return store


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    root = Path(args.root)
    store = Store(root, root / 'exports') if (root / 'jobs' / JOB_IDS[0] / 'job.json').exists() else fixture(root)
    uvicorn.run(create_app(store, batch_processor=fixture_processor), host='127.0.0.1', port=args.port)

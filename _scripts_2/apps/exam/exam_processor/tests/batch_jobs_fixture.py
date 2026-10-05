"""Two canonical jobs; explicit synthetic provider, never a network call."""
import json
import shutil

from .sample_fixture import create_fixture as base_fixture, JOB_ID
from ..pipeline.ai import process

JOB_IDS = [JOB_ID, 'f5' * 16]


def create_fixture(root):
    store = base_fixture(root)
    first = store.job_dir(JOB_ID)
    for rid in ('q1','q2','q3'):
        rev=store.reviews.get(first,rid);rev.audit=None;store.reviews.save(first,rev)
        rec=store.records.get(first,rid);rec.review.source_audit='pending';store.records.save(first,rec)
    second=store.job_dir(JOB_IDS[1]);shutil.copytree(first,second)
    raw=json.loads((second/'job.json').read_text());raw['id']=JOB_IDS[1];raw['source']='Synthetic second batch job';(second/'job.json').write_text(json.dumps(raw))
    return store


def fixture_processor(store, job_id, item_id, expected, operation, **kwargs):
    return process(store,job_id,item_id,expected,operation,caller=lambda _: {
        'data':{'result':'no_difference','issues':[]},'provider':'synthetic_batch_fixture','configured_model':'fixed_response'},**kwargs)


if __name__ == '__main__':
    import argparse
    import time
    from pathlib import Path
    import uvicorn
    from ..storage.store import Store
    from ..server import create_app
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--delay',type=float,default=.2);parser.add_argument('--fail-once',action='store_true')
    args=parser.parse_args();root=Path(args.root)
    store=Store(root,root/'exports') if (root/'jobs'/JOB_ID/'job.json').exists() else create_fixture(root)
    failed=[False]
    def processor(*pos,**kwargs):
        time.sleep(args.delay)
        if args.fail_once and not failed[0]:failed[0]=True;raise ValueError('SYNTHETIC first-call failure')
        return fixture_processor(*pos,**kwargs)
    uvicorn.run(create_app(store,batch_processor=processor),host='127.0.0.1',port=args.port)

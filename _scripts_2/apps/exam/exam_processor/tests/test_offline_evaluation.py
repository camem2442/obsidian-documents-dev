import copy
import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from _scripts_2.apps.exam.exam_processor.tests.offline_fixture import create_fixture, response_bundle, JOB_ID
from _scripts_2.apps.exam.exam_processor.storage.offline_evaluation import (
    OfflineEvaluation,
    report,
    build_plan,
    make_input,
)
from _scripts_2.apps.exam.exam_processor.storage.evaluation import Evaluation
from _scripts_2.apps.exam.exam_processor.storage.sample_review import digest
from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.server import create_app


class OfflineEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = create_fixture(self.root)
        self.svc = OfflineEvaluation(self.store); self.folder = self.store.job_dir(JOB_ID)
        self.dataset = self.store.runtime.load(self.folder).evaluation['datasets'][0]

    def create_payload(self):
        return dict(action='create', dataset_id=self.dataset['id'], dataset_hash=self.dataset['composition_hash'], request_id=uuid.uuid4().hex)

    def create(self): return self.svc.act(JOB_ID, self.create_payload())

    def payload(self, run, action='import', **extra):
        return dict(action=action, run_id=run['id'], revision=run['revision'], request_id=uuid.uuid4().hex, **extra)

    def files(self):
        return {str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob('*') if p.is_file() and 'evaluation-runs' not in p.parts}

    def test_read_only_empty_and_no_implicit_runs(self):
        before = self.files()
        self.assertEqual(self.svc.list(JOB_ID)['runs'], [])
        self.assertFalse((self.folder/'evaluation-runs').exists())
        self.assertEqual(before, self.files())

    def test_selection_scope_unit_deferred_and_missing_counts(self):
        run = self.create(); r = run['report']
        self.assertEqual(r['eligible'], 4); self.assertEqual(r['counts']['pending'], 4)
        reasons = [x for e in run['plan']['excluded'] for x in e['reasons']]
        for reason in ['deferred','no_explicit_judgment','unsupported_target_question','unsupported_or_unchecked_issue_scope']:
            self.assertIn(reason, reasons)
        self.assertEqual(r['excluded'], 6); self.assertIsNone(r['agreement']['value'])

    def test_input_allowlist_no_label_note_workflow_or_baseline_leak(self):
        d = copy.deepcopy(self.dataset)
        plan = build_plan(d)
        for case in plan['cases']:
            label=copy.deepcopy(case['ground_truth'])
            label['issue_evidence'].update(suggestion='SUGGESTION_SECRET',skipped=True,applied=True)
            allowed,_=make_input(label,d['evidence'][label['basis_hash']])
            text = json.dumps(allowed)
            for value in ['HUMAN_NOTE_SECRET','SUGGESTION_SECRET','ground_truth','verdict','supersedes','action_links','human_approval','review_route','tier','skipped','applied']:
                self.assertNotIn(value,text)
            self.assertTrue(case['input']['files']); self.assertEqual(case['input_hash'],digest(case['input']))
        run = self.create(); inputs = self.svc.inputs(JOB_ID,run['id'])
        self.assertNotIn('ground_truth',json.dumps(inputs)); self.assertNotIn('HUMAN_NOTE_SECRET',json.dumps(inputs))

    def test_fixed_confusion_matrix_and_comparable_baseline(self):
        before=self.files(); queue=self.store.get(JOB_ID)['review_queue_summary']
        run=self.create(); result=self.svc.act(JOB_ID,self.payload(run,bundle=response_bundle(run)))
        r=result['report']
        self.assertEqual(r['matrix'],dict(tp=1,fp=1,tn=1,fn=1))
        self.assertEqual(r['baseline_matrix_same_scored_cases'],dict(tp=2,fp=2,tn=0,fn=0))
        self.assertEqual(r['agreement'],dict(numerator=2,denominator=4,value=.5))
        self.assertEqual(r['label_origin'],'synthetic_fixture')
        self.assertTrue(all(a['usage'] is None and a['cost'] is None and a['provider_model'] is None for a in result['attempts']))
        self.assertEqual(before,self.files());self.assertEqual(queue,self.store.get(JOB_ID)['review_queue_summary'])

    def test_fail_abstain_invalid_are_not_negative_or_zero_accuracy(self):
        run=self.create(); bundle=response_bundle(run)
        bundle['responses'][0]['outcome']='failed';bundle['responses'][1]['outcome']='abstain'
        bundle['responses'][2]['outcome']='unknown';bundle['responses'].pop()
        result=self.svc.act(JOB_ID,self.payload(run,bundle=bundle));r=result['report']
        self.assertEqual(r['counts'],dict(pending=1,failed=1,invalid_response=1,abstain=1,scored=0))
        self.assertIsNone(r['agreement']['value']);self.assertEqual(r['matrix'],dict(tp=0,fp=0,tn=0,fn=0))

    def test_partial_resume_retry_preserves_attempts_and_success(self):
        run=self.create(); b=response_bundle(run);b['responses']=b['responses'][:2];b['responses'][0]['outcome']='failed'
        run=self.svc.act(JOB_ID,self.payload(run,bundle=b));first=run['attempts'][0]
        run=self.svc.act(JOB_ID,self.payload(run,'pause'))
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,self.payload(run,bundle=b))
        run=self.svc.act(JOB_ID,self.payload(run,'resume'))
        rest=response_bundle(run);rest['responses']=[x for x in rest['responses'] if x['case_id']!=b['responses'][1]['case_id']]
        with self.assertRaises(ValueError):self.svc.act(JOB_ID,self.payload(run,bundle=rest))
        run=self.svc.act(JOB_ID,self.payload(run,bundle=rest,retry_reason='explicit fixture retry'))
        self.assertEqual(len(run['attempts']),5);self.assertEqual(run['report']['counts']['scored'],4)
        self.assertEqual(run['attempts'][0],first)
        self.assertTrue(any(a['supersedes_attempt']==first['id'] for a in run['attempts']))
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,self.payload(run,bundle=response_bundle(run)))

    def test_idempotency_stale_request_and_concurrent_writers(self):
        p=self.create_payload();run=self.svc.act(JOB_ID,p)
        self.assertEqual(self.svc.act(JOB_ID,p)['id'],run['id'])
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,dict(p,dataset_hash='wrong'))
        b=response_bundle(run);b['responses']=b['responses'][:1]
        p=self.payload(run,bundle=b);result=self.svc.act(JOB_ID,p)
        self.assertEqual(len(self.svc.act(JOB_ID,p)['attempts']),1)
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,self.payload(run,'pause'))
        other=OfflineEvaluation(Store(self.root,self.root/'exports'))
        def act(svc):
            try:svc.act(JOB_ID,self.payload(result,'pause'));return 'ok'
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(act,[self.svc,other])),['conflict','ok'])

    def test_atomic_failure_and_same_request_retry_after_restart(self):
        p=self.create_payload()
        with patch('_scripts_2.apps.exam.exam_processor.storage.json_io.os.replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.svc.act(JOB_ID,p)
        self.assertEqual(self.svc.list(JOB_ID)['runs'],[])
        run=self.svc.act(JOB_ID,p);path=self.folder/'evaluation-runs'/(run['id']+'.json');before=path.read_bytes()
        p=self.payload(run,bundle=response_bundle(run))
        with patch('_scripts_2.apps.exam.exam_processor.storage.json_io.os.replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.svc.act(JOB_ID,p)
        self.assertEqual(before,path.read_bytes())
        svc=OfflineEvaluation(Store(self.root,self.root/'exports'));saved=svc.act(JOB_ID,p)
        self.assertEqual(saved['report'],svc.get(JOB_ID,run['id'])['report'])
        self.assertEqual(len(svc.act(JOB_ID,p)['attempts']),4)

    def test_frozen_input_current_changes_and_replay_determinism(self):
        r1=self.create();r2=self.create()
        self.assertEqual(r1['plan_hash'],r2['plan_hash']);self.assertNotEqual(r1['id'],r2['id'])
        saved=self.svc.inputs(JOB_ID,r1['id'])
        rec=self.store.records.get(self.folder,'q2');rec.body='CURRENT TEXT MUST NOT REPLACE';self.store.records.save(self.folder,rec)
        (self.folder/'regions/fixture.png').unlink()
        self.assertEqual(saved,self.svc.inputs(JOB_ID,r1['id']))
        self.assertEqual(r1['plan_hash'],self.create()['plan_hash'])
        r1=self.svc.act(JOB_ID,self.payload(r1,bundle=response_bundle(r1)))
        r2=self.svc.act(JOB_ID,self.payload(r2,bundle=response_bundle(r2)))
        self.assertEqual(r1['report']['matrix'],r2['report']['matrix'])
        self.assertEqual(report(r1),r1['report'])

    def test_reject_wrong_hash_unknown_case_duplicates_source_mix(self):
        run=self.create();original=response_bundle(run)
        for field,value in [('input_manifest_hash','wrong'),('response_origin','verified_ai')]:
            b=copy.deepcopy(original);b[field]=value
            with self.assertRaises(ValueError):self.svc.act(JOB_ID,self.payload(run,bundle=b))
        for key,value in [('case_id','unknown'),('input_hash','wrong')]:
            b=copy.deepcopy(original);b['responses'][0][key]=value
            with self.assertRaises(ValueError):self.svc.act(JOB_ID,self.payload(run,bundle=b))
        b=copy.deepcopy(original);b['responses'].append(b['responses'][0])
        with self.assertRaises(ValueError):self.svc.act(JOB_ID,self.payload(run,bundle=b))
        self.assertEqual(self.svc.get(JOB_ID,run['id'])['attempts'],[])
        b=copy.deepcopy(original);b['responses']=b['responses'][:1];run=self.svc.act(JOB_ID,self.payload(run,bundle=b))
        b=copy.deepcopy(original);b['responses']=b['responses'][1:];b['response_origin']='unverified_import'
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,self.payload(run,bundle=b))

    def test_corrupt_run_and_dataset_no_overwrite(self):
        run=self.create();path=self.folder/'evaluation-runs'/(run['id']+'.json')
        raw=json.loads(path.read_text());raw['plan']['cases'][0]['ground_truth']['note']='TAMPERED_NOTE';path.write_text(json.dumps(raw));before=path.read_bytes()
        with self.assertRaises(ValueError):self.svc.get(JOB_ID,run['id'])
        with self.assertRaises(ValueError):self.svc.act(JOB_ID,self.payload(run,'pause'))
        self.assertEqual(before,path.read_bytes())
        runtime=self.store.runtime.load(self.folder);runtime.evaluation['datasets'][0]['labels'][0]['note']='tampered';self.store.runtime.save(self.folder,runtime)
        with self.assertRaises(ValueError):self.create()

    def test_empty_dataset_and_missing_evidence_exclusion(self):
        d=copy.deepcopy(self.dataset);d['labels']=[];d['included']=[]
        plan=build_plan(d);self.assertEqual(plan['cases'],[])
        d=copy.deepcopy(self.dataset);d['evidence']={}
        plan=build_plan(d);self.assertEqual(plan['cases'],[])
        self.assertTrue(any('evidence_integrity' in x['reasons'] for x in plan['excluded']))

    def test_legacy_auth_http_and_no_runtime_mutation(self):
        client=TestClient(create_app(self.store));url=f'/api/jobs/{JOB_ID}/offline-evaluation'
        p=self.create_payload();self.assertEqual(client.post(url,json=p).status_code,403)
        headers={'x-exam-token':client.get('/api/config').json()['token']}
        before=self.files();result=client.post(url,json=p,headers=headers);self.assertEqual(result.status_code,200)
        run=result.json();self.assertEqual(client.get(url+'/'+run['id']+'/inputs').status_code,200)
        with patch('_scripts_2.apps.exam.exam_processor.storage.offline_evaluation.atomic_json_save',side_effect=OSError('fixture')):
            self.assertEqual(client.post(url,headers=headers,json=self.payload(run,'pause')).status_code,500)
        self.assertEqual(before,self.files())
        job=self.store.get(JOB_ID);job['storage_version']=1;(self.folder/'job.json').write_text(json.dumps(job))
        before=self.files();self.assertFalse(client.get(url).json()['available'])
        self.assertEqual(client.post(url,headers=headers,json=self.create_payload()).status_code,409)
        self.assertEqual(before,self.files())

    def test_policy_version_preserves_history_blocks_new_mutation(self):
        run=self.create()
        with patch('_scripts_2.apps.exam.exam_processor.storage.offline_evaluation.POLICY','changed'):
            self.assertEqual(self.svc.get(JOB_ID,run['id'])['id'],run['id'])
            with self.assertRaises(Conflict):self.svc.act(JOB_ID,self.payload(run,'pause'))

    def test_unverified_response_source_never_authenticated_ai(self):
        run=self.create();bundle=response_bundle(run);bundle['response_origin']='unverified_import';bundle['producer']='claimed model - unverified'
        saved=self.svc.act(JOB_ID,self.payload(run,bundle=bundle))
        self.assertEqual(saved['report']['response_source']['origin'],'unverified_import')
        self.assertEqual(saved['report']['label_origin'],'synthetic_fixture')
        self.assertIsNone(saved['attempts'][0]['provider_model'])

    def test_label_basis_and_issue_binding_fail_closed(self):
        for key,value,reason in [('revision_id','wrong','label_basis_mismatch'),('issue_token','wrong','issue_snapshot_mismatch')]:
            d=copy.deepcopy(self.dataset)
            label=next(x for x in d['labels'] if x['target']=='issue' and x['scope']==['body','source'])
            label[key]=value
            plan=build_plan(d)
            self.assertTrue(any(x['id']==label['id'] and reason in x['reasons'] for x in plan['excluded']))

    def test_missing_solution_source_and_unchecked_dependencies(self):
        label=copy.deepcopy(next(x for x in self.dataset['labels'] if x['target']=='issue'))
        evidence=copy.deepcopy(self.dataset['evidence'][label['basis_hash']])
        label['issue_evidence']['section']='solution';label['scope']=['solution']
        self.assertEqual(make_input(label,evidence)[1],['missing_solution_source'])
        label['issue_evidence']['section']='body';label['scope']=['body']
        evidence['materials'][label['item_id']]['parts'].append(evidence['materials']['q1']['parts'][-1])
        self.assertEqual(make_input(label,evidence)[1],['unchecked_dependencies'])

    def test_corrected_ground_truth_does_not_rewrite_prior_run(self):
        run=self.create();old=copy.deepcopy(run['plan']);svc=Evaluation(self.store)
        label=run['plan']['cases'][0]['ground_truth'];v=svc.get(JOB_ID)
        svc.act(JOB_ID,dict(action='label',revision=v['revision'],basis_token=v['basis_token'],request_id=uuid.uuid4().hex,
                           item_id=label['item_id'],target='issue',issue_token=label['issue_token'],scope=label['scope'],
                           verdict='deferred',note='Synthetic correction',supersedes=label['id'],correction_reason='Synthetic uncertainty'))
        self.assertEqual(old,self.svc.get(JOB_ID,run['id'])['plan'])
        self.assertEqual(old,self.create()['plan'])

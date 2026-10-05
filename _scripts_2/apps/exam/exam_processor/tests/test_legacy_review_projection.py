"""Read-only R2 compatibility and the actual approval preflight contract."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.storage.ephemeral_views import attach_legacy_review_projection
from _scripts_2.apps.exam.exam_processor.domain.models import format_output_markdown


class LegacyReviewProjectionTests(unittest.TestCase):
    def job(self):
        item = dict(id='q1', revision='r1', kind='question', number='1', body='Question',
                    solution='', points=3, regions=[], solution_regions=[], assets=[],
                    review='pending', audit=dict(status='completed', revision='r1',
                    result='no_difference', issues=[]), history=[{'body':'previous'}])
        return dict(id='a'*32, source='fixture', track='확률과 통계', format='test',
                    documents={}, warnings=[], items=[item])

    def inventory(self, root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                if p.is_file() else 'directory' for p in root.rglob('*')}

    def test_v1_get_preserves_all_evidence_and_stable_decision_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); job=self.job(); folder=store.job_dir(job['id']); folder.mkdir(parents=True)
            (folder/'job.json').write_text(json.dumps(job)); before=self.inventory(Path(tmp))
            first=store.get(job['id']); second=store.get(job['id'])
            self.assertEqual(before,self.inventory(Path(tmp)))
            self.assertEqual(first['items'][0]['review_decision'],second['items'][0]['review_decision'])
            for k,v in job['items'][0].items(): self.assertEqual(v,first['items'][0][k])
            self.assertEqual(first['review_queue_summary']['pending']['GREEN'],1)
            self.assertFalse(first['items'][0]['approval_gate']['eligible'])
            self.assertTrue(first['items'][0]['approval_gate']['review_eligible'])

    def test_missing_revision_or_invalid_workflow_is_unavailable(self):
        for field in ['revision','review','id']:
            job=self.job(); job['items'][0].pop(field)
            attach_legacy_review_projection(job)
            self.assertEqual(job['review_queue_summary']['status'],'unavailable')
            self.assertNotIn('pending',job['review_queue_summary'])
            self.assertNotIn('review_decision',job['items'][0])

    def test_missing_audit_is_not_ready_and_invalid_result_not_green(self):
        for audit,tier in [(None,'NOT_READY'), ({'status':'completed','revision':'old','result':'no_difference'},'NOT_READY'),
                           ({'status':'completed','revision':'r1'},'RED')]:
            job=self.job(); job['items'][0]['audit']=audit; attach_legacy_review_projection(job)
            self.assertEqual(job['items'][0]['review_decision']['tier'],tier)

    def test_workflow_tiers_and_dependency_unknown_remain_separate(self):
        job=self.job(); base=job['items'][0]
        approved={**copy.deepcopy(base),'id':'q2','review':'approved','body':''}
        held={**copy.deepcopy(base),'id':'q3','review':'held','audit':None}
        unknown={**copy.deepcopy(base),'id':'q4','passage_id':'missing'}
        job['items'] += [approved,held,unknown]; attach_legacy_review_projection(job)
        s=job['review_queue_summary']
        self.assertEqual(s['pending'],dict(NOT_READY=1,RED=0,YELLOW=0,GREEN=1))
        self.assertEqual(s['approved']['by_tier']['RED'],1)
        self.assertEqual(s['held']['by_tier']['NOT_READY'],1)

    def test_v2_embedded_runtime_get_does_not_migrate_or_persist_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); job=self.job(); store.save(job); folder=store.job_dir(job['id'])
            manifest=json.loads((folder/'job.json').read_text()); manifest['task']={'status':'completed'}
            (folder/'job.json').write_text(json.dumps(manifest)); before=self.inventory(Path(tmp))
            view=store.get(job['id']); self.assertEqual(view['task'],manifest['task'])
            self.assertTrue(view['items'][0]['approval_gate']['eligible'])
            self.assertEqual(before,self.inventory(Path(tmp)))
            self.assertFalse((folder/'runtime.json').exists())
            store.save(view)
            self.assertNotIn('review_queue_summary',json.loads((folder/'job.json').read_text()))

    def test_preflight_reuses_structural_job_and_note_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); job=self.job(); job['warnings']=['Job warning']; job['items'][0]['body']=''
            store.save(job); gate=store.get(job['id'])['items'][0]['approval_gate']
            self.assertFalse(gate['eligible']); self.assertIn('Job warning',gate['blockers'])
            self.assertIn('본문이 비어 있습니다.',gate['blockers'])
            job=self.job(); job['items'][0]['audit']['result']='suspected_difference'
            job['items'][0]['audit']['issues']=[{'message':'check'}]; store.save(job)
            self.assertFalse(store.get(job['id'])['items'][0]['approval_gate']['eligible'])
            job['items'][0]['note']='checked against source'; store.save(job)
            self.assertTrue(store.get(job['id'])['items'][0]['approval_gate']['eligible'])

    def test_inline_math_output_preserves_tex_and_code(self):
        cases=json.loads((Path(__file__).parent/'fixtures/math_preview_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']):
                self.assertEqual(format_output_markdown(case['source'],'수학'),case['output'])

"""New-source acceptance after direct transition to Exam Core (fixed AI, no network)."""
import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.storage.input_jobs import InputJobs
from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs
from _scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture import source_pdf, processor


class CoreCutoverFlowTests(unittest.TestCase):
    def test_new_pdf_native_extract_audit_approve_export_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.pdf'
            source_pdf(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            store = Store(root / 'data', root / 'exports')
            inputs = InputJobs(store)
            units = [dict(id='pdf', path=str(source), format='kice_korean',
                          source='Core cutover synthetic source', track='화법과 작문', pages='1')]
            preview = inputs.preview(units)
            plan = inputs.act(dict(action='plan', request_id=uuid.uuid4().hex,
                                   inputs=units, preview_hash=preview['plan_hash']))
            inputs.act(dict(action='execute', request_id=uuid.uuid4().hex,
                            plan_id=plan['id'], revision=plan['revision'],
                            confirmed_plan_hash=plan['plan_hash']))
            inputs.run(plan['id'])
            result = inputs.get(plan['id'])
            self.assertEqual(result['counts']['completed'], 1)
            job_id = result['entries'][0]['job_id']
            initial = store.get(job_id)['items']
            self.assertEqual(len(initial), 2)
            bodies = {item['id']: item['body'] for item in initial}
            self.assertTrue(any('Synthetic first question?' in body for body in bodies.values()))
            with self.assertRaises(ValueError):
                store.export(job_id)

            batches = BatchJobs(store, processor)
            # The PDF text parser already transcribed this source; do not force a second extraction.
            extract_preview = batches.preview([job_id], 'extract')
            self.assertEqual(extract_preview['plan']['planned_calls'], 0)
            self.assertTrue(all('missing_source_or_existing_transcription' in e['reasons']
                                for e in extract_preview['plan']['excluded']))
            for operation in ('audit',):
                preview = batches.preview([job_id], operation)
                self.assertEqual(preview['plan']['planned_calls'], 2, (operation, preview['plan']['excluded']))
                batch = batches.act(dict(action='plan', request_id=uuid.uuid4().hex,
                                         job_ids=[job_id], operation=operation,
                                         preview_hash=preview['plan_hash']))
                batches.act(dict(action='execute', request_id=uuid.uuid4().hex,
                                 batch_id=batch['id'], revision=batch['revision'],
                                 confirmed_plan_hash=batch['plan_hash']))
                batches.run(batch['id'])
                self.assertEqual(batches.get(batch['id'])['counts']['completed'], 2)
            job = store.get(job_id)
            self.assertTrue(all(item['review'] == 'pending' for item in job['items']))
            for item in job['items']:
                store.review(job_id, item['id'], item['revision'], 'approve')
            result = store.export(job_id, source_code='core-fixture')
            output = Path(result['last_export'])
            self.assertTrue(output.is_dir())
            notes = list(output.rglob('*.md'))
            self.assertGreaterEqual(len(notes), 2)
            self.assertTrue(any('Synthetic first question?' in p.read_text() for p in notes))
            reloaded = Store(root / 'data', root / 'exports')
            for item in reloaded.get(job_id)['items']:
                record = reloaded.records.get(reloaded.job_dir(job_id), item['id'])
                self.assertIs(type(record), CanonicalQuestionRecord)
                self.assertEqual(record.review.human_approval, 'approved')
                self.assertEqual(record.body, bodies[item['id']])
            self.assertFalse((store.root / 'library/kice/records').exists())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

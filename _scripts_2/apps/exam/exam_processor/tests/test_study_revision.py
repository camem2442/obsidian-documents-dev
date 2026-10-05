from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.ingestion.study_service import StudyIngestion
from _scripts_2.apps.exam.exam_processor.ingestion.study_revision import StudyRevision
from _scripts_2.apps.exam.exam_processor.storage.revision_repository import RevisionRepository
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class StudyRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'vault'
        self.root.mkdir()
        self.source = self.root / 'source.md'
        self.source.write_text('\t1. One?\n\t\t○ A\n\t2. Two?\n\t\t○ B\n\t3. Three?\n\t\t○ C\n')
        self.service = StudyIngestion(self.root, self.base / 'data')
        self.job = self.service.ingest(self.source, adapter='numbered-qa-v1', subject='logic', unit=['u'], question_type='essay')
        self.revision = StudyRevision(self.service)
        # Model genuine pre-existing review data to verify invalidation/preservation.
        for record in self.service.select(self.job):
            record.review.human_approval = 'approved'
            record.review.source_audit = 'no_difference'
            self.service.records.save(self.job, record)
            state = self.service.reviews.get(self.job, record.question.question_id)
            state.audit = {'status': 'completed', 'revision': record.provenance.revision_id}
            self.service.reviews.save(self.job, state)
        self.old = self.service.select(self.job)

    def changed(self):
        self.source.write_text('\t1. One?\n\t\t○ Revised answer\n\t2. Two?\n\t\t○ B\n\t4. Four?\n\t\t○ D\n')
        return self.revision.plan(self.job)

    def test_plan_read_only_and_apply_change_add_delete_replay(self):
        before = inventory(self.service.data_root)
        plan = self.changed()
        self.assertEqual(before, inventory(self.service.data_root))
        self.assertEqual([c['kind'] for c in plan['changes']], ['changed','unchanged','deleted','added'])
        self.assertEqual(self.revision.apply(plan)['status'], 'applied')
        records = self.service.select(self.job)
        self.assertEqual([r.question.question_number for r in records], ['1', '2', '4'])
        self.assertEqual(records[0].question.question_id, self.old[0].question.question_id)
        self.assertNotEqual(records[0].provenance.revision_id, self.old[0].provenance.revision_id)
        self.assertEqual(records[0].review.human_approval, 'pending')
        self.assertEqual(records[0].review.source_audit, 'pending')
        audit = self.service.reviews.get(self.job, records[0].question.question_id).audit
        self.assertEqual(audit['revision'], self.old[0].provenance.revision_id)
        self.assertNotEqual(audit['revision'], records[0].provenance.revision_id)
        self.assertEqual(records[1].to_dict(), self.old[1].to_dict())
        self.assertEqual((self.job/'review'/f'{records[1].question.question_id}.json').read_bytes(),
                         before[f'ingestion/jobs/{self.job.name}/review/{records[1].question.question_id}.json'])
        for old in (self.old[0], self.old[2]):
            restored, state = RevisionRepository().get_snapshot(self.job, old.question.question_id, old.provenance.revision_id)
            self.assertEqual(restored.to_dict(), old.to_dict())
            self.assertIsNotNone(state.audit)
            self.assertEqual(self.service.trace(restored, historical=True)['evidence'], 'archived_source_bytes')
        self.assertEqual(self.service.trace_current(self.job, records[1].question.question_id)['evidence'], 'current_source_observation')
        with self.assertRaises(ValueError):
            self.service.trace(records[1])  # old provenance must not masquerade as latest source
        stored = inventory(self.service.data_root)
        self.assertEqual(self.revision.apply(plan)['status'], 'already_applied')
        self.assertEqual(stored, inventory(self.service.data_root))
        self.assertEqual(self.service.ingest(self.source, adapter='numbered-qa-v1', subject='logic', unit=['u'], question_type='essay'), self.job)
        self.source.unlink()
        selected = self.service.select(self.job, ids=[records[2].question.question_id, records[0].question.question_id], subject='logic', unit='u', question_type='essay')
        self.assertIn('Four?', project_to_markdown(selected[0]))
        self.assertIn('Revised answer', project_to_markdown(selected[1]))
        with self.assertRaises(ValueError):
            self.service.select(self.job, ids=[self.old[2].question.question_id])

    def test_changed_body_requires_explicit_confirmation(self):
        self.source.write_text(self.source.read_text().replace('One?', 'One revised?'))
        plan = self.revision.plan(self.job)
        before = inventory(self.service.data_root)
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.revision.apply(plan)
        self.assertEqual(before, inventory(self.service.data_root))
        self.revision.apply(plan, confirmed_same_items={'1':'Reviewed wording correction; same exercise'})
        self.assertEqual(self.service.select(self.job)[0].question.question_id, self.old[0].question.question_id)

    def test_renumbering_and_swap_blocked_even_with_confirmation(self):
        original = self.source.read_text()
        for value in (original.replace('1. One?', '9. One?'), original.replace('One?', 'TEMP').replace('Two?', 'One?').replace('TEMP', 'Two?')):
            self.source.write_text(value)
            plan = self.revision.plan(self.job)
            with self.assertRaisesRegex(ValueError, 'renumbering'):
                self.revision.apply(plan, confirmed_same_items={'1':'same', '2':'same'})

    def test_retired_key_cannot_be_reused(self):
        self.revision.apply(self.changed())
        self.source.write_text(self.source.read_text()+'\t3. New use?\n\t\t○ New\n')
        with self.assertRaisesRegex(ValueError, 'retired key'):
            self.revision.apply(self.revision.plan(self.job))

    def test_stale_source_storage_review_and_edited_plan(self):
        plan = self.changed()
        original = self.source.read_bytes()
        self.source.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.revision.apply(plan)
        self.source.write_bytes(original)
        state = self.service.reviews.get(self.job, self.old[0].question.question_id)
        state.note = 'New human note'
        self.service.reviews.save(self.job, state)
        with self.assertRaisesRegex(ValueError, 'Stored state changed'):
            self.revision.apply(plan)
        plan = self.revision.plan(self.job)
        plan['changes'][0]['after']['solution'] = 'injected'
        with self.assertRaisesRegex(ValueError, 'edited plan'):
            self.revision.apply(plan)

    def test_option_changes_are_separate_and_rejected(self):
        options = dict(adapter='numbered-table-v1', subject='math', unit=['new'], question_type='other')
        plan = self.revision.plan(self.job, options=options)
        self.assertEqual(set(plan['options_changed']), set(options))
        with self.assertRaisesRegex(ValueError, 'option change'):
            self.revision.apply(plan)

    def test_failure_rolls_back_all_records_artifact_and_snapshots(self):
        plan = self.changed()
        before = inventory(self.service.data_root)
        with patch.object(self.service.artifacts, 'register_or_update', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.revision.apply(plan)
        self.assertEqual(before, inventory(self.service.data_root))
        self.assertEqual(self.revision.apply(plan)['status'], 'applied')

    def test_pending_transaction_blocks_read_plan_then_recovers(self):
        plan = self.changed()
        before = inventory(self.service.data_root)
        # Simulate process death by disabling exception rollback, then recover in a fresh service.
        with patch.object(self.revision, '_recover', side_effect=RuntimeError('process gone')):
            with patch.object(self.service.artifacts, 'register_or_update', side_effect=OSError('interrupted')):
                with self.assertRaises(RuntimeError):
                    self.revision.apply(plan)
        with self.assertRaisesRegex(ValueError, 'recover'):
            self.service.select(self.job)
        with self.assertRaisesRegex(ValueError, 'recover'):
            self.revision.plan(self.job)
        StudyRevision(self.service).recover(self.job)
        self.assertEqual(before, inventory(self.service.data_root))
        self.revision.apply(plan)

    def test_second_revision_and_stale_replay(self):
        first = self.changed()
        self.revision.apply(first)
        self.source.write_text(self.source.read_text().replace('Revised answer', 'Second answer'))
        second = self.revision.plan(self.job)
        self.revision.apply(second)
        self.assertEqual(len(list((self.job/'revisions'/self.old[0].question.question_id).glob('*.json'))),2)
        with self.assertRaises(ValueError):
            self.revision.apply(first)
        self.assertEqual(self.revision.apply(second)['status'],'already_applied')

    def test_empty_source_can_retire_all_and_noop_does_not_write(self):
        plan = self.revision.plan(self.job)
        before = inventory(self.service.data_root)
        self.assertEqual(self.revision.apply(plan)['status'], 'unchanged')
        self.assertEqual(before, inventory(self.service.data_root))
        self.source.write_text('')
        self.revision.apply(self.revision.plan(self.job))
        self.assertEqual(self.service.select(self.job), [])

    def test_stale_content_revision_rejected(self):
        plan = self.changed()
        record = self.old[1]
        self.revision.records.update_content(self.job, record.question.question_id,
                                             record.provenance.revision_id, {'body':'Local edit'})
        with self.assertRaisesRegex(ValueError, 'Stored state changed'):
            self.revision.apply(plan)

    def test_replay_after_review_edit_rejected(self):
        plan = self.changed()
        self.revision.apply(plan)
        record = self.service.select(self.job)[0]
        state = self.service.reviews.get(self.job, record.question.question_id)
        state.note = 'Reviewed after apply'
        self.service.reviews.save(self.job, state)
        with self.assertRaisesRegex(ValueError, 'Stored state changed after'):
            self.revision.apply(plan)

    def test_committed_transaction_recovery_does_not_roll_back(self):
        plan = self.changed()
        with patch.object(self.revision, '_recover', side_effect=RuntimeError('process ended after commit')):
            with self.assertRaises(RuntimeError):
                self.revision.apply(plan)
        self.revision.recover(self.job)
        self.assertEqual(self.revision.apply(plan)['status'], 'already_applied')
        self.assertEqual(self.service.select(self.job)[0].solution, 'Revised answer\n')

    def test_source_changes_during_apply_roll_back_storage(self):
        plan = self.changed()
        before = inventory(self.service.data_root)
        save = self.service.save_source_snapshot
        def race(job, raw):
            save(job, raw)
            self.source.write_bytes(raw+b'\n')
        with patch.object(self.service, 'save_source_snapshot', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'Source changed during'):
                self.revision.apply(plan)
        self.assertEqual(before, inventory(self.service.data_root))

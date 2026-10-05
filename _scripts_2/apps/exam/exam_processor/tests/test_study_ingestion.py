import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.ingestion.study_markdown import parse_study_markdown
from _scripts_2.apps.exam.exam_processor.ingestion.study_service import StudyIngestion
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown


class StudyIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'vault'
        self.root.mkdir()
        self.service = StudyIngestion(self.root, base / 'data')
        self.source = self.root / '연습.md'
        self.source.write_bytes('\t1. 첫 질문?\r\n\t\t○ 답 하나\r\n\t\t○ 답 둘\r\n\t2. 다음?\r\n\t\t○ 둘째\r\n'.encode())

    def ingest(self, **kwargs):
        return self.service.ingest(self.source, adapter='numbered-qa-v1', subject='철학',
                                   unit=['인식론'], question_type=kwargs.get('question_type', '서술형'))

    def test_trace_idempotency_and_source_preservation(self):
        before = self.source.read_bytes()
        job = self.ingest()
        files = {p.relative_to(job): p.read_bytes() for p in job.rglob('*') if p.is_file()}
        self.assertEqual(job, self.ingest())
        self.assertEqual(files, {p.relative_to(job): p.read_bytes() for p in job.rglob('*') if p.is_file()})
        records = self.service.select(job)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].body, '첫 질문?')
        self.assertEqual(records[0].solution, '답 하나\r\n답 둘\r\n')
        self.assertEqual(self.service.trace(records[0])['excerpt'], before.decode().split('\t2.')[0])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(records[0].review.human_approval, 'pending')
        self.assertEqual(records[0].review.source_audit, 'pending')
        self.assertIsNone(records[0].origin)

    def test_canonical_selection_export_without_source(self):
        job = self.ingest()
        ids = [r.question.question_id for r in self.service.select(job)]
        self.source.unlink()
        with patch(
                '_scripts_2.apps.exam.exam_processor.ingestion.study_service.parse_study_markdown',
                side_effect=AssertionError('No reparse')):
            records = self.service.select(job, ids=ids[::-1], subject='철학', unit='인식론', question_type='서술형')
            self.assertEqual([r.question.question_id for r in records], ids[::-1])
            self.assertIn('다음?', project_to_markdown(records[0]))
            self.assertNotIn('content_type: original', project_to_markdown(records[0]))
            self.assertEqual(self.service.select(job, subject='경제'), [])
        with self.assertRaises(ValueError):
            self.service.select(job, ids=[ids[0], ids[0]])
        with self.assertRaises(ValueError):
            self.service.select(job, ids=['unknown'])

    def test_changed_source_or_options_cannot_overwrite(self):
        job = self.ingest()
        before = {p.name: p.read_bytes() for p in job.rglob('*.json')}
        with self.assertRaisesRegex(ValueError, 'options changed'):
            self.ingest(question_type='changed')
        record = self.service.select(job)[0]
        self.source.write_text('\t1. 수정?\n\t\t○ 답\n')
        with self.assertRaisesRegex(ValueError, 'options changed'):
            self.ingest()
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.service.trace(record)
        self.assertEqual(before, {p.name: p.read_bytes() for p in job.rglob('*.json')})

    def test_table_preserves_math_html_spaces(self):
        text = '|   |   |   |\n|---|---|---|\n|1|∀x P(x)<br>∴ Q  |3. 증명<br><br>끝|\n'
        self.source.write_text(text)
        job = self.service.ingest(self.source, adapter='numbered-table-v1', subject='논리학', unit=[], question_type='증명')
        record, = self.service.select(job)
        self.assertEqual(record.body, '∀x P(x)<br>∴ Q  ')
        self.assertEqual(record.solution, '3. 증명<br><br>끝')
        self.assertEqual(self.service.trace(record)['excerpt'], text.splitlines(keepends=True)[2])

    def test_ambiguous_unsupported_input_rejected_before_storage(self):
        for text in ('\t1. Q\n', '\t1. Q\n\t\t○ A\n\t1. Q\n\t\t○ A\n',
                     '\t1. Q\n\t\t○ ![[x.png]]\n', '## Q\nanswer', '\t1. Q\ntext'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_study_markdown(text, 'numbered-qa-v1')
        with self.assertRaises(ValueError):
            parse_study_markdown('|1|a|b|extra|\n', 'numbered-table-v1')
        self.assertFalse(self.service.data_root.exists())

    def test_disjoint_storage_and_outside_source(self):
        with self.assertRaises(ValueError):
            StudyIngestion(self.root, self.root / 'output')
        with self.assertRaises(ValueError):
            StudyIngestion(self.root, self.root.parent)
        outside = self.root.parent / 'outside.md'
        outside.write_text('\t1. Q\n\t\t○ A\n')
        with self.assertRaises(ValueError):
            self.service.ingest(outside, adapter='numbered-qa-v1', subject='x', unit=[], question_type='y')

    def test_existing_artifact_identity_is_reused(self):
        from hashlib import sha256
        art = self.service.artifacts.register_or_update('연습.md', sha256(self.source.read_bytes()).hexdigest(), self.source.stat().st_size)
        job = self.ingest()
        self.assertEqual(self.service.select(job)[0].source.source_id, art.artifact_id)
        self.assertEqual(len(self.service.artifacts.list_all()), 1)

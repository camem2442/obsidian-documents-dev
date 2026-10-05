import copy
import json
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.domain.standard_content import (
    workbook_content,
    populate_standard_metadata,
)
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import (
    project_to_markdown,
    preview_sections,
)
from _scripts_2.apps.exam.exam_processor.storage.record_repository import RecordRepository


class StandardContentTests(unittest.TestCase):
    def make(self, **values):
        job = {'id': 'j', 'source': '한완기 테스트', 'format': 'hanwangi_2026_probability', 'workbook': {'id': 'w'}}
        item = {'id': 'q', 'number': 'A1·01', 'kind': 'question', 'revision': 'r',
                'body': 'A1·01 | 2018.9·가, 나 22번 |\n\n${}_7\n\n\\mathrm{P}_3$의 값 [3점]',
                'solution': '**A1·01** | 2018.9·가, 나 22번 |\n\n**교과서적 해법**\n풀이\n\n<span class="source-inline-box">정답</span> 210'}
        item.update(values)
        return item_to_canonical(job, item)

    def test_roundtrip_raw_evidence_metadata_and_common_preview(self):
        rec = self.make()
        original = copy.deepcopy(rec.to_dict())
        self.assertEqual(rec.question.answer, '210')
        self.assertEqual(rec.origin.authority, 'KICE')
        self.assertEqual(len(rec.origin.variants), 2)
        self.assertIn('source-inline-box', rec.solution)
        with tempfile.TemporaryDirectory() as tmp:
            repo = RecordRepository()
            repo.save(Path(tmp), rec)
            reread = repo.get(Path(tmp), 'q')
            self.assertEqual(reread.to_dict(), original)
            md = project_to_markdown(reread)
        self.assertEqual(md.count('**정답: 210**'), 1)
        self.assertNotIn('source-inline-box', md)
        self.assertNotIn('교과서적 해법', md)
        self.assertIn('평가원 가형 22번 · 나형 22번', md)
        view = preview_sections(rec)
        self.assertIn(view['solution'], md)
        self.assertIn('${}_7 \\mathrm{P}_3$', md)
        self.assertEqual(rec.to_dict(), original)
        populate_standard_metadata(rec)
        self.assertEqual(rec.to_dict(), original)

    def test_conflicts_preserve_raw_and_block_output(self):
        for change in ({'answer': '209'}, {'solution': self.make().solution + '\n**정답: ③**'},
                       {'origin': {'type': 'kice', 'authority': 'KICE', 'academic_year': '2019'}}):
            rec = self.make(**change)
            self.assertTrue(workbook_content(rec)['warnings'])
            self.assertEqual(preview_sections(rec)['status'], 'unavailable')
            with self.assertRaises(ValueError):
                project_to_markdown(rec)

    def test_unknown_box_and_conditions_not_deleted(self):
        rec = self.make(body='조건 <span class="source-inline-box">x > 0</span>', solution='풀이의 값은 210')
        self.assertIsNone(rec.question.answer)
        self.assertIsNone(rec.origin)
        self.assertIn('x > 0', project_to_markdown(rec))
        rec = self.make(solution='<span class="source-inline-box">정답</span> 판단 불가')
        self.assertTrue(workbook_content(rec)['warnings'])

    def test_duplicates_variant_and_other_formats(self):
        rec = self.make(solution='풀이\n**정답: ④**\n**정답: ④**', content_type='variant')
        self.assertEqual(project_to_markdown(rec).count('**정답: ④**'), 1)
        self.assertIn('변형:', project_to_markdown(rec))
        boxed = self.make(solution='<span class="source-inline-box">정답</span> <span class="source-inline-box">④</span>')
        self.assertEqual(boxed.question.answer, '④')
        self.assertNotIn('source-inline-box', project_to_markdown(boxed))
        rec.source.format_id = 'kice_english'
        rec.body = 'Read the passage.\n① one ② two'
        rec.solution = 'Explanation'
        rec.question.answer = '②'
        md = project_to_markdown(rec, passage_file='../passages/p.md', script_files=['../scripts/01.md'])
        self.assertIn('![[../passages/p.md]]', md)
        self.assertIn('![[../scripts/01.md]]', md)
        self.assertIn('① one ② two', md)
        self.assertEqual(CanonicalQuestionRecord.from_dict(json.loads(rec.to_json())).body, rec.body)

    def test_english_answer_conflict_and_preview_dependency_links(self):
        from _scripts_2.apps.exam.exam_processor.storage.ephemeral_views import attach_display_titles
        job = {'id': 'j', 'format': 'kice_english', 'source': '2027학년도 9월 모의평가 영어', 'items': [
            {'id': 'q', 'kind': 'question', 'number': '41', 'body': 'Question', 'solution': '**정답: ①**',
             'answer': '②', 'passage_id': 'p', 'listening_script_id': 's'},
            {'id': 'p', 'kind': 'passage', 'number': '41-42', 'body': 'Passage'},
            {'id': 's', 'kind': 'script', 'number': '1', 'body': 'Script text'}]}
        attach_display_titles(job)
        self.assertEqual(job['items'][0]['standard_preview']['status'], 'unavailable')
        job['items'][0]['answer'] = '①'
        attach_display_titles(job)
        view = job['items'][0]['standard_preview']
        self.assertIn('![[../passages/41-42.md]]', view['body'])
        self.assertIn('![[../scripts/01.md]]', view['solution'])
        self.assertIn('Script text', view['solution'])
        self.assertEqual(view['solution'].count('**정답: ①**'), 1)

    def test_draft_preview_is_read_only_and_revision_checked(self):
        import hashlib
        from fastapi.testclient import TestClient
        from _scripts_2.apps.exam.exam_processor.server import create_app
        from _scripts_2.apps.exam.exam_processor.storage.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'data', root / 'exports')
            rec = self.make()
            job = {'id': 'a' * 32, 'format': rec.source.format_id, 'workbook': {'id': 'w'}, 'source': rec.source.title,
                   'warnings': [], 'items': [{'id': 'q', 'kind': 'question', 'number': 'A1·01',
                   'revision': 'r', 'review': 'pending', 'body': rec.body, 'solution': rec.solution, 'answer': '',
                   'warnings': [], 'history': []}]}
            folder = store.job_dir('a' * 32); folder.mkdir(parents=True)
            (folder / 'job.json').write_text(json.dumps(job))
            client = TestClient(create_app(store))
            headers = {'x-exam-token': client.get('/api/config').json()['token']}
            def hashes():
                return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            before = hashes()
            endpoint = '/api/jobs/' + 'a' * 32 + '/items/q/preview'
            response = client.post(endpoint, headers=headers, json={'revision': 'r', 'values': {'body': rec.body+'\nDraft text'}})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Draft text', response.json()['body'])
            response = client.post(endpoint, headers=headers, json={'revision': 'r', 'values': {'answer': '209'}})
            self.assertEqual(response.json()['status'], 'unavailable')
            self.assertEqual(client.post(endpoint, headers=headers, json={'revision': 'old'}).status_code, 409)
            self.assertEqual(client.post(endpoint, json={'revision': 'r'}).status_code, 403)
            self.assertEqual(hashes(), before)

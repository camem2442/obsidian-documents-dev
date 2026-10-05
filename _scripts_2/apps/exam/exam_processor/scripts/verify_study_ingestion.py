"""Real-source acceptance; output is isolated outside the vault, never production jobs."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.ingestion.study_service import StudyIngestion
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown

SAMPLES = [
    ('공부/1 인문/1 철학/2 분석철학/6 인식론/2 토대론/연습문제.md',
     'numbered-qa-v1', '인식론', '서술형', 10),
    ('공부/1 인문/1 철학/2 분석철학/2 논리학개론/3 술어 논리/5 자연 연역/연습문제.md',
     'numbered-table-v1', '논리학', '증명', 10),
]


def snapshot(directories):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in directories for p in root.rglob('*') if p.is_file()}


def verify(repo_root, output_root):
    service = StudyIngestion(repo_root, output_root / 'data')
    sources = [repo_root / entry[0] for entry in SAMPLES]
    before = snapshot({p.parent for p in sources})
    results = []
    for (relative, adapter, subject, kind, count), source in zip(SAMPLES, sources):
        raw = source.read_bytes()
        text = raw.decode('utf-8')
        job = service.ingest(source, adapter=adapter, subject=subject, unit=[subject], question_type=kind)
        records = service.select(job)
        assert len(records) == count, (relative, len(records), count)
        # Independent field comparison, not a second call to the adapter.
        if adapter == 'numbered-table-v1':
            expected = [line.split('|')[2:4] for line in text.splitlines()
                        if line.startswith('|') and line.split('|')[1].isdigit()]
        else:
            expected = []
            for line in text.splitlines(keepends=True):
                if line.startswith('\t\t○ '):
                    expected[-1][1] += line[len('\t\t○ '):]
                elif line.startswith('\t') and '. ' in line:
                    expected.append([line.split('. ', 1)[1].rstrip('\r\n'), ''])
        assert [[r.body, r.solution] for r in records] == expected
        for record in records:
            trace = service.trace(record)
            assert trace['sha256'] == hashlib.sha256(raw).hexdigest()
            assert Path(trace['source_path']) == source.resolve()
            assert record.review.source_audit == 'pending' and record.review.human_approval == 'pending'
        stored_before = snapshot([job])
        assert service.ingest(source, adapter=adapter, subject=subject, unit=[subject], question_type=kind) == job
        assert snapshot([job]) == stored_before
        ids = [r.question.question_id for r in records]
        original_read = Path.read_bytes
        def no_source_read(path):
            if path in sources:
                raise AssertionError('Exporter must not read sources')
            return original_read(path)
        with patch('pathlib.Path.read_bytes', no_source_read), patch(
                '_scripts_2.apps.exam.exam_processor.ingestion.study_service.parse_study_markdown',
                side_effect=AssertionError('Exporter must not reparse')):
            selected = service.select(job, ids=ids[::-1], subject=subject, unit=subject,
                                      source_id=records[0].source.source_id, question_type=kind)
            assert [r.question.question_id for r in selected] == ids[::-1]
            assert not service.select(job, subject='not-this-subject')
            rendered = '\n\n'.join(project_to_markdown(r) for r in selected)
            assert rendered.count('<!-- SECTION:PROBLEM_START -->') == count
            (output_root / f'{adapter}.md').write_text(rendered, encoding='utf-8')
        results.append(dict(source=relative, adapter=adapter, count=count, source_sha256=hashlib.sha256(raw).hexdigest(),
                            job=str(job), example_id=ids[0], artifact_id=records[0].source.source_id,
                            content_comparison='all body/solution fields equal source',
                            idempotent=True, source_trace=True, export_without_source_reads=True))
    after = snapshot({p.parent for p in sources})
    assert before == after
    assert len(service.artifacts.list_all()) == 2
    report = dict(samples=results, preserved_files=len(before), source_inventory_unchanged=True,
                  canonical_records=sum(r['count'] for r in results),
                  limitations=['No original textbook/PDF fidelity claim', 'No UI or PDF rendering acceptance',
                               'No production library promotion or legacy migration'])
    (output_root / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    verify(args.repo_root.resolve(), args.output_root.resolve())

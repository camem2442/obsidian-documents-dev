"""Real-source revision acceptance on isolated copies only."""
import argparse
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.scripts.verify_study_ingestion import SAMPLES, snapshot
from _scripts_2.apps.exam.exam_processor.ingestion.study_service import StudyIngestion, digest
from _scripts_2.apps.exam.exam_processor.ingestion.study_revision import StudyRevision
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown


def must_reject(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError('Expected rejection')


def modified_source(text, adapter):
    """Change explicit fields using source grammar independently of the ingestion parser."""
    if adapter == 'numbered-qa-v1':
        blocks = []
        for line in text.splitlines(keepends=True):
            if line.startswith('\t') and not line.startswith('\t\t') and '. ' in line:
                blocks.append(line)
            elif blocks:
                blocks[-1] += line
        blocks[0] = blocks[0].replace('?', '? [revision acceptance wording]', 1)
        blocks[1] = blocks[1].replace('○ ', '○ [revision acceptance answer] ', 1)
        return ''.join(blocks[:-1]) + '\t11. 추가된 격리 검증 문항?\n\t\t○ 격리 검증 답안\n'
    lines = text.splitlines(keepends=True)
    result = []
    for line in lines:
        cells = line.rstrip('\r\n').split('|')
        if len(cells) == 5 and cells[1] == '1':
            cells[2] += ' [revision acceptance wording]'
            line = '|'.join(cells)+'\n'
        elif len(cells) == 5 and cells[1] == '2':
            cells[3] += ' [revision acceptance answer]'
            line = '|'.join(cells)+'\n'
        elif len(cells) == 5 and cells[1] == '10':
            continue
        result.append(line)
    return ''.join(result)+'|11|추가된 격리 검증 문항?|격리 검증 답안|\n'


def verify(repo_root, output_root):
    if output_root.is_relative_to(repo_root) or repo_root.is_relative_to(output_root):
        raise ValueError('Acceptance output must be disjoint from original vault')
    sources = [repo_root / sample[0] for sample in SAMPLES]
    before = snapshot({p.parent for p in sources})
    output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='revision-', dir=output_root))
    reports = []
    for sample, original in zip(SAMPLES, sources):
        relative, adapter, subject, kind, count = sample
        base = run / adapter
        copied = base / 'vault' / original.name
        copied.parent.mkdir(parents=True)
        raw = original.read_bytes()
        copied.write_bytes(raw)
        service = StudyIngestion(copied.parent, base / 'data')
        revision = StudyRevision(service)
        job = service.ingest(copied, adapter=adapter, subject=subject, unit=[subject], question_type=kind)
        old = service.select(job)
        assert len(old) == count
        for record in old:
            record.review.human_approval = 'approved'
            record.review.source_audit = 'no_difference'
            service.records.save(job, record)
            state = service.reviews.get(job, record.question.question_id)
            state.audit = {'revision': record.provenance.revision_id, 'status':'completed'}
            service.reviews.save(job, state)
        old = service.select(job)
        old_reviews = {r.question.question_id: (job/'review'/f'{r.question.question_id}.json').read_bytes() for r in old}
        new_text = modified_source(raw.decode(), adapter)
        assert new_text != raw.decode()
        copied.write_text(new_text)
        stored_before = snapshot([base/'data'])
        plan = revision.plan(job)
        assert snapshot([base/'data']) == stored_before
        kinds = {c['key']:c['kind'] for c in plan['changes']}
        assert kinds['1'] == kinds['2'] == 'changed' and kinds['10'] == 'deleted' and kinds['11'] == 'added'
        assert all(kinds[str(n)] == 'unchanged' for n in range(3,10))
        must_reject(lambda: revision.apply(plan))
        assert snapshot([base/'data']) == stored_before
        copied.write_text(new_text+'\n')
        must_reject(lambda: revision.apply(plan, confirmed_same_items={'1':'same'}))
        copied.write_text(new_text)
        stale_state = service.reviews.get(job, old[2].question.question_id)
        stale_state.note = 'After planning'
        service.reviews.save(job, stale_state)
        must_reject(lambda: revision.apply(plan, confirmed_same_items={'1':'same'}))
        old_reviews[old[2].question.question_id] = (job/'review'/f'{old[2].question.question_id}.json').read_bytes()
        plan = revision.plan(job)
        result = revision.apply(plan, confirmed_same_items={'1':'Controlled wording edit of same copied exercise'})
        assert result['status'] == 'applied'
        current = service.select(job)
        assert [r.question.question_number for r in current] == [str(n) for n in range(1,10)]+['11']
        for old_record, record in zip(old[:9], current[:9]):
            assert old_record.question.question_id == record.question.question_id
            if record.question.question_number in ('1','2'):
                assert record.provenance.revision_id != old_record.provenance.revision_id
                assert record.review.human_approval == record.review.source_audit == 'pending'
            else:
                assert record.to_dict() == old_record.to_dict()
                assert (job/'review'/f'{record.question.question_id}.json').read_bytes() == old_reviews[record.question.question_id]
            service.trace_current(job, record.question.question_id)
        for record in (old[0], old[1], old[9]):
            restored, state = revision.revisions.get_snapshot(job, record.question.question_id, record.provenance.revision_id)
            assert restored.to_dict() == record.to_dict()
            assert state.audit['revision'] == record.provenance.revision_id
            trace = service.trace(restored, historical=True)
            assert trace['sha256'] == digest(raw)
            assert trace['excerpt'] == record.provenance.source_regions[0]['raw_text']
        applied_state = snapshot([base/'data'])
        assert revision.apply(plan)['status'] == 'already_applied'
        assert snapshot([base/'data']) == applied_state
        must_reject(lambda: service.select(job, ids=[old[9].question.question_id]))
        with patch('pathlib.Path.read_bytes', side_effect=AssertionError('Exporter source read')):
            selected = service.select(job, ids=[current[-1].question.question_id, current[0].question.question_id],
                                      subject=subject, unit=subject, source_id=current[0].source.source_id, question_type=kind)
            markdown = '\n\n'.join(project_to_markdown(r) for r in selected)
            assert '추가된 격리 검증 문항' in markdown and 'revision acceptance wording' in markdown
        (base/'selection.md').write_text(markdown)
        # Moving a known item to another key and reusing retired 10 are both blocked.
        if adapter == 'numbered-qa-v1':
            renumbered = new_text.replace('\t3. ', '\t30. ', 1)
            reused = new_text+'\t10. 새 용도?\n\t\t○ 새 답\n'
        else:
            renumbered = new_text.replace('|3|', '|30|', 1)
            reused = new_text+'|10|새 용도?|새 답|\n'
        for changed in (renumbered, reused):
            copied.write_text(changed)
            must_reject(lambda: revision.apply(revision.plan(job)))
        copied.write_text(new_text)
        reports.append(dict(source=relative, original_sha256=digest(raw), adapter=adapter, job=str(job),
                            original_count=count, active_count=len(current), changed=2, added=1, deleted=1, unchanged=7,
                            read_only_plan=True, stale_source_and_review_rejected=True,
                            identity_confirmation_required=True, renumber_and_reuse_rejected=True,
                            prior_canonical_review_and_source_restored=True, unchanged_records_and_reviews_preserved=True,
                            idempotent_replay=True, canonical_export_without_source=True))
    assert snapshot({p.parent for p in sources}) == before
    report = dict(samples=reports, original_files_preserved=len(before), originals_unchanged=True,
                  modified_only_isolated_copies=True, run=str(run))
    (output_root/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    verify(args.repo_root.resolve(), args.output_root.resolve())

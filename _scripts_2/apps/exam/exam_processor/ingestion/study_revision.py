"""Explicit study-source change plans and recoverable application.

Plans are data, never authority: application recomputes them against source and storage.
No similarity scoring or implicit renumbering is permitted.
"""
import json
from pathlib import Path
import shutil

from .study_markdown import parse_study_markdown
from .study_service import digest
from ..domain.review_state import ReviewState
from ..domain.services.record_service import RecordService
from ..storage.revision_repository import RevisionRepository
from ..storage.json_io import atomic_json_save


def token(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def storage_token(job):
    return token({p.relative_to(job).as_posix(): digest(p.read_bytes())
                  for p in sorted(job.rglob('*')) if p.is_file()})


def region_for(span, text, artifact_id, source_path, adapter):
    return dict(document_id=artifact_id, source_path=source_path, locator_kind='markdown_chars',
                start_char=span.start, end_char=span.end, raw_text=text[span.start:span.end],
                adapter=adapter, item_key=span.key)


class StudyRevision:
    def __init__(self, service):
        self.service = service
        self.revisions = RevisionRepository()
        self.records = RecordService(service.records, service.reviews, self.revisions)

    def _job(self, job):
        job = Path(job).resolve(strict=True)
        if job.parent != self.service.data_root / 'ingestion' / 'jobs':
            raise ValueError('Expected a study ingestion job in this data root')
        return job

    def _transaction(self, job):
        return self.service.data_root / 'ingestion' / 'transactions' / job.name

    def ensure_ready(self, job):
        if self._transaction(job).exists():
            raise ValueError('Interrupted application; run recover before reading or planning')

    def _artifact(self, manifest):
        artifact = self.service.artifacts.get_by_id(manifest.source_id)
        if artifact is None or not artifact.present:
            raise ValueError('Source artifact missing')
        indexed = self.service.artifacts.get_by_path(artifact.source_path)
        if indexed is None or indexed.artifact_id != artifact.artifact_id:
            raise ValueError('Source artifact index mismatch')
        return artifact

    def _source(self, artifact):
        path = (self.service.repo_root / artifact.source_path).resolve(strict=True)
        path.relative_to(self.service.repo_root)
        return path, path.read_bytes()

    def plan(self, job, *, options=None):
        job = self._job(job)
        with self.service._read_lock():
            self.ensure_ready(job)
            return self._plan(job, options=options)

    def _plan(self, job, *, options=None):
        before = storage_token(job)
        manifest = self.service.jobs.load_manifest(job)
        artifact = self._artifact(manifest)
        source, raw = self._source(artifact)
        settings = dict(manifest.metadata['study_ingestion'])
        previous_hash = settings.pop('source_sha256')
        requested = dict(settings if options is None else options)
        if set(requested) != set(settings):
            raise ValueError('Options must specify adapter, subject, unit and question_type')
        changed_options = {k: {'before': settings[k], 'after': requested[k]}
                           for k in settings if requested[k] != settings[k]}
        if not (job / 'records').is_dir():
            raise ValueError('Missing records directory')
        for rid in manifest.record_ids:
            if not (job / 'records' / (rid + '.json')).is_file() or not (job / 'review' / (rid + '.json')).is_file():
                raise ValueError('Incomplete stored record/review state')
        old_records = {r.question.question_number: r for r in self.service._select(job)}
        if len(old_records) != len(manifest.record_ids):
            raise ValueError('Duplicate stored item keys')
        text = raw.decode('utf-8')
        # An explicitly empty revision can retire all items; initial ingest remains strict.
        spans = [] if not text.strip() else parse_study_markdown(text, settings['adapter'])
        new = {s.key: s for s in spans}
        changes = []
        retired = manifest.metadata.get('study_retired', {})
        for key in list(old_records) + [key for key in new if key not in old_records]:
            old, fresh = old_records.get(key), new.get(key)
            old_value = dict(body=old.body, solution=old.solution) if old else None
            new_value = dict(body=fresh.body, solution=fresh.solution) if fresh else None
            kind = ('added' if old is None else 'deleted' if fresh is None
                    else 'unchanged' if old_value == new_value else 'changed')
            ambiguity = []
            if fresh and old and old.body != fresh.body:
                ambiguity.append('body_changed_requires_identity_confirmation')
            if fresh and key in retired:
                ambiguity.append('retired_key_reuse')
            if fresh and any(r.body == fresh.body and other != key for other, r in old_records.items()):
                # Unchanged duplicate stems do not by themselves constitute a renumbering.
                if old is None or old.body != fresh.body:
                    ambiguity.append('possible_renumbering')
            changes.append(dict(key=key, kind=kind, before=old_value, after=new_value,
                                question_id=old.question.question_id if old else None,
                                ambiguity=ambiguity))
        if storage_token(job) != before or source.read_bytes() != raw:
            raise ValueError('State changed while planning')
        result = dict(version=1, job=str(job), before_state=before,
                      artifact=artifact.to_dict(), previous_source_sha256=previous_hash,
                      source_sha256=digest(raw),
                      previous_source_snapshot_available=(job / 'sources' / (previous_hash + '.md')).is_file(),
                      options=settings, requested_options=requested,
                      options_changed=changed_options, changes=changes,
                      order=[span.key for span in spans])
        result['plan_id'] = token(result)
        return result

    def recover(self, job):
        """Explicit recovery; planning never writes or silently recovers."""
        job = self._job(job)
        with self.service._lock():
            return self._recover(job)

    def _recover(self, job):
        tx = self._transaction(job)
        if not tx.exists():
            return 'nothing_to_recover'
        marker = tx / 'state.json'
        if not marker.exists():
            # Backups were not committed; no live mutation could have begun.
            shutil.rmtree(tx)
            return 'discarded_preparation'
        state = json.loads(marker.read_text())
        if state['status'] == 'prepared':
            # Keep backup and marker until both restores complete; recovery is retryable.
            for p in job.iterdir():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            shutil.copytree(tx / 'job', job, dirs_exist_ok=True)
            artifact = self.service.artifacts.records_dir / state['artifact_file']
            atomic_json_save(artifact, json.loads((tx / 'artifact.json').read_text()))
        elif state['status'] != 'committed':
            raise ValueError('Invalid recovery state')
        shutil.rmtree(tx)
        return 'recovered'

    def apply(self, plan, *, confirmed_same_items=None):
        confirmations = dict(confirmed_same_items or {})
        job = self._job(plan['job'])
        with self.service._lock():
            self.ensure_ready(job)
            expected_id = token({k: v for k, v in plan.items() if k != 'plan_id'})
            if plan.get('plan_id') != expected_id:
                raise ValueError('Invalid or edited plan')
            manifest = self.service.jobs.load_manifest(job)
            receipt = manifest.metadata.get('study_applied', {}).get(plan['plan_id'])
            artifact = self._artifact(manifest)
            source, raw = self._source(artifact)
            if digest(raw) != plan['source_sha256']:
                raise ValueError('Source changed since plan')
            if receipt:
                if artifact.content_sha256 != digest(raw):
                    raise ValueError('Artifact state changed after application')
                # Replays after any further storage/review changes are stale, not no-ops.
                if self._state_without_receipts(job) != receipt['state']:
                    raise ValueError('Stored state changed after application')
                return dict(status='already_applied', job=str(job))
            fresh = self._plan(job, options=plan['requested_options'])
            if fresh != plan:
                raise ValueError('Stored state changed since plan')
            if plan['options_changed']:
                raise ValueError('Adapter/classification option change requires a separate workflow')
            required = {c['key'] for c in plan['changes'] if c['ambiguity']}
            blocked = [c for c in plan['changes'] if any(reason != 'body_changed_requires_identity_confirmation'
                                                        for reason in c['ambiguity'])]
            if blocked:
                raise ValueError('Ambiguous renumbering or retired key reuse; no identity inference allowed')
            if (set(confirmations) != required or any(not isinstance(v, str) or not v.strip()
                                                     for v in confirmations.values())):
                raise ValueError('Explicit same-item confirmation and reason required for changed bodies')
            if digest(raw) == plan['previous_source_sha256'] and all(c['kind'] == 'unchanged' for c in plan['changes']):
                return dict(status='unchanged', job=str(job))
            tx = self._transaction(job)
            tx.mkdir(parents=True)
            try:
                shutil.copytree(job, tx / 'job')
                (tx / 'artifact.json').write_text(json.dumps(artifact.to_dict(), ensure_ascii=False))
                state = dict(status='prepared', artifact_file=artifact.artifact_id + '.json')
                atomic_json_save(tx / 'state.json', state)
                self._apply_records(job, plan, raw, confirmations)
                if source.read_bytes() != raw:
                    raise ValueError('Source changed during application')
                self.service.artifacts.register_or_update(artifact.source_path, digest(raw), len(raw), artifact.title)
                manifest = self.service.jobs.load_manifest(job)
                receipt = dict(state=self._state_without_receipts(job), confirmations=confirmations)
                manifest.metadata.setdefault('study_applied', {})[plan['plan_id']] = receipt
                self.service.jobs.save_manifest(job, manifest)
                if source.read_bytes() != raw:
                    raise ValueError('Source changed before commit')
                atomic_json_save(tx / 'state.json', dict(state, status='committed'))
            except BaseException:
                self._recover(job)
                raise
            self._recover(job)  # discard committed backups only
            return dict(status='applied', job=str(job))

    def _state_without_receipts(self, job):
        state = {p.relative_to(job).as_posix(): digest(p.read_bytes())
                 for p in sorted(job.rglob('*')) if p.is_file() and p.name != 'job.json'}
        manifest = self.service.jobs.load_manifest(job)
        manifest.metadata.pop('study_applied', None)
        state['job.json'] = token(manifest.to_dict())
        return token(state)

    def _apply_records(self, job, plan, raw, confirmations):
        service = self.service
        manifest = service.jobs.load_manifest(job)
        settings = plan['options']
        artifact = self._artifact(manifest)
        text = raw.decode('utf-8')
        spans = [] if not text.strip() else parse_study_markdown(text, settings['adapter'])
        span_by_key = {s.key: s for s in spans}
        active_ids = {}
        retired = manifest.metadata.setdefault('study_retired', {})
        for change in plan['changes']:
            key, kind = change['key'], change['kind']
            qid = change['question_id']
            if kind == 'deleted':
                record = service.records.get(job, qid)
                review = service.reviews.get(job, qid)
                self.revisions.save_snapshot(job, qid, record.provenance.revision_id, record, review)
                retired[key] = dict(question_id=qid, revision_id=record.provenance.revision_id)
                continue
            span = span_by_key[key]
            region = region_for(span, text, artifact.artifact_id, artifact.source_path, settings['adapter'])
            if kind == 'changed':
                record = service.records.get(job, qid)
                def mutate(record, review, new_revision):
                    record.body, record.solution = span.body, span.solution
                    record.provenance.source_hashes = [digest(raw)]
                    record.provenance.source_regions = [region]
                self.records.mutate(job, qid, record.provenance.revision_id, mutate)
            elif kind == 'added':
                # Same deterministic key rule as initial ingestion, but tombstones prohibit reuse.
                record = service.make_record(artifact, span, text, raw, settings)
                qid = record.question.question_id
                if (job / 'records' / f'{qid}.json').exists():
                    raise ValueError('Existing historical identity cannot be reused')
                service.records.save(job, record)
                service.reviews.save(job, ReviewState(record_id=qid, applies_to_revision_id=record.provenance.revision_id,
                                                      warnings=manifest.warnings))
            active_ids[key] = qid
        # Original provenance of unchanged records remains bound to its original source revision.
        manifest.record_ids = [active_ids[s.key] for s in spans]
        manifest.metadata['study_ingestion']['source_sha256'] = digest(raw)
        manifest.metadata['study_observations'] = {
            active_ids[s.key]: dict(source_sha256=digest(raw), region=region_for(
                s, text, artifact.artifact_id, artifact.source_path, settings['adapter'])) for s in spans}
        service.save_source_snapshot(job, raw)
        service.jobs.save_manifest(job, manifest)

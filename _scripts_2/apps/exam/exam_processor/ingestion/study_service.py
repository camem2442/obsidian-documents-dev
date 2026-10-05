"""Study Markdown -> existing artifact identity + v2 draft job + canonical records.

No master promotion, AI calls, vault writes, source mutation, or automatic refresh.
Changed sources require an explicit StudyRevision plan/apply workflow.
"""
from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path
import shutil
import tempfile
import uuid

from .study_markdown import parse_study_markdown
from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    QuestionDomain,
    SourceDomain,
    SourceLocator,
    ProvenanceDomain,
    UnitDomain,
    ReviewDomain,
)
from ..domain.job_manifest import JobManifest
from ..domain.review_state import ReviewState
from ..storage.review_repository import ReviewRepository
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository
from ..storage.job_repository import JobRepository
from ..storage.record_repository import RecordRepository


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StudyIngestion:
    def __init__(self, repo_root: Path, data_root: Path):
        self.repo_root = repo_root.resolve()
        self.data_root = data_root.resolve()
        if self.data_root.is_relative_to(self.repo_root) or self.repo_root.is_relative_to(self.data_root):
            raise ValueError('Ingestion storage must be outside and disjoint from the source tree')
        self.artifacts = ArtifactRepository(self.data_root / 'library' / 'artifacts')
        self.jobs = JobRepository()
        self.records = RecordRepository()
        self.reviews = ReviewRepository()

    @contextmanager
    def _lock(self):
        self.data_root.mkdir(parents=True, exist_ok=True)
        with (self.data_root / '.study-ingestion.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    @contextmanager
    def _read_lock(self):
        # Initial ingestion creates this file; read-only plans never create directories/locks.
        with (self.data_root / '.study-ingestion.lock').open('r') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _revision(self):
        from .study_revision import StudyRevision
        return StudyRevision(self)

    def select(self, job_dir: Path, **filters):
        with self._read_lock():
            self._revision().ensure_ready(job_dir)
            return self._select(job_dir, **filters)

    def save_source_snapshot(self, job_dir, raw):
        path = job_dir / 'sources' / (digest(raw) + '.md')
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('Source snapshot hash collision/corruption')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def make_record(self, artifact, span, text, raw, options):
        from .study_revision import region_for
        adapter = options['adapter']
        qid = 'q-' + uuid.uuid5(uuid.NAMESPACE_URL, f'{artifact.artifact_id}:{adapter}:{span.key}').hex
        record = CanonicalQuestionRecord(
            question=QuestionDomain(qid, span.key, options['subject'], question_number=span.key,
                                    question_type=options['question_type']),
            source=SourceDomain(artifact.artifact_id, 'other', artifact.title,
                                format_id=adapter, item_code=span.key,
                                locator=SourceLocator(document_id=artifact.artifact_id)),
            unit=UnitDomain(path=list(options['unit'])),
            provenance=ProvenanceDomain(revision_id=digest(raw),
                                        source_record_ids=[artifact.artifact_id],
                                        source_hashes=[digest(raw)], source_regions=[region_for(
                                            span, text, artifact.artifact_id, artifact.source_path, adapter)]),
            review=ReviewDomain(transcription='completed'), body=span.body, solution=span.solution,
        )
        record.to_json()
        return record

    def trace_current(self, job_dir, record_id):
        with self._read_lock():
            self._revision().ensure_ready(job_dir)
            return self._trace_current(job_dir, record_id)

    def _trace_current(self, job_dir, record_id):
        import copy
        manifest = self.jobs.load_manifest(job_dir)
        if record_id not in manifest.record_ids:
            raise ValueError('Record is not currently selected')
        record = self.records.get(job_dir, record_id)
        observation = manifest.metadata.get('study_observations', {}).get(record_id)
        if observation:
            record = copy.deepcopy(record)
            record.provenance.source_hashes = [observation['source_sha256']]
            record.provenance.source_regions = [observation['region']]
        result = self.trace(record)
        result['evidence'] = 'current_source_observation'
        return result

    def ingest(self, source: Path, *, adapter: str, subject: str,
               unit: list[str], question_type: str, creation_receipt=None) -> Path:
        source = source.resolve(strict=True)
        relative = source.relative_to(self.repo_root).as_posix()
        if source.suffix != '.md' or not subject.strip() or not question_type.strip():
            raise ValueError('Explicit Markdown source, subject and question type are required')
        raw = source.read_bytes()
        if creation_receipt and digest(raw) != creation_receipt['source_sha256']:
            raise ValueError('Source differs from the confirmed input plan')
        text = raw.decode('utf-8')
        spans = parse_study_markdown(text, adapter)
        options = dict(adapter=adapter, subject=subject, unit=unit, question_type=question_type)
        with self._lock():
            self.artifacts.validate_integrity()
            artifact = self.artifacts.get_by_path(relative)
            # One draft job per existing artifact, preserving its path identity.
            if artifact:
                job_id = 'study-' + uuid.uuid5(uuid.NAMESPACE_URL, artifact.artifact_id).hex
                job_dir = self.data_root / 'ingestion' / 'jobs' / job_id
                if job_dir.exists():
                    manifest = self.jobs.load_manifest(job_dir)
                    if manifest.metadata.get('study_ingestion') != dict(options, source_sha256=digest(raw)):
                        raise ValueError('Source or ingestion options changed; explicit revision workflow required')
                    if artifact.content_sha256 != digest(raw) or not artifact.present:
                        raise ValueError('Artifact inventory disagrees with ingested source')
                    self._revision().ensure_ready(job_dir)
                    for record in self._select(job_dir):
                        self._trace_current(job_dir, record.question.question_id)
                        state = self.reviews.get(job_dir, record.question.question_id)
                        if state.applies_to_revision_id != record.provenance.revision_id:
                            raise ValueError('Missing or stale review state')
                    if len(manifest.record_ids) != len(spans):
                        raise ValueError('Incomplete ingestion manifest')
                    return job_dir
            if source.read_bytes() != raw:
                raise ValueError('Source changed during ingestion')
            artifact = self.artifacts.register_or_update(relative, digest(raw), len(raw), source.stem)
            job_id = 'study-' + uuid.uuid5(uuid.NAMESPACE_URL, artifact.artifact_id).hex
            job_dir = self.data_root / 'ingestion' / 'jobs' / job_id
            records = [self.make_record(artifact, span, text, raw, options) for span in spans]
            jobs_root = self.data_root / 'ingestion' / 'jobs'
            jobs_root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.study-pending-', dir=jobs_root))
            try:
                self.save_source_snapshot(staging, raw)
                self.records.save_many(staging, records)
                for record in records:
                    self.reviews.save(staging, ReviewState(
                        record_id=record.question.question_id,
                        applies_to_revision_id=record.provenance.revision_id,
                        warnings=['Study-note content; original publication and answer correctness unverified.']))
                manifest = JobManifest(
                    id=job_id, source_id=artifact.artifact_id, source=source.stem,
                    track='', format=adapter, subject=subject, source_type='other',
                    record_ids=[r.question.question_id for r in records],
                    documents={artifact.artifact_id: str(source)},
                    warnings=['Study-note transcription only; original publication and answers unverified.'],
                    metadata={'study_ingestion': dict(options, source_sha256=digest(raw)),
                              **({'input_creation': creation_receipt} if creation_receipt else {})},
                )
                self.jobs.save_manifest(staging, manifest)
                if source.read_bytes() != raw:
                    raise ValueError('Source changed before publishing')
                staging.rename(job_dir)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            return job_dir

    def _select(self, job_dir: Path, *, ids: list[str] | None = None,
               subject: str | None = None, source_id: str | None = None,
               unit: str | None = None, question_type: str | None = None) -> list[CanonicalQuestionRecord]:
        """Manifest-backed selection; exporters need no source reads or parsing."""
        manifest = self.jobs.load_manifest(job_dir)
        selected = manifest.record_ids if ids is None else ids
        if len(selected) != len(set(selected)) or any(rid not in manifest.record_ids for rid in selected):
            raise ValueError('Duplicate or unknown selection IDs')
        records = [self.records.get(job_dir, rid) for rid in selected]
        return [r for r in records if (subject is None or r.question.subject == subject)
                and (source_id is None or r.source.source_id == source_id)
                and (unit is None or unit in r.unit.path)
                and (question_type is None or r.question.question_type == question_type)]

    def trace(self, record: CanonicalQuestionRecord, *, historical=False) -> dict:
        """Resolve and verify source bytes/span. Does not confer source-audit approval."""
        artifact = self.artifacts.get_by_id(record.source.locator.document_id)
        if artifact is None:
            raise ValueError('Source artifact missing')
        source = (self.repo_root / artifact.source_path).resolve()
        source.relative_to(self.repo_root)
        if historical:
            job_id = 'study-' + uuid.uuid5(uuid.NAMESPACE_URL, artifact.artifact_id).hex
            expected, = record.provenance.source_hashes
            import re
            if not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Invalid source hash')
            snapshot = self.data_root / 'ingestion' / 'jobs' / job_id / 'sources' / (expected + '.md')
            if not snapshot.is_file():
                raise ValueError('Historical full-source snapshot unavailable; record retains recorded excerpt only')
            raw = snapshot.read_bytes()
        else:
            raw = source.read_bytes()
        if ((not historical and (not artifact.present or artifact.content_sha256 != digest(raw)))
                or record.provenance.source_hashes != [digest(raw)]):
            raise ValueError('Source hash changed')
        region, = record.provenance.source_regions
        excerpt = raw.decode('utf-8')[region['start_char']:region['end_char']]
        if (region['document_id'] != artifact.artifact_id
                or record.provenance.source_record_ids != [artifact.artifact_id]
                or region['source_path'] != artifact.source_path or region['raw_text'] != excerpt
                or record.source.source_id != artifact.artifact_id):
            raise ValueError('Source locator mismatch')
        return dict(source_path=str(source), sha256=digest(raw), excerpt=excerpt,
                    evidence='archived_source_bytes' if historical else 'live_source_bytes')

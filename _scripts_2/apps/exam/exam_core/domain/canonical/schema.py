"""Canonical Data Model (SSOT) for Exam Core v0.3.0."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "0.3.0"

SOURCE_AUDIT_VALUES = {
    "pending",
    "no_difference",
    "suspected_difference",
    "unreadable",
    "mismatch",
    "waived",
    "completed",  # legacy alias of no_difference
}

SOURCE_AUDIT_RESULTS = {
    "no_difference",
    "suspected_difference",
    "unreadable",
}


@dataclass
class QuestionDomain:
    question_id: str
    display_code: str
    subject: str
    content_kind: str = "question"  # question, passage, concept, example
    question_number: str = ""
    points: Optional[float] = None
    correct_rate: Optional[float] = None
    answer: Optional[str] = None
    question_type: Optional[str] = None
    difficulty: Optional[int] = None
    status: str = "unread"  # unread, solved, incorrect, mastered
    content_type: str = ""  # past_exam, original, variant; empty means infer at export
    variant_of: List[str] = field(default_factory=list)
    modality: str = ""  # listening, reading; English only
    question_set_id: str = ""
    strategy_tags: List[str] = field(default_factory=list)


@dataclass
class SourceLocator:
    document_id: str = ""
    pdf_pages: List[int] = field(default_factory=list)


@dataclass
class SourceDomain:
    source_id: str
    type: str  # exam, workbook, solution, other
    title: str
    format_id: str = ""
    edition: Optional[str] = None
    section_code: Optional[str] = None
    section_title: Optional[str] = None
    item_code: Optional[str] = None
    print_labels: List[str] = field(default_factory=list)
    source_set_id: str = ""
    exam_code: str = ""
    exam_form: str = ""
    locator: SourceLocator = field(default_factory=SourceLocator)


@dataclass
class OriginDomain:
    type: str  # kice, police, leet, mock
    academic_year: str = ""
    administration_year: str = ""
    session: str = ""
    round: Optional[str] = None
    authority: Optional[str] = None
    track: str = ""
    question_number: str = ""
    raw_label: str = ""
    variants: List[Dict[str, str]] = field(default_factory=list)
    question_id: str = ""  # verified identical original only
    subject: str = ""
    exam_form: str = ""

    def canonical_key(self, subject: str = "", exam_form: str = "") -> str:
        """Return 7-tuple KICE canonical origin key.

        Format: authority:academic_year:session:subject:track:exam_form:question_number
        Example: kice:2026:09:math:prob_stat::29
        """
        auth = (self.authority or self.type or "").strip().lower()
        year = str(self.academic_year or "").strip()
        sess = str(self.session or "").strip()
        if sess.isdigit() and len(sess) == 1:
            sess = f"0{sess}"
        subj = (subject or self.subject or "").strip().lower()
        trk = (self.track or "").strip().lower()
        form = (exam_form or self.exam_form or "").strip().lower()
        q_num = str(self.question_number or "").strip()
        return f"{auth}:{year}:{sess}:{subj}:{trk}:{form}:{q_num}"


@dataclass
class UnitDomain:
    display_name: Optional[str] = None
    path: List[str] = field(default_factory=list)
    code: Optional[str] = None
    taxonomy_id: Optional[str] = None


@dataclass
class RelationsDomain:
    passage_ids: List[str] = field(default_factory=list)
    solution_ids: List[str] = field(default_factory=list)
    listening_script_ids: List[str] = field(default_factory=list)


@dataclass
class AssetDomain:
    asset_id: str
    section: str
    source_path: str
    path: str
    media_type: str
    description: str = ""
    sha256: Optional[str] = None


@dataclass
class SourceRegion:
    region_id: str = ""
    document_id: str = ""
    page_num: int = 0
    bbox_pt: List[float] = field(default_factory=list)
    crop_img_path: str = ""


@dataclass
class ProvenanceDomain:
    revision_id: str = ""
    source_record_ids: List[str] = field(default_factory=list)
    source_regions: List[Dict[str, Any]] = field(default_factory=list)
    source_hashes: List[str] = field(default_factory=list)


@dataclass
class ClassificationCandidate:
    code: str
    title: str
    confidence: float
    evidence: List[str] = field(default_factory=list)


@dataclass
class ClassificationDomain:
    status: str = "unclassified"  # unclassified, candidate, confirmed
    method: Optional[str] = None  # workbook_toc, curriculum_ai, manual
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    curriculum_revision: Optional[str] = None


@dataclass
class ReviewDomain:
    transcription: str = "pending"  # pending, completed, failed
    source_audit: str = "pending"
    classification: str = "not_started"  # not_started, candidate, confirmed
    human_approval: str = "pending"  # pending, approved, rejected


@dataclass
class CanonicalQuestionRecord:
    schema_version: str = SCHEMA_VERSION
    question: QuestionDomain = field(default_factory=lambda: QuestionDomain(question_id="", display_code="", subject=""))
    source: SourceDomain = field(default_factory=lambda: SourceDomain(source_id="", type="", title=""))
    origin: Optional[OriginDomain] = None
    unit: UnitDomain = field(default_factory=UnitDomain)
    relations: RelationsDomain = field(default_factory=RelationsDomain)
    provenance: ProvenanceDomain = field(default_factory=ProvenanceDomain)
    classification: ClassificationDomain = field(default_factory=ClassificationDomain)
    review: ReviewDomain = field(default_factory=ReviewDomain)
    assets: List[AssetDomain] = field(default_factory=list)
    body: str = ""
    solution: str = ""

    def canonical_origin_key(self) -> str:
        """Return canonical origin key for this record if origin domain exists."""
        if not self.origin:
            return ""
        return self.origin.canonical_key(
            subject=self.question.subject,
            exam_form=self.source.exam_form,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        import json
        from _scripts_2.apps.exam.exam_core.domain.canonical.validate import validate_canonical_dict
        data = self.to_dict()
        errors = validate_canonical_dict(data)
        if errors:
            raise ValueError("Invalid canonical record: " + "; ".join(errors))
        return json.dumps(data, ensure_ascii=False, indent=indent, allow_nan=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalQuestionRecord:
        from _scripts_2.apps.exam.exam_core.domain.canonical.validate import validate_canonical_dict
        errors = validate_canonical_dict(data)
        if errors:
            raise ValueError("Invalid canonical record: " + "; ".join(errors))
        q_data = data.get("question", {})
        question = QuestionDomain(**q_data) if isinstance(q_data, dict) else q_data

        s_data = data.get("source", {})
        if isinstance(s_data, dict):
            loc_data = s_data.get("locator", {})
            locator = SourceLocator(**loc_data) if isinstance(loc_data, dict) else loc_data
            s_kwargs = dict(s_data)
            s_kwargs["locator"] = locator
            source = SourceDomain(**s_kwargs)
        else:
            source = s_data

        o_data = data.get("origin")
        origin = OriginDomain(**o_data) if isinstance(o_data, dict) else o_data

        u_data = data.get("unit", {})
        unit = UnitDomain(**u_data) if isinstance(u_data, dict) else u_data

        r_data = data.get("relations", {})
        relations = RelationsDomain(**r_data) if isinstance(r_data, dict) else r_data

        assets = [AssetDomain(**asset) for asset in data.get("assets", [])]

        p_data = data.get("provenance", {})
        provenance = ProvenanceDomain(**p_data) if isinstance(p_data, dict) else p_data

        c_data = data.get("classification", {})
        classification = ClassificationDomain(**c_data) if isinstance(c_data, dict) else c_data

        rev_data = data.get("review", {})
        review = ReviewDomain(**rev_data) if isinstance(rev_data, dict) else rev_data

        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            question=question,
            source=source,
            origin=origin,
            unit=unit,
            relations=relations,
            provenance=provenance,
            classification=classification,
            review=review,
            assets=assets,
            body=data.get("body", ""),
            solution=data.get("solution", ""),
        )

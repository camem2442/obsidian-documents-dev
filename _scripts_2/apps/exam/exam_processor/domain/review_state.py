"""ReviewState dataclass for Exam Processor runtime inspection and AI states."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class ReviewState:
    record_id: str
    applies_to_revision_id: str
    published_revision_id: str = ""
    state_revision_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    note: str = ""

    audit: Optional[Dict[str, Any]] = None
    audit_waiver: Optional[Dict[str, Any]] = None

    warnings: List[str] = field(default_factory=list)
    quality_checks: List[Dict[str, Any]] = field(default_factory=list)

    transcription_pending: List[str] = field(default_factory=list)

    source_styles: List[Dict[str, Any]] = field(default_factory=list)
    style_mapping_errors: List[Any] = field(default_factory=list)
    group_mapping_errors: List[Any] = field(default_factory=list)
    box_checks: List[Dict[str, Any]] = field(default_factory=list)

    solution_candidates: List[Dict[str, Any]] = field(default_factory=list)
    selected_solution_id: str = ""
    solution_match: Optional[Dict[str, Any]] = None
    solution_link_backup: Optional[Dict[str, Any]] = None

    ai_runs: List[Dict[str, Any]] = field(default_factory=list)
    material_validation_errors: List[Any] = field(default_factory=list)

    approval_events: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    extracted_revision: str = ""
    reference_text: str = ""
    assets: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewState:
        return cls(
            record_id=data.get("record_id", ""),
            applies_to_revision_id=data.get("applies_to_revision_id", data.get("revision_id", "")),
            published_revision_id=data.get("published_revision_id", data.get("published_revision", "")),
            state_revision_id=data.get("state_revision_id") or uuid.uuid4().hex,
            note=data.get("note", ""),
            audit=data.get("audit"),
            audit_waiver=data.get("audit_waiver"),
            warnings=list(data.get("warnings", [])),
            quality_checks=list(data.get("quality_checks", [])),
            transcription_pending=list(data.get("transcription_pending", [])),
            source_styles=list(data.get("source_styles", [])),
            style_mapping_errors=list(data.get("style_mapping_errors", [])),
            group_mapping_errors=list(data.get("group_mapping_errors", [])),
            box_checks=list(data.get("box_checks", [])),
            solution_candidates=list(data.get("solution_candidates", [])),
            selected_solution_id=data.get("selected_solution_id", ""),
            solution_match=data.get("solution_match"),
            solution_link_backup=data.get("solution_link_backup"),
            ai_runs=list(data.get("ai_runs", [])),
            material_validation_errors=list(data.get("material_validation_errors", [])),
            approval_events=list(data.get("approval_events", [])),
            history=list(data.get("history", [])),
            extracted_revision=data.get("extracted_revision", ""),
            reference_text=data.get("reference_text", ""),
            assets=list(data.get("assets", [])),
        )

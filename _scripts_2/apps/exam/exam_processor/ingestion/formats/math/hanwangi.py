"""Import confirmed source-bound regions; automatic scan segmentation is separate."""
from __future__ import annotations

import copy
from typing import Any, Dict, List

import pypdfium2 as pdfium

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.domain.canonical_factory import create_canonical_record
from ....domain.models import digest, identity
from ....domain.review_state import ReviewState
from ....pipeline.workbook import structure_errors
from ...curriculum import workbook_toc_classification
from ...region_render import render_region
from ..base import BaseFormatModule, IngestBatch
from .workbook_layout import match_records, validate_layout


class HanwangiFormat(BaseFormatModule):
    format_id = "hanwangi_2026_probability"
    label = "2026 한완기 확률과통계 (확인된 영역)"
    extensions = frozenset({".pdf"})
    supported_tracks = frozenset({"확률과 통계"})
    parser_version = "0.1.0"

    def parse(self, request):
        self.validate(request)
        structure, layout = request.structure, request.layout
        if not structure or not layout or structure.get("profile") != self.format_id:
            raise ValueError("이 판본의 교재 구조와 확인된 문항 영역 기록이 필요합니다.")
        errors = structure_errors(structure)
        if errors:
            raise ValueError("; ".join(errors))
        if not request.solution_path:
            raise ValueError("이 판본은 해설 원본 PDF가 필요합니다.")
        paths = {"problem": request.problem_path, "solution": request.solution_path}
        for role, path in paths.items():
            if digest(path) != structure["documents"][role]["sha256"]:
                raise ValueError(f"{role}: 확인한 구조와 원본 PDF가 다릅니다.")
        validate_layout(layout, structure)
        if request.pages:
            raise ValueError("확인된 영역 기록이 추출 범위를 지정합니다. 추가 페이지 범위는 비워 두세요.")
        matches = match_records(layout)
        nodes = {n["id"]: n for n in structure["nodes"]}
        request.workdir.mkdir(parents=True, exist_ok=True)

        def regions_for(record):
            regions = copy.deepcopy(record["regions"])
            for index, region in enumerate(regions):
                region["image"] = f"{record['id']}-{index}.png"
                with pdfium.PdfDocument(paths[region["document"]]) as document:
                    page = document[region["page"] - 1]
                    actual = page.get_size()
                    page.close()
                    if any(abs(a - b) > .1 for a, b in zip(actual, [region["width"], region["height"]])):
                        raise ValueError("영역 기록과 PDF의 페이지 크기가 다릅니다.")
                    render_region(document, region, request.workdir / region["image"])
            return regions

        records: List[CanonicalQuestionRecord] = []
        review_states: List[ReviewState] = []

        for record in layout["items"]:
            if record["kind"] == "solution":
                continue
            node = nodes[record["node"]]
            record_id = identity(structure["id"], "region-item", record["id"])
            regions = regions_for(record)
            subtype = record.get("subtype", "")
            content_kind = "example" if subtype == "example" else record["kind"]
            number = str(record["number"])
            chapter = node["chapter"]
            section_code = node.get("section_code")
            section_title = node.get("section_title")

            toc_class = workbook_toc_classification(section_code, section_title, chapter)
            transcription_pending = ["body"]

            solution_candidates = []
            solution_match = None
            if record["kind"] == "question":
                match = matches[record["id"]]
                solution_match = {k: v for k, v in match.items() if k != "candidates"}
                solution_candidates = [
                    {
                        "id": identity(structure["id"], "solution", c["id"]),
                        "number": c["number"],
                        "body": "[해설 전사 대기]",
                        "answer": "",
                        "transcription_required": True,
                        "regions": regions_for(c),
                    }
                    for c in match["candidates"]
                ]
                transcription_pending.append("solution")

            rec, rev = create_canonical_record(
                record_id=record_id,
                subject="수학",
                source_id=request.source_id,
                source_type="workbook",
                source_title=request.source,
                number=number,
                content_kind=content_kind,
                body="[전사 대기]",
                format_id=self.format_id,
                edition=structure.get("edition"),
                section_code=section_code,
                section_title=section_title,
                source_set_id=node["id"],
                unit_name=chapter,
                unit_path=toc_class.get("unit_path") or ([chapter] if chapter else []),
                unit_code=toc_class.get("unit_code"),
                taxonomy_id=toc_class.get("curriculum_id") or None,
                source_regions=[dict(region, content_role="body") for region in regions],
                classification_status=toc_class.get("classification_status", "unclassified"),
                classification_method="workbook_toc" if toc_class.get("classification_method") == "workbook_toc_confirmed" else None,
                classification_candidates=toc_class.get("classification_candidates", []),
                classification_evidence=toc_class.get("classification_evidence", []),
                transcription_pending=transcription_pending,
                solution_candidates=solution_candidates,
                solution_match=solution_match,
            )
            records.append(rec)
            review_states.append(rev)

        if not records:
            raise ValueError("가져올 문제나 개념 항목이 없습니다.")

        return IngestBatch(
            records=records,
            review_states=review_states,
            warnings=[],
            metadata={
                "subject": "수학",
                "workbook": structure,
                "layout": layout,
                "solution_format": self.format_id,
            },
        )


"""OriginEvidenceParser — extracts structured KICE origin evidence from note paths and contents.

Follows strict fail-closed rules:
- Integrates ExamSchemeResolver to resolve historical era constraints (2014~2028).
- Differentiates subject from elective track (e.g., '2 문법' indicates subject='korean' without forcing track='lang_media').
- Rejects non-KICE sources (police, military academy, education office, workbook codes like '26해0929', EBS codes).
- Defaults relation_type_hint to None unless explicitly indicated by known keywords.
- Computes is_fully_qualified based on scheme requirements and available path evidence.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..match_models import MatchEvidence
from .exam_scheme_resolver import ExamSchemeResolver, SchemeRequirement


# 6-digit KICE pattern: YYSSNN (Year, Session, Question Number)
# e.g., 260929, 241134, 180621
RE_6DIGIT_PREFIX = re.compile(r"^(\d{2})(\d{2})(\d{2})(?:[_\-\s](.*))?$")
RE_6DIGIT_EXACT = re.compile(r"^(\d{2})(\d{2})(\d{2})$")

# Hanwangi workbook occurrence pattern: YY해SSNN (e.g., 26해0929)
RE_HANWANGI = re.compile(r"^(\d{2})해(\d{2})(\d{2})")

# EBS item code pattern: YYNNN-NNNN (e.g., 24001-0123)
RE_EBS_CODE = re.compile(r"^\d{5}-\d{4}$")


@dataclass
class ParsedOriginEvidence:
    """Structured evidence extracted from an artifact's path and name."""
    is_kice: bool = False
    authority: str = "kice"
    academic_year: Optional[str] = None
    session: Optional[str] = None
    question_number: Optional[str] = None
    subject: Optional[str] = None
    track: Optional[str] = None
    exam_form: str = ""
    relation_type_hint: Optional[str] = None
    is_fully_qualified: bool = False
    scheme_requirement: Optional[SchemeRequirement] = None
    rejection_reason: Optional[str] = None
    evidences: List[MatchEvidence] = field(default_factory=list)


def infer_relation_type_hint(label: str) -> Optional[str]:
    """Infer relation type hint from file suffix or label. Defaults to None.
    
    Keywords:
    - 전략, 시험장 전략 -> exam_strategy
    - 태도, 강령 -> attitude_framework
    - 풀이, 풀이 과정, 독해 과정, 해설 -> reasoning_process
    - 해석, 번역 -> translation
    - 선지, 오답 선지, 선지 분석 -> choice_analysis
    """
    if not label:
        return None
    normalized = unicodedata.normalize("NFC", label).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    
    if "시험장전략" in normalized or "전략" in normalized:
        return "exam_strategy"
    if "태도" in normalized or "강령" in normalized:
        return "attitude_framework"
    if any(kw in normalized for kw in ["풀이과정", "독해과정", "풀이", "해설"]):
        return "reasoning_process"
    if "해석" in normalized or "번역" in normalized:
        return "translation"
    if any(kw in normalized for kw in ["선지분석", "오답선지", "선지"]):
        return "choice_analysis"
    
    return None


class OriginEvidenceParser:
    """Parses legacy KS note paths and metadata to extract origin candidate constraints."""

    def parse_path(self, relative_path: str) -> ParsedOriginEvidence:
        """Parse relative path within KS vault to determine origin constraints and evidence."""
        norm_path_str = unicodedata.normalize("NFC", relative_path)
        p = Path(norm_path_str)
        stem = unicodedata.normalize("NFC", p.stem)
        full_path_str = norm_path_str

        evidences: List[MatchEvidence] = []

        # 1. Non-KICE and workbook rejection
        if RE_HANWANGI.search(stem):
            return ParsedOriginEvidence(
                is_kice=False,
                rejection_reason="Workbook occurrence pattern (Hanwangi '해')",
                evidences=[MatchEvidence("filename", stem, "conclusive", "Hanwangi workbook code")]
            )

        if RE_EBS_CODE.match(stem):
            return ParsedOriginEvidence(
                is_kice=False,
                rejection_reason="EBS item code pattern",
                evidences=[MatchEvidence("filename", stem, "conclusive", "EBS item code")]
            )

        # Check for non-KICE authority keywords in path or stem
        for non_kice in ["경찰", "사관", "교육청", "학평", "전국연합", "한능검", "leet", "meet", "peet", "공무원"]:
            if non_kice in full_path_str.lower():
                return ParsedOriginEvidence(
                    is_kice=False,
                    authority=non_kice,
                    rejection_reason=f"Non-KICE authority indicated by '{non_kice}'",
                    evidences=[MatchEvidence("path", full_path_str, "conclusive", f"Path contains '{non_kice}'")]
                )

        # 2. Extract 6-digit stem
        match = RE_6DIGIT_PREFIX.match(stem)
        if not match:
            return ParsedOriginEvidence(
                is_kice=False,
                rejection_reason=f"Filename stem '{stem}' does not match KICE 6-digit pattern (YYSSNN)",
                evidences=[MatchEvidence("filename", stem, "conclusive", "Non-standard filename")]
            )

        yy, ss, nn, suffix = match.groups()
        yy_int = int(yy)
        year_full = str(1900 + yy_int if yy_int >= 90 else 2000 + yy_int)
        session_norm = f"{int(ss):02d}"
        q_num_norm = str(int(nn))

        evidences.append(
            MatchEvidence(
                kind="stem_6digit",
                value=f"{yy}{ss}{nn}",
                strength="conclusive",
                description=f"Parsed academic_year={year_full}, session={session_norm}, question_number={q_num_norm}"
            )
        )

        relation_hint = infer_relation_type_hint(suffix or "")

        # 3. Subject and Track inference from path hierarchy
        subject: Optional[str] = None
        track: Optional[str] = None
        exam_form: str = ""

        path_lower = full_path_str.lower()

        # Subject detection
        if any(kw in path_lower for kw in ["/1 수학", "/수학", "수학/"]):
            subject = "math"
            evidences.append(MatchEvidence("path_subject", "math", "strong", "Path indicates Mathematics"))
        elif any(kw in path_lower for kw in ["/영어", "영어/"]):
            subject = "english"
            track = ""
            evidences.append(MatchEvidence("path_subject", "english", "strong", "Path indicates English"))
        elif any(kw in path_lower for kw in ["/국어", "국어/", "2 문법"]):
            subject = "korean"
            evidences.append(MatchEvidence("path_subject", "korean", "strong", "Path indicates Korean"))
        elif any(kw in path_lower for kw in ["물리", "physics"]):
            subject = "physics"
        elif any(kw in path_lower for kw in ["화학", "chemistry"]):
            subject = "chemistry"
        elif any(kw in path_lower for kw in ["생명", "생물", "biology"]):
            subject = "life_science"
        elif any(kw in path_lower for kw in ["지구", "earth"]):
            subject = "earth_science"
        elif "한국사" in path_lower:
            subject = "korean_history"

        # Track detection
        if subject == "math":
            if any(kw in path_lower for kw in ["미적분", "미적"]):
                track = "calculus"
                evidences.append(MatchEvidence("path_track", "calculus", "strong", "Path indicates Calculus"))
            elif any(kw in path_lower for kw in ["확률과 통계", "확통", "확률과통계"]):
                track = "prob_stat"
                evidences.append(MatchEvidence("path_track", "prob_stat", "strong", "Path indicates Probability & Statistics"))
            elif any(kw in path_lower for kw in ["기하"]):
                track = "geometry"
                evidences.append(MatchEvidence("path_track", "geometry", "strong", "Path indicates Geometry"))
        elif subject == "korean":
            if any(kw in path_lower for kw in ["언어와 매체", "언어와매체", "언매"]):
                track = "lang_media"
                evidences.append(MatchEvidence("path_track", "lang_media", "strong", "Path indicates Language & Media"))
            elif any(kw in path_lower for kw in ["화법과 작문", "화법과작문", "화작"]):
                track = "speech_writing"
                evidences.append(MatchEvidence("path_track", "speech_writing", "strong", "Path indicates Speech & Writing"))
            elif any(kw in path_lower for kw in ["문학", "독서"]):
                track = ""

        # Exam Form detection (Ga/Na, A/B)
        if any(kw in path_lower for kw in ["/가형", "가형/", "_가형", " 가형"]):
            exam_form = "ga"
            evidences.append(MatchEvidence("path_exam_form", "ga", "strong", "Path indicates Type Ga"))
        elif any(kw in path_lower for kw in ["/나형", "나형/", "_나형", " 나형"]):
            exam_form = "na"
            evidences.append(MatchEvidence("path_exam_form", "na", "strong", "Path indicates Type Na"))
        elif any(kw in path_lower for kw in ["/a형", "a형/", "_a형", " a형"]):
            exam_form = "a"
            evidences.append(MatchEvidence("path_exam_form", "a", "strong", "Path indicates Type A"))
        elif any(kw in path_lower for kw in ["/b형", "b형/", "_b형", " b형"]):
            exam_form = "b"
            evidences.append(MatchEvidence("path_exam_form", "b", "strong", "Path indicates Type B"))

        # 4. Era-specific Exam Scheme validation
        scheme_req = ExamSchemeResolver.resolve(
            academic_year=year_full,
            session=session_norm,
            subject=subject,
            question_number=q_num_norm,
        )

        is_fully_qualified = False
        if scheme_req.is_fully_valid and subject:
            # Check track requirement
            track_ok = False
            if scheme_req.needs_track:
                track_ok = (track is not None)
            else:
                if track is None and scheme_req.default_track is not None:
                    track = scheme_req.default_track
                track_ok = (track is not None)

            # Check exam_form requirement
            form_ok = False
            if scheme_req.needs_exam_form:
                form_ok = bool(exam_form)
            else:
                form_ok = True

            is_fully_qualified = track_ok and form_ok

        return ParsedOriginEvidence(
            is_kice=True,
            authority="kice",
            academic_year=year_full,
            session=session_norm,
            question_number=q_num_norm,
            subject=subject,
            track=track,
            exam_form=exam_form,
            relation_type_hint=relation_hint,
            is_fully_qualified=is_fully_qualified,
            scheme_requirement=scheme_req,
            evidences=evidences,
        )

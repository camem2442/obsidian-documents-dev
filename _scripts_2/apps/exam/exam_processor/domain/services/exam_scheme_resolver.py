"""ExamSchemeResolver — Resolves KICE exam structure requirements by academic year.

Encapsulates official Korean College Scholastic Ability Test (CSAT/KICE) era policies:
- 2028: 2028 Admissions Reform (Integrated Korean & Math, August Mock Exam replacing September).
- 2022~2027: Common + Elective System (Math 1~22 Common, 23~30 Elective; Korean 1~34 Common, 35~45 Elective).
- 2017~2021: Integrated Korean (no electives); Math separated into Type Ga / Type Na (exam_form required).
- 2015~2016: Level-differentiated A/B type for Korean & Math; English integrated.
- 2014: Level-differentiated A/B type for Korean, Math, and English.
- <2014 or >2028: Unsupported / unverified scheme (is_supported=False, fail-closed to manual review).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Set


@dataclass(frozen=True)
class SchemeRequirement:
    is_supported: bool           # Whether this year's scheme is officially verified and supported
    is_valid_session: bool       # Whether the session is valid for the academic year
    is_valid_question_range: bool# Whether question number is within valid range for the subject
    needs_track: bool            # Whether elective track is required (e.g. Calculus, ProbStat, Geometry)
    needs_exam_form: bool        # Whether exam paper form is required (e.g. Ga/Na, A/B)
    default_track: Optional[str] # Default track for common questions (usually "")
    min_question: int
    max_question: int
    valid_sessions: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def is_fully_valid(self) -> bool:
        return self.is_supported and self.is_valid_session and self.is_valid_question_range


class ExamSchemeResolver:
    """SSOT Resolver for KICE exam era constraints and question boundaries."""

    @staticmethod
    def resolve(
        academic_year: Optional[str],
        session: Optional[str],
        subject: Optional[str],
        question_number: Optional[str],
    ) -> SchemeRequirement:
        if not academic_year or not str(academic_year).strip().isdigit():
            return SchemeRequirement(
                is_supported=False,
                is_valid_session=False,
                is_valid_question_range=False,
                needs_track=False,
                needs_exam_form=False,
                default_track=None,
                min_question=0,
                max_question=0,
                valid_sessions=frozenset(),
            )

        year = int(academic_year)
        subj = (subject or "").strip().lower()
        sess = str(session or "").strip()
        if sess.isdigit() and len(sess) == 1:
            sess = f"0{sess}"

        q_num = int(question_number) if (question_number and str(question_number).strip().isdigit()) else 0

        # [Era 0] Pre-2014 or Post-2028: Fail-closed unsupported scheme
        if year < 2014 or year > 2028:
            return SchemeRequirement(
                is_supported=False,
                is_valid_session=False,
                is_valid_question_range=False,
                needs_track=False,
                needs_exam_form=False,
                default_track=None,
                min_question=1,
                max_question=50,
                valid_sessions=frozenset({"06", "09", "11"}),
            )

        # Session validity by era
        # 2028 onwards: June(06), August(08), November/CSAT(11)
        # 2014~2027: June(06), September(09), November/CSAT(11)
        valid_sessions = frozenset({"06", "08", "11"}) if year == 2028 else frozenset({"06", "09", "11"})
        is_valid_session = sess in valid_sessions if sess else True

        # Subject question range
        if subj == "korean":
            min_q, max_q = 1, 45
        elif subj == "math":
            min_q, max_q = 1, 30
        elif subj == "english":
            min_q, max_q = 1, 45
        else:
            # Exploration or unspecified
            min_q, max_q = 1, 20

        is_valid_q = (min_q <= q_num <= max_q) if q_num > 0 else True

        # [Era 1] 2014: Korean A/B, Math A/B, English A/B
        if year == 2014:
            needs_form = subj in ("korean", "math", "english")
            return SchemeRequirement(
                is_supported=True,
                is_valid_session=is_valid_session,
                is_valid_question_range=is_valid_q,
                needs_track=False,
                needs_exam_form=needs_form,
                default_track="",
                min_question=min_q,
                max_question=max_q,
                valid_sessions=valid_sessions,
            )

        # [Era 2] 2015 ~ 2016: Korean A/B, Math A/B, English Integrated
        if 2015 <= year <= 2016:
            needs_form = subj in ("korean", "math")
            return SchemeRequirement(
                is_supported=True,
                is_valid_session=is_valid_session,
                is_valid_question_range=is_valid_q,
                needs_track=False,
                needs_exam_form=needs_form,
                default_track="",
                min_question=min_q,
                max_question=max_q,
                valid_sessions=valid_sessions,
            )

        # [Era 3] 2017 ~ 2021: Korean Integrated, Math Ga/Na, English Integrated
        if 2017 <= year <= 2021:
            needs_form = (subj == "math")
            return SchemeRequirement(
                is_supported=True,
                is_valid_session=is_valid_session,
                is_valid_question_range=is_valid_q,
                needs_track=False,
                needs_exam_form=needs_form,
                default_track="",
                min_question=min_q,
                max_question=max_q,
                valid_sessions=valid_sessions,
            )

        # [Era 4] 2022 ~ 2027: Common + Elective System
        if 2022 <= year <= 2027:
            if subj == "korean":
                is_elective = (q_num >= 35)
                return SchemeRequirement(
                    is_supported=True,
                    is_valid_session=is_valid_session,
                    is_valid_question_range=is_valid_q,
                    needs_track=is_elective,
                    needs_exam_form=False,
                    default_track="" if not is_elective else None,
                    min_question=min_q,
                    max_question=max_q,
                    valid_sessions=valid_sessions,
                )
            elif subj == "math":
                is_elective = (q_num >= 23)
                return SchemeRequirement(
                    is_supported=True,
                    is_valid_session=is_valid_session,
                    is_valid_question_range=is_valid_q,
                    needs_track=is_elective,
                    needs_exam_form=False,
                    default_track="" if not is_elective else None,
                    min_question=min_q,
                    max_question=max_q,
                    valid_sessions=valid_sessions,
                )
            elif subj == "english":
                return SchemeRequirement(
                    is_supported=True,
                    is_valid_session=is_valid_session,
                    is_valid_question_range=is_valid_q,
                    needs_track=False,
                    needs_exam_form=False,
                    default_track="",
                    min_question=min_q,
                    max_question=max_q,
                    valid_sessions=valid_sessions,
                )
            return SchemeRequirement(
                is_supported=True,
                is_valid_session=is_valid_session,
                is_valid_question_range=is_valid_q,
                needs_track=False,
                needs_exam_form=False,
                default_track="",
                min_question=min_q,
                max_question=max_q,
                valid_sessions=valid_sessions,
            )

        # [Era 5] 2028: 2028 Admissions Reform (All Integrated)
        if year == 2028:
            return SchemeRequirement(
                is_supported=True,
                is_valid_session=is_valid_session,
                is_valid_question_range=is_valid_q,
                needs_track=False,
                needs_exam_form=False,
                default_track="",
                min_question=min_q,
                max_question=max_q,
                valid_sessions=valid_sessions,
            )

        return SchemeRequirement(
            is_supported=False,
            is_valid_session=False,
            is_valid_question_range=False,
            needs_track=False,
            needs_exam_form=False,
            default_track=None,
            min_question=0,
            max_question=0,
            valid_sessions=frozenset(),
        )

"""Versioned curriculum identifiers used by format-specific classification."""

PROBABILITY_STATISTICS_CURRICULUM_ID = "curriculum-2022-math-probability-statistics"

PROBABILITY_STATISTICS_UNITS = {
    "1-1": ("ps.counting", "순열과 조합"),
    "1-2": ("ps.binomial-theorem", "이항정리"),
    "2-1": ("ps.probability", "확률의 개념과 활용"),
    "2-2": ("ps.conditional-probability", "조건부확률"),
    "3-1": ("ps.distribution", "확률분포"),
    "3-2": ("ps.estimation", "통계적 추정"),
}


def workbook_toc_classification(section_code, section_title, chapter_title=""):
    """Keep a printed TOC title even when no finer curriculum mapping exists."""
    unit = PROBABILITY_STATISTICS_UNITS.get(section_code)
    if not unit:
        if not chapter_title:
            return {
                "classification_status": "unclassified",
                "classification_method": "unclassified",
                "curriculum_id": "",
                "classification_candidates": [],
                "classification_evidence": [],
            }
        return {
            "classification_status": "confirmed",
            "classification_method": "workbook_toc_confirmed",
            "curriculum_id": "",
            "classification_candidates": [],
            "classification_evidence": [f"printed_toc:chapter:{chapter_title}"],
        }
    unit_code, canonical_title = unit
    return {
        "classification_status": "confirmed",
        "classification_method": "workbook_toc_confirmed",
        "curriculum_id": PROBABILITY_STATISTICS_CURRICULUM_ID,
        "unit_code": unit_code,
        "unit_path": ["확률과 통계", canonical_title],
        "classification_candidates": [],
        "classification_evidence": [
            f"printed_toc:{section_code}",
            f"section_title:{section_title or canonical_title}",
            f"curriculum_unit:{unit_code}",
        ],
    }


def exam_ai_classification_plan(subject):
    """Describe the future candidate path without making an AI call."""
    return {
        "classification_status": "unclassified",
        "classification_method": "curriculum_ai_candidate",
        "curriculum_id": "" if subject != "수학" else PROBABILITY_STATISTICS_CURRICULUM_ID,
        "classification_candidates": [],
        "classification_evidence": [],
    }

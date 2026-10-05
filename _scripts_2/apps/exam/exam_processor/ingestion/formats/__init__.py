"""Registered input formats used by the shared ingestion pipeline."""
from pathlib import Path

from .base import (
    BaseFormatModule,
    BaseSolutionFormatModule,
    FormatRequest,
    FormatResult,
    SolutionFormatRequest,
)
from .korean.korean_notes import KoreanNotesFormat
from .korean.kice_korean import KiceKoreanFormat
from .korean.ebsi_korean_explanation import EBSiKoreanExplanationFormat
from .math.hanwangi import HanwangiFormat
from .english.kice_english import KiceEnglishFormat


FORMATS = {
    module.format_id: module
    for module in (KoreanNotesFormat(), KiceKoreanFormat(), HanwangiFormat(), KiceEnglishFormat())
}

SOLUTION_FORMATS = {
    module.format_id: module
    for module in (EBSiKoreanExplanationFormat(),)
}


def get_format(format_id):
    try:
        return FORMATS[format_id]
    except KeyError as exc:
        raise ValueError(f"등록되지 않은 입력 형식입니다: {format_id}") from exc


def resolve_format(path, format_id=""):
    """Resolve explicit formats first; extension routing is intentionally conservative."""
    if format_id:
        module = get_format(format_id)
        if not module.supports(path):
            raise ValueError(f"선택한 형식({module.label})과 파일 종류가 맞지 않습니다.")
        return module
    # Preserve legacy input routing; workbooks always require an explicit format.
    matches = [module for key, module in FORMATS.items()
               if key not in (HanwangiFormat.format_id, KiceEnglishFormat.format_id)
               and module.supports(Path(path))]
    if not matches:
        raise ValueError("지원하는 입력 형식을 찾지 못했습니다.")
    if len(matches) > 1:
        raise ValueError("입력 형식을 하나로 확정할 수 없습니다. 형식을 직접 선택하세요.")
    return matches[0]


def resolve_solution_format(request):
    matches = [module for module in SOLUTION_FORMATS.values() if module.supports(request)]
    if len(matches) > 1:
        raise ValueError("해설 형식을 하나로 확정할 수 없습니다.")
    return matches[0] if matches else None


__all__ = [
    "BaseFormatModule",
    "BaseSolutionFormatModule",
    "FormatRequest",
    "FormatResult",
    "SolutionFormatRequest",
    "FORMATS",
    "SOLUTION_FORMATS",
    "get_format",
    "resolve_format",
    "resolve_solution_format",
]

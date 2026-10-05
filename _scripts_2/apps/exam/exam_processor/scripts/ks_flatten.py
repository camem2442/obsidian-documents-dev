"""Flatten existing KS vault exam notes into canonical review batches.

The script is intentionally conservative: it never edits the source vault, and
it only calls AICORE when explicitly requested with ``--ai``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import re
from pathlib import Path
import sys
from typing import Any
import unicodedata
import uuid

SCRIPTS_ROOT = Path(__file__).resolve().parents[4]
PROJECT_SOURCE_ROOT = Path(__file__).resolve().parents[5]
for candidate in (PROJECT_SOURCE_ROOT, SCRIPTS_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from _scripts_2 import path_contract, vault_paths

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    OriginDomain,
    ProvenanceDomain,
    QuestionDomain,
    ReviewDomain,
    SourceDomain,
    UnitDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.models import format_output_markdown, yaml_scalar
from _scripts_2.apps.exam.exam_processor.exporters.profiles.markdown import project_to_markdown


SUBJECT_PREFIXES = {
    "1": "국어",
    "2": "문법",
    "3": "영어",
    "4": "수학",
    "5": "탐구",
}

SKIP_PARTS = {
    ".obsidian",
    ".trash",
    "98 script",
    "99 병합",
    "assets",
    "_attachments",
}

EXAM_HINTS = ("기출", "문제")
IMAGE_LINK = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
CHOICE = re.compile(r"^[①②③④⑤]\s*", re.M)
QUESTION_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?\d{1,2}[\.)]\s+", re.M)
COMPACT_CODE = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
ANSWER = re.compile(r"(?:정답|답)\s*[:：]?\s*([①②③④⑤]|[1-5])")
POINTS = re.compile(r"\[(\d+(?:\.\d+)?)점\]")
SET_RANGE = re.compile(r"\[(\d{1,2})\s*[~～-]\s*(\d{1,2})(?:번)?\]")
QUESTION_NUMBER_HEADING = re.compile(
    r"^\s*(?:#{2,4}\s+(?:\*\*)?|\*\*)(\d{1,2})[.]\s+",
    re.MULTILINE,
)
AI_RESPONSE_LINE = re.compile(
    r"^(?:알겠습니다|네[,，.]|네\s|맞습니다|물론입니다)",
    re.MULTILINE,
)
EXAM_CONTEXT_PART = re.compile(
    r"^(?:\d{2}|20\d{2})[_ ]?(?:03|05|06|07|09|10|11)(?:\b|[_ ])"
)
FORMAT_REVIEW_ROLES = {
    "question",
    "question_set",
    "passage",
    "solution",
    "study_note",
    "mixed",
    "unknown",
}
REMOVABLE_AI_SPAN_KINDS = {
    "response_preamble",
    "response_epilogue",
    "persona_frame",
}
SECTION_MARKERS = (
    "<!-- SECTION:PROBLEM_START -->",
    "<!-- SECTION:PROBLEM_END -->",
    "<!-- SECTION:SOLUTION_START -->",
    "<!-- SECTION:SOLUTION_END -->",
)
SEVERITY_RANK = {"info": 0, "warning": 1, "review": 2, "blocker": 3}


@dataclass
class FlattenIssue:
    code: str
    severity: str
    message: str


@dataclass
class FlattenResult:
    source_path: Path
    adapter: str
    record: CanonicalQuestionRecord
    markdown: str
    content_hash: str
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[FlattenIssue] = field(default_factory=list)

    def review_status(self) -> str:
        worst = max((SEVERITY_RANK.get(issue.severity, 1) for issue in self.issues), default=0)
        if worst >= SEVERITY_RANK["blocker"]:
            return "blocked"
        if worst >= SEVERITY_RANK["review"]:
            return "needs_review"
        if worst >= SEVERITY_RANK["warning"]:
            return "warnings"
        return "pass"

    def manifest_entry(self, base: Path, index: int) -> dict[str, Any]:
        rel = self.source_path.relative_to(base).as_posix()
        record_id = self.record.question.question_id
        stem = safe_stem(self.record.question.display_code or f"record-{index:04d}", record_id)
        return {
            "record_id": record_id,
            "display_code": self.record.question.display_code,
            "subject": self.record.question.subject,
            "source_path": rel,
            "json": f"records/{stem}.json",
            "markdown": f"markdown/{stem}.md",
            "content_hash": self.content_hash,
            "adapter": self.adapter,
            "review_status": self.review_status(),
            "metrics": self.metrics,
            "issues": [issue.__dict__ for issue in self.issues],
        }


@dataclass
class FormatReviewResult:
    source_path: Path
    content_hash: str
    heuristic: dict[str, Any]
    ai_review: dict[str, Any] | None = None
    issues: list[FlattenIssue] = field(default_factory=list)

    def review_status(self) -> str:
        if self.ai_review is None:
            return "heuristic_only"
        worst = max((SEVERITY_RANK.get(issue.severity, 1) for issue in self.issues), default=0)
        if worst >= SEVERITY_RANK["blocker"]:
            return "blocked"
        if worst >= SEVERITY_RANK["review"]:
            return "needs_review"
        if worst >= SEVERITY_RANK["warning"]:
            return "reviewed_with_warnings"
        return "reviewed"

    def to_dict(self, vault_root: Path) -> dict[str, Any]:
        return {
            "source_path": self.source_path.relative_to(vault_root).as_posix(),
            "content_hash": self.content_hash,
            "review_status": self.review_status(),
            "heuristic": self.heuristic,
            "ai_review": self.ai_review,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def stable_id(*parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return uuid.uuid5(uuid.NAMESPACE_URL, "ks-flatten:" + payload).hex


def safe_stem(display_code: str, record_id: str) -> str:
    code = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", display_code).strip("-") or "question"
    return f"{code}-{record_id[:8]}"


def default_vault_root() -> Path:
    return vault_paths.default_ks_vault().resolve()


def detect_subject(path: Path, vault_root: Path) -> str:
    try:
        top = path.relative_to(vault_root).parts[0]
    except ValueError:
        top = path.parts[0] if path.parts else ""
    return SUBJECT_PREFIXES.get(top.split(" ", 1)[0], "기타")


def path_track(path: Path, vault_root: Path) -> str:
    try:
        parts = path.relative_to(vault_root).parts
    except ValueError:
        parts = path.parts
    for part in parts[1:-1]:
        if part and not any(hint in part for hint in EXAM_HINTS):
            return re.sub(r"^\d+\s*", "", part).strip()
    return ""


def discover_candidates(vault_root: Path, subjects: set[str] | None = None) -> list[Path]:
    candidates: list[Path] = []
    for path in vault_root.rglob("*.md"):
        raw_parts = path.relative_to(vault_root).parts
        norm_parts = [unicodedata.normalize("NFC", part) for part in raw_parts]
        parts = set(norm_parts)
        if parts & SKIP_PARTS or any(re.match(r"\d+\s*개념$", part) for part in norm_parts):
            continue
        subject = detect_subject(path, vault_root)
        if subject == "기타":
            continue
        if subjects and subject not in subjects:
            continue
        rel = unicodedata.normalize("NFC", path.relative_to(vault_root).as_posix())
        has_code_stem = bool(COMPACT_CODE.search(path.stem) or re.fullmatch(r"\d{1,2}", path.stem.strip()))
        if has_code_stem:
            candidates.append(path)
    return sorted(candidates)


def discover_format_review_candidates(vault_root: Path) -> list[Path]:
    """Collect Korean exam-context notes, including passages and study notes."""
    korean_root = vault_root / "1 국어"
    if not korean_root.is_dir():
        return []
    candidates: list[Path] = []
    for path in korean_root.rglob("*.md"):
        raw_parts = path.relative_to(vault_root).parts
        norm_parts = [unicodedata.normalize("NFC", part) for part in raw_parts]
        if set(norm_parts) & SKIP_PARTS:
            continue
        if "기출" in norm_parts or any(EXAM_CONTEXT_PART.search(part) for part in norm_parts):
            candidates.append(path)
    return sorted(candidates)


def infer_display_code(path: Path, vault_root: Path) -> str:
    set_item = re.fullmatch(r"(\d{4,6})[_\s.-]+(\d{1,2})", path.stem.strip())
    if set_item:
        return f"{set_item.group(1)}-{set_item.group(2).zfill(2)}"
    stem_code = COMPACT_CODE.search(path.stem)
    if stem_code:
        return stem_code.group(1)
    stem_number = re.fullmatch(r"\d{1,2}", path.stem.strip())
    try:
        parts = path.relative_to(vault_root).parts
    except ValueError:
        parts = path.parts
    parent_code = next((m.group(1) for part in reversed(parts[:-1]) if (m := COMPACT_CODE.search(part))), "")
    if parent_code and stem_number:
        return f"{parent_code}-{stem_number.group(0).zfill(2)}"
    return path.stem


def infer_question_number(display_code: str, path: Path) -> str:
    if re.fullmatch(r"\d{6}", display_code):
        return display_code[-2:]
    if "-" in display_code:
        return display_code.rsplit("-", 1)[-1].lstrip("0") or display_code.rsplit("-", 1)[-1]
    if re.fullmatch(r"\d{1,2}", path.stem):
        return path.stem
    return ""


def infer_origin(display_code: str, track: str, question_number: str) -> OriginDomain | None:
    compact = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", display_code)
    split = re.fullmatch(r"(\d{2})(\d{2})-(\d{1,2})", display_code)
    source_only = re.fullmatch(r"(\d{2})(\d{2})", display_code)
    match = compact or split or source_only
    if not match:
        return None
    groups = match.groups()
    yy, session = groups[:2]
    number = groups[2] if len(groups) == 3 else question_number
    authority = "KICE" if session in {"06", "09", "11"} else None
    origin_type = "kice" if authority else "unknown"
    academic_year = f"20{yy}"
    administration_year = str(int(academic_year) - 1) if session in {"06", "09", "11"} else ""
    return OriginDomain(
        type=origin_type,
        academic_year=academic_year,
        administration_year=administration_year,
        session=session,
        authority=authority,
        track=track,
        question_number=question_number or number,
    )


def source_title_from_origin(origin: OriginDomain | None) -> str:
    if not origin:
        return ""
    if origin.session == "06" and origin.authority == "KICE":
        label = "6월 평가원"
    elif origin.session == "09" and origin.authority == "KICE":
        label = "9월 평가원"
    elif origin.session == "11" and origin.authority == "KICE":
        label = "수능"
    else:
        return ""
    return f"{origin.academic_year}학년도 {label}"


def _frontmatter_info(text: str) -> tuple[str, dict[str, Any]]:
    stripped = text.lstrip("\ufeff\n ")
    if not stripped.startswith("---\n"):
        return text, {"present": False, "keys": []}
    end = stripped.find("\n---", 4)
    if end < 0:
        return text, {"present": True, "keys": [], "closed": False}
    block = stripped[4:end]
    keys = sorted(set(re.findall(r"^([A-Za-z_][\w-]*):", block, re.MULTILINE)))
    return stripped[end + 4:].lstrip(), {"present": True, "keys": keys, "closed": True}


def _source_code_from_path(path: Path, vault_root: Path) -> str:
    try:
        parts = path.relative_to(vault_root).parts
    except ValueError:
        parts = path.parts
    for part in reversed(parts):
        normalized = unicodedata.normalize("NFC", part)
        long_match = re.search(r"(?<!\d)20(\d{2})[_ ](03|05|06|07|09|10|11)(?!\d)", normalized)
        if long_match:
            return "".join(long_match.groups())
        short_match = re.search(r"(?<!\d)(\d{2})(03|05|06|07|09|10|11)(?!\d)", normalized)
        if short_match:
            return "".join(short_match.groups())
    return ""


def _filename_question_number(path: Path) -> str:
    match = re.match(r"^(\d{1,2})(?:번|\s|$)", unicodedata.normalize("NFC", path.stem).strip())
    return match.group(1) if match else ""


def _relative_counterparts(path: Path, vault_root: Path) -> list[str]:
    siblings = []
    try:
        for sibling in sorted(path.parent.glob("*.md")):
            if sibling != path:
                siblings.append(sibling.relative_to(vault_root).as_posix())
    except (OSError, ValueError):
        return []
    return siblings[:30]


def heuristic_format_review(path: Path, vault_root: Path, text: str) -> dict[str, Any]:
    content, frontmatter = _frontmatter_info(text)
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    set_match = SET_RANGE.search(content)
    question_numbers = list(dict.fromkeys(QUESTION_NUMBER_HEADING.findall(content)))
    filename_number = _filename_question_number(path)
    if filename_number and filename_number not in question_numbers:
        question_numbers.insert(0, filename_number)
    choice_count = len(CHOICE.findall(content))
    image_count = len(IMAGE_LINK.findall(content))
    has_ai_response = bool(AI_RESPONSE_LINE.search(content))
    stem = unicodedata.normalize("NFC", path.stem)
    analysis_name = bool(re.search(r"분석|스키마|추론|출제\s*의도|실전\s*독해|배경\s*지식|비교|정리|Phase|선지", stem, re.I))

    if re.search(r"해설|정답", stem) and choice_count == 0:
        role, confidence = "solution", 0.94
    elif set_match and choice_count >= 5:
        role, confidence = "question_set", 0.92
    elif set_match:
        role, confidence = "passage", 0.88
    elif filename_number and choice_count >= 5:
        role, confidence = "question", 0.94
    elif choice_count >= 5 and len(question_numbers) >= 2:
        role, confidence = "question_set", 0.82
    elif choice_count >= 5:
        role, confidence = "question", 0.82
    elif analysis_name:
        role, confidence = "study_note", 0.86
    elif has_ai_response:
        role, confidence = "study_note", 0.68
    else:
        role, confidence = "unknown", 0.45

    variants: list[str] = []
    if frontmatter["present"]:
        variants.append("legacy_frontmatter")
    if any(marker in text for marker in SECTION_MARKERS):
        variants.append("extractor_sections")
    if re.match(r"^##\s+20\d{2}학년도\s+", first_line):
        variants.append("source_h2_header")
    elif re.match(r"^#\s+20\d{2}학년도\s+", first_line):
        variants.append("source_h1_title")
    elif re.match(r"^#\s+\[\d+\s*[~～-]\s*\d+", first_line):
        variants.append("h1_set_header")
    elif re.match(r"^##\s+\[\d+\s*[~～-]\s*\d+", first_line):
        variants.append("h2_set_header")
    elif re.match(r"^\[\d+\s*[~～-]\s*\d+", first_line):
        variants.append("plain_set_header")
    elif re.match(r"^#{2,4}\s+\d{1,2}[.]", first_line):
        variants.append("number_heading")
    elif re.match(r"^\*\*\d{1,2}[.]", first_line):
        variants.append("bold_number_heading")
    else:
        variants.append("unstructured_start")
    if set_match:
        variants.append("set_range_present")
    if has_ai_response:
        variants.append("embedded_ai_response")
    if image_count:
        variants.append("image_dependent")

    counterparts = _relative_counterparts(path, vault_root)
    sibling_names = [Path(item).name for item in counterparts]
    if role == "solution" and counterparts:
        variants.append("paired_solution")
    elif role in {"question", "question_set"} and any(re.search(r"해설|정답", name) for name in sibling_names):
        variants.append("paired_problem")

    source_code = _source_code_from_path(path, vault_root)
    question_number = filename_number or (question_numbers[0] if len(question_numbers) == 1 else "")
    display_code = f"{source_code}-{question_number.zfill(2)}" if source_code and question_number else source_code
    origin = infer_origin(display_code, "국어", question_number) if display_code else None
    source_title = source_title_from_origin(origin)
    if role == "question" and source_title and question_number:
        suggested_header = f"## {source_title} {int(question_number)}번"
    elif role in {"question_set", "passage", "solution"} and source_title and set_match:
        suffix = " 지문" if role == "passage" else (" 해설" if role == "solution" else "")
        suggested_header = f"## {source_title} {int(set_match.group(1))}~{int(set_match.group(2))}번{suffix}"
    else:
        suggested_header = ""

    return {
        "role": role,
        "confidence": confidence,
        "format_variants": list(dict.fromkeys(variants)),
        "source_code": source_code,
        "source_title": source_title,
        "suggested_header": suggested_header,
        "question_numbers": question_numbers,
        "question_range": (
            {"start": set_match.group(1), "end": set_match.group(2)} if set_match else None
        ),
        "frontmatter": frontmatter,
        "signals": {
            "choice_count": choice_count,
            "image_count": image_count,
            "has_ai_response": has_ai_response,
            "has_section_markers": any(marker in text for marker in SECTION_MARKERS),
            "first_content_line": first_line[:240],
        },
        "counterparts": counterparts,
    }


def _confidence(value: Any, default: float = 0.0) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def validate_ai_format_review(
    data: dict[str, Any],
    text: str,
    heuristic: dict[str, Any],
) -> tuple[dict[str, Any], list[FlattenIssue]]:
    issues: list[FlattenIssue] = []
    role = str(data.get("role") or "unknown").strip()
    if role not in FORMAT_REVIEW_ROLES:
        issues.append(FlattenIssue("ai_role_invalid", "review", f"허용되지 않은 AICORE 역할: {role}"))
        role = "unknown"
    confidence = _confidence(data.get("confidence"), 0.0)
    if confidence < 0.8:
        issues.append(FlattenIssue("ai_low_confidence", "review", f"AICORE 역할 신뢰도가 낮습니다: {confidence:.2f}"))
    heuristic_role = str(heuristic.get("role") or "unknown")
    if role != heuristic_role and role not in {"mixed", "unknown"} and heuristic_role not in {"mixed", "unknown"}:
        issues.append(
            FlattenIssue(
                "ai_role_disagreement",
                "review",
                f"규칙 판정({heuristic_role})과 AICORE 판정({role})이 다릅니다.",
            )
        )

    normalized_spans = []
    raw_spans = data.get("remove_spans") if isinstance(data.get("remove_spans"), list) else []
    for raw_span in raw_spans:
        if not isinstance(raw_span, dict):
            issues.append(FlattenIssue("ai_span_invalid", "review", "제거 구간 항목이 객체가 아닙니다."))
            continue
        exact_text = str(raw_span.get("exact_text") or "")
        kind = str(raw_span.get("kind") or "").strip()
        span_confidence = _confidence(raw_span.get("confidence"), confidence)
        occurrences = [match.start() for match in re.finditer(re.escape(exact_text), text)] if exact_text else []
        validated = len(occurrences) == 1 and kind in REMOVABLE_AI_SPAN_KINDS
        blocked_reason = ""
        if not exact_text:
            blocked_reason = "empty_text"
        elif kind not in REMOVABLE_AI_SPAN_KINDS:
            blocked_reason = "invalid_kind"
        elif not occurrences:
            blocked_reason = "not_in_source"
        elif len(occurrences) > 1:
            blocked_reason = "ambiguous_occurrence"
        elif len(exact_text) > max(2000, len(text) // 4):
            validated = False
            blocked_reason = "span_too_large"
        elif len(CHOICE.findall(exact_text)) >= 3 or SET_RANGE.search(exact_text):
            validated = False
            blocked_reason = "protected_exam_content"
        if blocked_reason:
            issues.append(
                FlattenIssue(
                    "ai_span_rejected",
                    "review",
                    f"AICORE 제거 후보를 적용 불가로 표시했습니다: {blocked_reason}",
                )
            )
        start = occurrences[0] if len(occurrences) == 1 else None
        normalized_spans.append({
            "kind": kind,
            "exact_text": exact_text,
            "confidence": span_confidence,
            "reason": str(raw_span.get("reason") or "").strip(),
            "validated": validated,
            "start": start,
            "end": start + len(exact_text) if start is not None else None,
            "blocked_reason": blocked_reason,
        })

    substantive = data.get("substantive_start") if isinstance(data.get("substantive_start"), dict) else None
    normalized_substantive = None
    if substantive:
        exact_text = str(substantive.get("exact_text") or "")
        occurrences = [match.start() for match in re.finditer(re.escape(exact_text), text)] if exact_text else []
        validated = len(occurrences) == 1
        if not validated:
            issues.append(
                FlattenIssue(
                    "ai_substantive_start_unverified",
                    "review",
                    "AICORE가 제시한 실질 내용 시작점을 원문에서 유일하게 확인하지 못했습니다.",
                )
            )
        normalized_substantive = {
            "exact_text": exact_text,
            "reason": str(substantive.get("reason") or "").strip(),
            "validated": validated,
            "start": occurrences[0] if validated else None,
        }

    warnings = [str(item).strip() for item in data.get("warnings", []) if str(item).strip()] if isinstance(data.get("warnings"), list) else []
    for warning in warnings:
        issues.append(FlattenIssue("ai_format_warning", "review", warning))

    normalized = {
        "role": role,
        "confidence": confidence,
        "reasoning": str(data.get("reasoning") or "").strip(),
        "format_variants": [str(item) for item in data.get("format_variants", []) if str(item).strip()]
        if isinstance(data.get("format_variants"), list) else [],
        "source_title": str(data.get("source_title") or "").strip(),
        "question_numbers": [str(item) for item in data.get("question_numbers", []) if str(item).strip()]
        if isinstance(data.get("question_numbers"), list) else [],
        "question_range": data.get("question_range") if isinstance(data.get("question_range"), dict) else None,
        "substantive_start": normalized_substantive,
        "remove_spans": normalized_spans,
        "warnings": warnings,
    }
    return normalized, dedupe_issues(issues)


def review_format_with_ai(
    path: Path,
    text: str,
    heuristic: dict[str, Any],
    profile: str | None = None,
    client: Any | None = None,
) -> tuple[dict[str, Any], list[FlattenIssue]]:
    if client is None:
        try:
            from _scripts_2.ai_core.client import AIClient
        except ImportError:  # pragma: no cover
            from ai_core.client import AIClient
        client = AIClient()

    excerpt = text[:24000]
    prompt = f"""
다음 KS 국어 기출 Markdown의 형식과 내용 경계를 검토하세요.
원문을 고쳐 쓰거나 요약하지 말고 반드시 JSON 객체만 반환하세요.

허용 role:
- question: 개별 문항
- question_set: 공통 지문과 여러 문항이 있는 세트
- passage: 지문만 있는 파일
- solution: 별도 해설 파일
- study_note: 스키마, 추론, 출제 의도, 실전 독해 등 학습 노트
- mixed: 위 역할이 분리되지 않고 섞여 있음
- unknown: 판단 불가

중요 판정 원칙:
1. 알겠습니다, 네 맞습니다, 사용자 칭찬, 작업 예고, 역할극 문구는 실질 내용이 아니다.
2. '알겠습니다. 이 문제는 ... 분석하겠습니다.'처럼 이어지는 응답 도입 문단은 전체를 response_preamble 후보로 잡는다.
3. 문제, 선지, 지문, 정답 근거, 실제 풀이, 스키마, 행동 강령은 제거 후보에 포함하지 않는다.
4. remove_spans의 exact_text는 원문에 존재하는 연속 문자열을 글자 그대로 인용한다.
5. 확실하지 않으면 제거 후보를 만들지 말고 warnings에 기록한다.

반환 형식:
{{
  "role": "question|question_set|passage|solution|study_note|mixed|unknown",
  "confidence": 0.0,
  "reasoning": "짧은 판정 근거",
  "format_variants": ["형식 특징"],
  "source_title": "확인 가능한 출처 또는 빈 문자열",
  "question_numbers": ["11"],
  "question_range": {{"start": "10", "end": "13"}},
  "substantive_start": {{"exact_text": "실질 내용이 시작되는 짧은 원문", "reason": "이유"}},
  "remove_spans": [
    {{
      "kind": "response_preamble|response_epilogue|persona_frame",
      "exact_text": "삭제 후보 원문 전체",
      "confidence": 0.0,
      "reason": "삭제 후보인 이유"
    }}
  ],
  "warnings": []
}}

규칙 기반 사전 판정:
{json.dumps(heuristic, ensure_ascii=False, indent=2)}

파일명: {path.name}
원문{' 앞부분' if len(text) > len(excerpt) else ''}:
--- BEGIN SOURCE ---
{excerpt}
--- END SOURCE ---
""".strip()
    raw = client.generate(
        prompt=prompt,
        system_instruction="너는 한국 시험 Markdown의 파일 역할과 원문 경계를 감사하는 검토자다. 유효한 JSON만 출력한다.",
        temperature=0.0,
        task="bulk_text",
        profile=profile,
    )
    data = parse_ai_json(raw)
    return validate_ai_format_review(data, text, heuristic)


def review_format_file(
    path: Path,
    vault_root: Path,
    use_ai: bool = False,
    ai_profile: str | None = None,
    client: Any | None = None,
) -> FormatReviewResult:
    text = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    heuristic = heuristic_format_review(path, vault_root, text)
    ai_review = None
    issues: list[FlattenIssue] = []
    if use_ai:
        try:
            ai_review, ai_issues = review_format_with_ai(path, text, heuristic, ai_profile, client)
            issues.extend(ai_issues)
        except Exception as exc:  # noqa: BLE001 - batch review must continue
            ai_review = {"role": "unknown", "confidence": 0.0, "failure": str(exc)}
            issues.append(FlattenIssue("ai_format_review_failed", "review", f"AICORE 형식 검토 실패: {exc}"))
    return FormatReviewResult(path, content_hash, heuristic, ai_review, dedupe_issues(issues))


def split_body_solution(text: str, subject: str) -> tuple[str, str, list[FlattenIssue]]:
    cleaned = text.strip()
    issues: list[FlattenIssue] = []
    markers = [
        r"\n-{3,}\s*\n\s*###\s+\*\*\[[^\]]*분석\]",
        r"\n###\s+\*\*\[[^\]]*분석\]",
        r"\n###\s+\[?[^\n]*해설\]?",
        r"\n알겠습니다\.",
    ]
    if subject == "문법":
        markers.insert(0, r"\n알겠습니다\.")
    if subject == "영어":
        markers.insert(0, r"\n-{3,}\s*\n\s*###\s+\*\*\[[^\]]*(?:분석|풀이)[^\]]*\]")
    matches = [m for pattern in markers if (m := re.search(pattern, cleaned))]
    if not matches:
        issues.append(FlattenIssue("solution_not_split", "warning", "해설 경계를 찾지 못해 전체를 문제 본문으로 보존했습니다."))
        return cleaned, "", issues
    match = min(matches, key=lambda item: item.start())
    if match.start() < 30:
        issues.append(FlattenIssue("early_solution_marker", "warning", "해설 표지가 너무 앞쪽에 있어 자동 분리를 보류했습니다."))
        return cleaned, "", issues
    body = cleaned[:match.start()].strip().rstrip("-").rstrip()
    solution = cleaned[match.start():].strip().lstrip("-").strip()
    return body, solution, issues


def extract_answer(*texts: str) -> str | None:
    for text in texts:
        match = ANSWER.search(text)
        if match:
            return {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}.get(match.group(1), match.group(1))
    return None


def dedupe_issues(issues: list[FlattenIssue]) -> list[FlattenIssue]:
    seen = set()
    output = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        output.append(issue)
    return output


def review_metrics(source_text: str, body: str, solution: str, markdown: str) -> dict[str, Any]:
    combined = f"{body}\n{solution}"
    answers = [match.group(1) for match in ANSWER.finditer(combined)]
    return {
        "source_chars": len(source_text),
        "body_chars": len(body),
        "solution_chars": len(solution),
        "image_link_count": len(IMAGE_LINK.findall(combined)),
        "choice_count": len(CHOICE.findall(body)),
        "answer_candidate_count": len(answers),
        "question_heading_count": len(QUESTION_HEADING.findall(body)),
        "source_has_frontmatter": source_text.lstrip().startswith("---"),
        "source_has_section_markers": any(marker in source_text for marker in SECTION_MARKERS),
        "markdown_has_all_section_markers": all(marker in markdown for marker in SECTION_MARKERS),
    }


def issue_scan(source_text: str, body: str, solution: str, record: CanonicalQuestionRecord, markdown: str) -> list[FlattenIssue]:
    issues: list[FlattenIssue] = []
    combined = f"{body}\n{solution}"
    image_count = len(IMAGE_LINK.findall(combined))
    text_without_images = IMAGE_LINK.sub("", body).strip()
    answer_candidates = {extract_answer(match.group(0)) for match in ANSWER.finditer(combined)}
    answer_candidates.discard(None)
    if not body.strip():
        issues.append(FlattenIssue("body_empty", "blocker", "문제 본문이 비어 있어 출력 승인이 불가능합니다."))
    elif len(text_without_images) < 20 and not image_count:
        issues.append(FlattenIssue("body_too_short", "review", "문제 본문이 매우 짧아 후보 파일 여부를 확인해야 합니다."))
    if any(marker in source_text for marker in SECTION_MARKERS):
        issues.append(FlattenIssue("source_already_has_section_markers", "review", "원본에 이미 SECTION 마커가 있어 재평탄화 대상인지 확인해야 합니다."))
    if source_text.lstrip().startswith("---"):
        issues.append(FlattenIssue("source_frontmatter_present", "info", "원본 frontmatter는 읽기만 하고 preview metadata를 새로 생성했습니다."))
    if image_count:
        issues.append(FlattenIssue("embedded_assets_not_copied", "warning", "이미지 링크는 본문에 보존했지만 에셋 파일 복사는 하지 않았습니다."))
    if image_count and len(text_without_images) < 40:
        issues.append(FlattenIssue("image_heavy_or_image_only", "review", "이미지 의존도가 높아 원본 대조가 필요합니다."))
    if len(re.findall(r"^\s*\d{1,2}\.", body, re.M)) > 1:
        issues.append(FlattenIssue("multiple_questions_possible", "review", "한 파일 안에 여러 문항 제목이 감지되었습니다."))
    if record.question.display_code and not re.search(r"\d", record.question.display_code):
        issues.append(FlattenIssue("display_code_not_numeric", "review", "표시 코드가 숫자형 기출 코드가 아니어서 후보 파일 여부를 확인해야 합니다."))
    if answer_candidates and record.question.answer not in answer_candidates:
        issues.append(FlattenIssue("answer_mismatch", "review", "추출된 정답 후보와 canonical answer가 다릅니다."))
    if len(answer_candidates) > 1:
        issues.append(FlattenIssue("multiple_answers_possible", "review", "정답 표기가 여러 개 감지되었습니다."))
    if not CHOICE.search(body):
        issues.append(FlattenIssue("choices_not_detected", "info", "선지 패턴을 찾지 못했습니다."))
    if not solution.strip():
        issues.append(FlattenIssue("solution_empty", "info", "해설 영역이 비어 있습니다."))
    if not all(marker in markdown for marker in SECTION_MARKERS):
        issues.append(FlattenIssue("markdown_section_markers_missing", "blocker", "preview Markdown의 SECTION 마커가 누락되었습니다."))
    return dedupe_issues(issues)


def build_record(path: Path, vault_root: Path, body: str, solution: str, content_hash: str) -> CanonicalQuestionRecord:
    subject = detect_subject(path, vault_root)
    track = path_track(path, vault_root)
    display_code = infer_display_code(path, vault_root)
    question_number = infer_question_number(display_code, path)
    origin = infer_origin(display_code, track, question_number)
    source_id = stable_id("source", subject, display_code[:4], track)
    question_id = stable_id("question", path.relative_to(vault_root).as_posix(), content_hash)
    source_title = source_title_from_origin(origin) or f"KS 기존 기출 {display_code}"
    return CanonicalQuestionRecord(
        question=QuestionDomain(
            question_id=question_id,
            display_code=display_code,
            subject=subject,
            question_number=question_number,
            points=float(m.group(1)) if (m := POINTS.search(body)) else None,
            answer=extract_answer(solution, body),
        ),
        source=SourceDomain(
            source_id=source_id,
            type="exam" if origin else "other",
            title=source_title,
        ),
        origin=origin,
        unit=UnitDomain(display_name=track or None, path=[track] if track else []),
        provenance=ProvenanceDomain(
            revision_id=content_hash[:12],
            source_record_ids=[path.relative_to(vault_root).as_posix()],
            source_hashes=[content_hash],
        ),
        review=ReviewDomain(
            transcription="completed" if body.strip() else "pending",
            source_audit="pending",
            classification="not_started",
            human_approval="pending",
        ),
        body=body,
        solution=solution,
    )


def parse_ai_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def refine_with_ai(text: str, subject: str, body: str, solution: str, profile: str | None = None) -> tuple[str, str, list[FlattenIssue]]:
    try:
        from _scripts_2.ai_core.client import AIClient
    except ImportError:  # pragma: no cover
        from ai_core.client import AIClient

    prompt = f"""
다음은 KS 볼트의 기존 {subject} 기출 Markdown입니다.
원문을 축약하거나 고치지 말고 JSON만 반환하세요.

필드:
- body: 문제 원문. 해설/분석/학습 메모는 제외.
- solution: 해설/분석/학습 메모. 없으면 빈 문자열.
- warnings: 자동 분리가 불확실한 이유 목록.

현재 휴리스틱 body:
{body[:3000]}

현재 휴리스틱 solution:
{solution[:3000]}

전체 원문:
{text[:12000]}
""".strip()
    raw = AIClient().generate(
        prompt,
        system_instruction="너는 시험 문제 Markdown 정리 도우미다. 반드시 유효한 JSON만 출력한다.",
        temperature=0,
        task="bulk_text",
        profile=profile,
    )
    try:
        data = parse_ai_json(raw)
    except Exception as exc:  # noqa: BLE001 - keep CLI resilient
        return body, solution, [FlattenIssue("ai_json_parse_failed", "warning", f"AICORE 응답 JSON 파싱 실패: {exc}")]
    ai_body = str(data.get("body") or "").strip()
    ai_solution = str(data.get("solution") or "").strip()
    issues = [
        FlattenIssue("ai_warning", "review", str(warning))
        for warning in data.get("warnings", [])
        if str(warning).strip()
    ]
    if not ai_body:
        issues.append(FlattenIssue("ai_empty_body", "warning", "AICORE가 빈 문제 본문을 반환해 휴리스틱 결과를 유지했습니다."))
        return body, solution, issues
    return ai_body, ai_solution, issues


def flatten_file(path: Path, vault_root: Path, use_ai: bool = False, ai_profile: str | None = None) -> FlattenResult:
    text = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    subject = detect_subject(path, vault_root)
    body, solution, issues = split_body_solution(text, subject)
    if use_ai:
        body, solution, ai_issues = refine_with_ai(text, subject, body, solution, ai_profile)
        issues.extend(ai_issues)
    record = build_record(path, vault_root, body, solution, content_hash)
    markdown = project_to_markdown(record)
    metrics = review_metrics(text, body, solution, markdown)
    issues.extend(issue_scan(text, body, solution, record, markdown))
    return FlattenResult(path, subject, record, markdown, content_hash, metrics, dedupe_issues(issues))


def write_batch(results: list[FlattenResult], vault_root: Path, output_dir: Path) -> dict[str, Any]:
    records_dir = output_dir / "records"
    markdown_dir = output_dir / "markdown"
    records_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "0.3.0",
        "kind": "ks-flatten-preview",
        "vault_root": str(vault_root),
        "records": [],
    }
    for index, result in enumerate(results, start=1):
        entry = result.manifest_entry(vault_root, index)
        (output_dir / entry["json"]).write_text(result.record.to_json() + "\n", encoding="utf-8")
        (output_dir / entry["markdown"]).write_text(result.markdown, encoding="utf-8")
        manifest["records"].append(entry)
    (output_dir / "canonical-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def summarize(results: list[FlattenResult], vault_root: Path) -> dict[str, Any]:
    by_subject: dict[str, int] = {}
    issues: dict[str, int] = {}
    by_review_status: dict[str, int] = {}
    display_paths: dict[str, list[str]] = {}
    samples = []
    for result in results:
        by_subject[result.record.question.subject] = by_subject.get(result.record.question.subject, 0) + 1
        by_review_status[result.review_status()] = by_review_status.get(result.review_status(), 0) + 1
        display_paths.setdefault(result.record.question.display_code, []).append(result.source_path.relative_to(vault_root).as_posix())
        for issue in result.issues:
            issues[issue.code] = issues.get(issue.code, 0) + 1
        if len(samples) < 12:
            samples.append({
                "path": result.source_path.relative_to(vault_root).as_posix(),
                "display_code": result.record.question.display_code,
                "subject": result.record.question.subject,
                "review_status": result.review_status(),
                "metrics": result.metrics,
                "issues": [issue.code for issue in result.issues],
            })
    duplicate_display_codes = {
        code: paths for code, paths in sorted(display_paths.items())
        if code and len(paths) > 1
    }
    return {
        "count": len(results),
        "by_subject": by_subject,
        "by_review_status": by_review_status,
        "issues": issues,
        "duplicate_display_codes": duplicate_display_codes,
        "samples": samples,
    }


def summarize_format_reviews(results: list[FormatReviewResult], vault_root: Path) -> dict[str, Any]:
    by_heuristic_role: dict[str, int] = {}
    by_ai_role: dict[str, int] = {}
    by_review_status: dict[str, int] = {}
    issues: dict[str, int] = {}
    for result in results:
        heuristic_role = str(result.heuristic.get("role") or "unknown")
        by_heuristic_role[heuristic_role] = by_heuristic_role.get(heuristic_role, 0) + 1
        if result.ai_review is not None:
            ai_role = str(result.ai_review.get("role") or "unknown")
            by_ai_role[ai_role] = by_ai_role.get(ai_role, 0) + 1
        status = result.review_status()
        by_review_status[status] = by_review_status.get(status, 0) + 1
        for issue in result.issues:
            issues[issue.code] = issues.get(issue.code, 0) + 1
    return {
        "count": len(results),
        "by_heuristic_role": by_heuristic_role,
        "by_ai_role": by_ai_role,
        "by_review_status": by_review_status,
        "issues": issues,
        "samples": [result.to_dict(vault_root) for result in results[:12]],
    }


def write_format_review_report(
    results: list[FormatReviewResult],
    vault_root: Path,
    output_dir: Path,
    ai_enabled: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "format-review.json"
    payload = {
        "schema_version": "1",
        "kind": "ks-korean-format-review",
        "vault_root": str(vault_root),
        "ai_enabled": ai_enabled,
        "reviews": [result.to_dict(vault_root) for result in results],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def resolve_requested_paths(vault_root: Path, requested: list[Path]) -> list[Path]:
    resolved: list[Path] = []
    for raw in requested:
        path = raw.expanduser()
        if not path.is_absolute():
            path = vault_root / path
        path = path.resolve()
        try:
            path.relative_to(vault_root)
        except ValueError as exc:
            raise ValueError(f"검토 경로가 KS 볼트 밖에 있습니다: {path}") from exc
        if not path.is_file() or path.suffix.lower() != ".md":
            raise ValueError(f"검토할 Markdown 파일을 찾을 수 없습니다: {path}")
        resolved.append(path)
    return list(dict.fromkeys(resolved))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a KS exam-note flattening preview batch.")
    parser.add_argument("--mode", choices=("flatten", "review-formats"), default="flatten")
    parser.add_argument("--vault-root", type=Path, help="Explicit KS vault folder (not its Documents parent)")
    parser.add_argument("--subject", action="append", choices=sorted(SUBJECT_PREFIXES.values()))
    parser.add_argument("--path", action="append", type=Path, default=[], help="Review an explicit path relative to the KS root.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=Path, default=Path("tmp/ks_flatten_preview"))
    parser.add_argument("--write", action="store_true", help="Write canonical JSON/Markdown preview files.")
    parser.add_argument("--ai", action="store_true", help="Use AICORE to refine body/solution splitting.")
    parser.add_argument("--ai-profile", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    vault_root = path_contract.vault_root(explicit=args.vault_root if args.vault_root is not None else default_vault_root())
    if args.mode == "review-formats":
        paths = resolve_requested_paths(vault_root, args.path) if args.path else discover_format_review_candidates(vault_root)
        if args.limit >= 0:
            paths = paths[:args.limit]
        reviews = [
            review_format_file(path, vault_root, use_ai=args.ai, ai_profile=args.ai_profile)
            for path in paths
        ]
        report = summarize_format_reviews(reviews, vault_root)
        if args.write:
            report_path = write_format_review_report(reviews, vault_root, args.output, args.ai)
            report["output"] = str(report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report)
        return 0

    subjects = set(args.subject) if args.subject else None
    paths = discover_candidates(vault_root, subjects=subjects)
    if args.limit >= 0:
        paths = paths[:args.limit]
    results = [flatten_file(path, vault_root, use_ai=args.ai, ai_profile=args.ai_profile) for path in paths]
    report = summarize(results, vault_root)
    if args.write:
        manifest = write_batch(results, vault_root, args.output)
        report["output"] = str(args.output)
        report["manifest_count"] = len(manifest["records"])
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

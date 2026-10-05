"""Validate canonical dictionaries against specifications.md rules."""
from __future__ import annotations

from dataclasses import is_dataclass
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Union, get_args, get_origin, get_type_hints

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import CanonicalQuestionRecord, SCHEMA_VERSION, SOURCE_AUDIT_VALUES


def _type_errors(value, annotation, path):
    """Check dataclass fields recursively without coercing identifiers or dropping keys."""
    if annotation is Any:
        return []
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Union:
        if any(not _type_errors(value, option, path) for option in args):
            return []
        return [f"{path}: invalid type or value"]
    if is_dataclass(annotation):
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        hints = get_type_hints(annotation)
        errors = [f"{path}.{key}: unknown field" for key in value if key not in hints]
        for key in value.keys() & hints.keys():
            errors.extend(_type_errors(value[key], hints[key], f"{path}.{key}"))
        return errors
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        return [error for i, entry in enumerate(value)
                for error in _type_errors(entry, args[0], f"{path}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        return [error for key, entry in value.items()
                for error in (_type_errors(key, args[0], path) + _type_errors(entry, args[1], path))]
    valid = (type(value) in (int, float) and math.isfinite(value)) if annotation is float else type(value) is annotation
    return [] if valid else [f"{path}: invalid type or value"]


def validate_canonical_dict(data: Dict[str, Any]) -> List[str]:
    """Validate canonical dictionary against specifications.md rules."""
    errors = []
    if not isinstance(data, dict):
        return ["Canonical record must be a dict."]

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"Invalid schema_version: {data.get('schema_version')} (expected {SCHEMA_VERSION})")

    for key in ("question", "source", "unit", "relations", "provenance", "classification", "review"):
        if key not in data or not isinstance(data[key], dict):
            errors.append(f"Missing or non-dict top-level domain: {key}")
    if "assets" not in data or not isinstance(data["assets"], list):
        errors.append("Missing or non-list top-level domain: assets")

    errors.extend(_type_errors(data, CanonicalQuestionRecord, "record"))
    if errors:
        return errors

    q = data.get("question", {})
    if not q.get("question_id"):
        errors.append("question.question_id is required.")
    if not q.get("display_code"):
        errors.append("question.display_code is required.")
    if not q.get("subject"):
        errors.append("question.subject is required.")

    s = data.get("source", {})
    if not s.get("source_id"):
        errors.append("source.source_id is required.")
    if not s.get("type"):
        errors.append("source.type is required.")
    if "title" not in s:
        errors.append("source.title is required.")

    if isinstance(data.get("origin"), dict) and not data["origin"].get("type"):
        errors.append("origin.type is required when origin is present.")
    if q.get("content_type") == "variant" and not isinstance(data.get("origin"), dict):
        errors.append("variant questions require an origin.")

    enums = {
        ("question", "content_kind"): {"question", "passage", "solution", "script", "concept", "example"},
        ("question", "status"): {"unread", "solved", "incorrect", "mastered"},
        ("question", "content_type"): {"", "past_exam", "original", "variant"},
        ("question", "modality"): {"", "listening", "reading"},
        ("source", "type"): {"exam", "workbook", "solution", "other"},
        ("origin", "type"): {"kice", "police", "leet", "mock", "unknown"},
        ("classification", "status"): {"unclassified", "candidate", "confirmed"},
        ("classification", "method"): {None, "workbook_toc", "curriculum_ai", "manual"},
        ("review", "transcription"): {"pending", "completed", "failed"},
        ("review", "source_audit"): SOURCE_AUDIT_VALUES,
        ("review", "classification"): {"not_started", "candidate", "confirmed"},
        ("review", "human_approval"): {"pending", "approved", "rejected"},
    }
    for (domain, key), allowed in enums.items():
        domain_data = data.get(domain)
        if isinstance(domain_data, dict) and key in domain_data and domain_data[key] not in allowed:
            errors.append(f"{domain}.{key}: unsupported value")
    if q.get("points") is not None and q["points"] < 0:
        errors.append("question.points must be non-negative")
    if q.get("correct_rate") is not None and not 0 <= q["correct_rate"] <= 1:
        errors.append("question.correct_rate must be between 0 and 1")
    if any(page < 1 for page in s.get("locator", {}).get("pdf_pages", [])):
        errors.append("source.locator.pdf_pages must be one-based")
    asset_ids = set()
    asset_paths = set()
    for asset in data.get("assets", []):
        if asset.get("asset_id") in asset_ids:
            errors.append("assets.asset_id values must be unique within a record")
        asset_ids.add(asset.get("asset_id"))
        if asset.get("path") in asset_paths:
            errors.append("assets.path values must be unique within a record")
        asset_paths.add(asset.get("path"))
        if asset.get("section") not in ("body", "solution"):
            errors.append("assets.section must be body or solution")
        for key in ("source_path", "path"):
            value = asset.get(key, "")
            candidate = PurePosixPath(value) if isinstance(value, str) else None
            if (not value or candidate is None or not candidate.parts or candidate.is_absolute()
                    or ".." in candidate.parts or "\\" in value):
                errors.append(f"assets.{key} must be a safe relative path")
        sha256 = asset.get("sha256")
        if sha256 is not None and (not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
            errors.append("assets.sha256 must be a lowercase SHA-256 digest or null")
        if not asset.get("media_type"):
            errors.append("assets.media_type is required")
        if not isinstance(asset.get("description", ""), str):
            errors.append("assets.description must be a string")
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError):
        errors.append("Record must contain finite JSON values")

    return errors

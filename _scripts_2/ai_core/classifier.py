"""Generic AI text classifier — reusable across tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from .client import AIClient


@dataclass
class Category:
    id: str
    name: str
    description: str


@dataclass
class ClassificationResult:
    category_id: str
    confidence: float
    reasoning: str


import unicodedata

_SYSTEM_PROMPT = """You are a document classification assistant.
Given a document excerpt and a list of categories, determine which category best matches.
Respond ONLY with valid JSON:
{"category_id": "<id>", "confidence": <0.0-1.0>, "reasoning": "<brief reason in Korean>"}
Do not include any other text outside the JSON."""


class AIClassifier:
    """Generic text classifier using AIClient. Reusable across tools."""

    def __init__(self, client: Optional[AIClient] = None):
        self._client = client or AIClient()

    def classify(self, text: str, categories: List[Category]) -> ClassificationResult:
        if not categories:
            raise ValueError("At least one category is required.")
        cat_lines = "\n".join(
            f"{i + 1}. [{c.id}] {c.name}: {c.description}"
            for i, c in enumerate(categories)
        )
        prompt = f"""다음 문서 텍스트를 아래 카테고리 중 하나로 분류하세요.

카테고리 목록:
{cat_lines}

문서 텍스트:
\"\"\"
{text[:3000]}
\"\"\"

JSON으로만 응답하세요."""
        raw = self._client.generate(prompt=prompt, system_instruction=_SYSTEM_PROMPT, temperature=0.0)
        return self._parse(raw, categories)

    def _parse(self, raw: str, categories: List[Category]) -> ClassificationResult:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                raw_cat_id = str(data.get("category_id", "")).strip()
                cat_id_norm = unicodedata.normalize('NFC', raw_cat_id)
                reasoning = unicodedata.normalize('NFC', str(data.get("reasoning", "")))

                # Lookup mappings
                id_map = {unicodedata.normalize('NFC', c.id): c.id for c in categories}
                name_map = {unicodedata.normalize('NFC', c.name): c.id for c in categories}

                matched_id = None
                # 1. Direct ID match
                if cat_id_norm in id_map:
                    matched_id = id_map[cat_id_norm]
                # 2. Direct Name match
                elif cat_id_norm in name_map:
                    matched_id = name_map[cat_id_norm]
                # 3. 1-based Index match
                elif cat_id_norm.isdigit():
                    idx = int(cat_id_norm) - 1
                    if 0 <= idx < len(categories):
                        matched_id = categories[idx].id
                # 4. Substring match in ID or Name
                if not matched_id:
                    for c in categories:
                        cid_n = unicodedata.normalize('NFC', c.id)
                        cname_n = unicodedata.normalize('NFC', c.name)
                        if (cid_n and cid_n in cat_id_norm) or (cname_n and cname_n in cat_id_norm):
                            matched_id = c.id
                            break
                # 5. Check if category name/ID is mentioned in reasoning
                if not matched_id and reasoning:
                    for c in categories:
                        cid_n = unicodedata.normalize('NFC', c.id)
                        cname_n = unicodedata.normalize('NFC', c.name)
                        if (cid_n and cid_n in reasoning) or (cname_n and cname_n in reasoning):
                            matched_id = c.id
                            break

                final_id = matched_id if matched_id else categories[0].id
                return ClassificationResult(
                    category_id=final_id,
                    confidence=min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
                    reasoning=reasoning,
                )
            except Exception:
                pass
        return ClassificationResult(category_id=categories[0].id, confidence=0.1, reasoning="분류 실패")


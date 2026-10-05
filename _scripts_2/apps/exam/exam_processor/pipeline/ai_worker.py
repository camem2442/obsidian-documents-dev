"""Bounded subprocess adapter for AI Core. Never return provider error bodies."""
import contextlib
import json
import re
import sys

try:
    from _scripts_2.ai_core import AIClient
    from _scripts_2.ai_core.usage_tracker import get_today_summary
except ImportError:
    from ai_core import AIClient
    from ai_core.usage_tracker import get_today_summary
from ..domain.rich_content import markup_errors


def response_schema(operation):
    string = {"type": "STRING"}
    strings = {"type": "ARRAY", "items": string}
    if operation == "audit":
        issue = {"type": "OBJECT", "properties": {
            key: string for key in ("location", "original", "extracted", "message", "suggestion")
        }, "required": ["location", "original", "extracted", "message", "suggestion"]}
        return {"type": "OBJECT", "properties": {
            "result": {"type": "STRING", "enum": ["no_difference", "suspected_difference", "unreadable"]},
            "issues": {"type": "ARRAY", "items": issue},
        }, "required": ["result", "issues"]}

    structured = {"type": "OBJECT", "properties": {
        "title": string,
        "texts": strings,
        "tables": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "headers": strings,
            "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": string}},
        }, "required": ["headers", "rows"]}},
        "symbols": strings,
        "units": strings,
        "source": string,
    }, "required": ["title", "texts", "tables", "symbols", "units", "source"]}
    diagram = {"type": "OBJECT", "properties": {
        "id": string,
        "region": {"type": "INTEGER"},
        "box": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "description": string,
        "markdown": string,
        "structured": structured,
    }, "required": ["id", "region", "box", "description", "markdown"]}
    return {"type": "OBJECT", "properties": {
        "body": string,
        "diagrams": {"type": "ARRAY", "items": diagram},
    }, "required": ["body", "diagrams"]}


def parse_json(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Empty response")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Expected object")
    return result


def validate(result, operation, count):
    if operation == "audit":
        if result.get("result") not in ("no_difference", "suspected_difference", "unreadable"):
            raise ValueError("Invalid audit result")
        issues = result.get("issues")
        if not isinstance(issues, list) or any(not isinstance(i, dict) or not all(isinstance(i.get(k), str) for k in ("location", "original", "extracted", "message", "suggestion")) for i in issues):
            raise ValueError("Invalid audit issues")
        if result["result"] == "no_difference" and issues:
            raise ValueError("Contradictory audit")
    else:
        if not isinstance(result.get("body"), str) or not result["body"].strip():
            raise ValueError("Missing body")
        if markup_errors(result["body"]):
            raise ValueError("Invalid body markup")
        diagrams = result.get("diagrams")
        if not isinstance(diagrams, list):
            raise ValueError("Missing diagrams")
        ids = set()
        for diagram in diagrams:
            if not isinstance(diagram, dict):
                raise ValueError("Invalid diagram")
            name, region, box = diagram.get("id"), diagram.get("region"), diagram.get("box")
            if not isinstance(name, str) or not re.fullmatch(r"fig[0-9]+", name) or name in ids:
                raise ValueError("Invalid diagram id")
            if type(region) is not int or not 0 <= region < count:
                raise ValueError("Invalid region")
            if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box) or not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
                raise ValueError("Invalid box")
            markdown = diagram.get("markdown")
            if not isinstance(markdown, str) or not markdown.strip():
                raise ValueError("Missing diagram transcription")
            description = diagram.get("description")
            if not isinstance(description, str):
                raise ValueError("Missing diagram description")
            if re.search(r"!\[|\{\{diagram:|<!--\s*(?:MATERIAL|SECTION):", markdown) or markup_errors(markdown):
                raise ValueError("Invalid diagram transcription")
            structured = diagram.get("structured")
            if structured is not None:
                if not isinstance(structured, dict) or not isinstance(structured.get("title", ""), str):
                    raise ValueError("Invalid material structure")
                for key in ("texts", "symbols", "units"):
                    if not isinstance(structured.get(key, []), list) or any(not isinstance(v, str) for v in structured.get(key, [])):
                        raise ValueError("Invalid material structure")
                if not isinstance(structured.get("source", ""), str) or not isinstance(structured.get("tables", []), list):
                    raise ValueError("Invalid material structure")
                for table in structured.get("tables", []):
                    if not isinstance(table, dict) or not isinstance(table.get("headers", []), list) or not isinstance(table.get("rows", []), list):
                        raise ValueError("Invalid material table")
                    if any(not isinstance(v, str) for v in table.get("headers", [])) or any(not isinstance(row, list) or any(not isinstance(v, str) for v in row) for row in table.get("rows", [])):
                        raise ValueError("Invalid material table")
            ids.add(name)
        slots = re.findall(r"\{\{diagram:(.*?)\}\}", result["body"])
        if len(slots) != len(ids) or set(slots) != ids:
            raise ValueError("Unbound diagram")
    return result


def failure_label(exc):
    """Return a safe local diagnostic without exposing provider response bodies."""
    message = str(exc)
    if isinstance(exc, json.JSONDecodeError) or message in {
        "Empty response", "Expected object", "Invalid audit result",
        "Invalid audit issues", "Contradictory audit",
    } or message.startswith(("Invalid ", "Missing ", "Unbound ")):
        return f"response_validation:{message}"
    if "429" in message or "rate limit" in message.lower() or "quota" in message.lower():
        return "provider_rate_limit_or_quota"
    if ("retries exhausted" in message.lower()
            or "exhausted without" in message.lower()
            or "keys and models exhausted" in message.lower()):
        return "provider_retries_exhausted"
    if isinstance(exc, TimeoutError) or "timed out" in message.lower() or "timeout" in message.lower():
        return "provider_timeout"
    return f"provider_error:{type(exc).__name__}"


def public_error(exc):
    """Expose safe provider/validation categories without leaking raw API errors."""
    message = str(exc)
    if (isinstance(exc, ValueError)
            and message.startswith("AI 제공자 또는 응답 검증 실패 (")
            and message.endswith("키 상태와 사용 한도를 확인하세요.")):
        return message
    return f"AI 작업 실패 ({failure_label(exc)})"


def run(payload):
    client = AIClient(profile="reasoning")
    images = payload.get("images", [])
    if len(images) > 5:
        raise ValueError("한 번에 5개를 초과하는 원본 영역은 현재 지원하지 않습니다.")

    before = get_today_summary()
    args = dict(
        prompt=payload["prompt"],
        temperature=0,
        max_tokens=14000,
        task="deep_reasoning",
        response_mime_type="application/json",
        response_schema=response_schema(payload["operation"]),
    )
    try:
        raw = client.generate_images(image_paths=images, **args) if images else client.generate(**args)
        result = validate(parse_json(raw), payload["operation"], len(images))
        meta = client.last_generation_meta or {}
        after = get_today_summary()
        usage = {
            "input_tokens": meta.get("input_tokens", 0),
            "output_tokens": meta.get("output_tokens", 0),
            "total_tokens": meta.get("total_tokens", 0),
            "model": meta.get("actual_model") or meta.get("model") or "gemini-3.7-flash",
            "retry_count": 1 if meta.get("fallback_used") else 0,
            "attempts": 2 if meta.get("fallback_used") else 1,
        }
        for name, current in after.items():
            previous = before.get(name, {})
            diff_in = max(0, current.get("prompt_tokens", 0) - previous.get("prompt_tokens", 0))
            diff_out = max(0, current.get("completion_tokens", 0) - previous.get("completion_tokens", 0))
            diff_tot = max(0, current.get("total_tokens", 0) - previous.get("total_tokens", 0))
            if diff_tot > 0:
                usage["input_tokens"] = diff_in
                usage["output_tokens"] = diff_out
                usage["total_tokens"] = diff_tot
                if name in current.get("models", {}):
                    usage["model"] = next(iter(current["models"])) if len(current["models"]) == 1 else usage["model"]

        provider_name = meta.get("provider", "gemini")
        return {"data": result, "provider": provider_name, "configured_model": meta.get("requested_model", "gemini-3.7-flash"), "usage": usage}
    except Exception as exc:
        raise ValueError(f"AI 제공자 또는 응답 검증 실패 ({failure_label(exc)}). 키 상태와 사용 한도를 확인하세요.") from exc



def main():
    payload = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            response = run(payload)
        print(json.dumps(response, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": public_error(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

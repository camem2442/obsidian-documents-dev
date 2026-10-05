"""Google Gemini LLM Provider."""

import json
import os
import sys
import time
import base64
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Iterator
from typing import Sequence
from typing import Any, Dict, List, Optional

from ..keychain import get_api_key
from ..stream_usage import StreamUsageAttempt
from .base import BaseLLMProvider

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
FALLBACK_GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]


def _candidate_models(req_model: str) -> List[str]:
    if req_model == "gemini-3.8-flash":
        return ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    if req_model == "gemini-3.7-flash":
        return ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    if req_model == "gemini-3.6-flash":
        return ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    if req_model == "gemini-3.5-flash":
        return ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    return [req_model]


def _text_parts_from_stream_event(parsed: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for cand in parsed.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict) or part.get("thought"):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                out.append(text)
    return out


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API Provider with automatic Keychain resolution."""

    def __init__(self, api_key: Optional[str] = None, api_keys: Optional[List[str]] = None, model: str = DEFAULT_GEMINI_MODEL):
        if api_keys:
            keys = [k.strip() for k in api_keys if k and len(k.strip()) > 5]
        elif api_key:
            keys = [api_key.strip()]
        else:
            from ..keychain import get_api_keys
            keys = get_api_keys("gemini")

        self.api_keys: List[str] = keys
        self.current_key_idx: int = 0
        primary = self.api_keys[0] if self.api_keys else ""
        super().__init__(api_key=primary, model=model)

    @property
    def provider_name(self) -> str:
        return "gemini"

    def is_available(self) -> bool:
        return bool(self.api_keys and len(self.api_keys) > 0)

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        if not self.is_available():
            raise ValueError("Gemini API Key is not configured. Save it to Keychain or set GEMINI_API_KEY.")

        req_model = kwargs.get("model") or self.model
        thinking_budget = kwargs.get("thinking_budget")

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
            }
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if thinking_budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}

        response_schema = kwargs.get("response_schema")
        response_mime_type = kwargs.get("response_mime_type")
        if response_schema:
            payload["generationConfig"]["responseMimeType"] = response_mime_type or "application/json"
            payload["generationConfig"]["responseSchema"] = response_schema
        elif response_mime_type:
            payload["generationConfig"]["responseMimeType"] = response_mime_type

        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        total_keys = len(self.api_keys)
        max_retries = kwargs.get("max_retries", max(3, total_keys * 2))

        candidate_models = _candidate_models(req_model)

        for active_model in candidate_models:
            active_payload = json.loads(json.dumps(payload))
            if active_model in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash") and thinking_budget is None:
                # Provide a modest thinking budget so thinking models do not consume all output tokens
                active_payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 64}
            data = json.dumps(active_payload).encode("utf-8")
            for attempt in range(max_retries):
                current_key = self.api_keys[self.current_key_idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_model}:generateContent?key={current_key}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "StudyVault-AICore/1.0",
                    },
                    data=data,
                )

                try:
                    with urllib.request.urlopen(req, timeout=40) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        usage = res.get("usageMetadata", {})
                        prompt_tokens = usage.get("promptTokenCount", 0)
                        completion_tokens = usage.get("candidatesTokenCount", 0)
                        total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)
                        try:
                            from ..usage_tracker import record_usage
                            record_usage("gemini", active_model, prompt_tokens, completion_tokens, total_tokens)
                        except Exception:
                            pass

                        candidates = res.get("candidates", [])
                        if candidates and "content" in candidates[0]:
                            parts = candidates[0]["content"].get("parts", [])
                            if parts:
                                text = "".join(
                                    part.get("text", "") for part in parts
                                    if isinstance(part, dict) and not part.get("thought", False)
                                )
                                if isinstance(text, str) and text.strip():
                                    return text.strip()
                        raise RuntimeError(f"Gemini API ({active_model}) returned no usable text.")
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504):
                        if total_keys > 1 and e.code == 429:
                            old_idx = self.current_key_idx
                            self.current_key_idx = (self.current_key_idx + 1) % total_keys
                            print(f"  [Gemini 429 Limit] Key #{old_idx+1} reached quota ({active_model}). Failover to Key #{self.current_key_idx+1}/{total_keys}...", file=sys.stderr, flush=True)
                            time.sleep(0.5)
                            continue
                        elif attempt < max_retries - 1:
                            wait = 1.5 * (1.5 ** attempt)
                            reason = "429 Rate Limit" if e.code == 429 else f"{e.code} Server Error"
                            print(f"  [Gemini {reason}] Retrying in {wait:.1f}s ({active_model}, attempt {attempt+1}/{max_retries})...", file=sys.stderr, flush=True)
                            time.sleep(wait)
                            continue
                        else:
                            # Break inner loop to try next candidate model
                            break
                    else:
                        err_msg = e.read().decode("utf-8", errors="ignore")
                        raise RuntimeError(f"Gemini API Error {e.code}: {err_msg}")
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(1.5)
                    else:
                        break
            if len(candidate_models) > 1 and active_model != candidate_models[-1]:
                next_model = candidate_models[candidate_models.index(active_model) + 1]
                print(f"  [Gemini Model Fallback] '{active_model}' unavailable. Falling back to '{next_model}'...", file=sys.stderr, flush=True)

        raise RuntimeError(f"Gemini API retries exhausted without a usable response for models {candidate_models}.")


    def generate_images(
        self,
        image_paths: Sequence[Path | str],
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
        thinking_budget: Optional[int] = None,
        response_mime_type: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Send local images and text to Gemini using inlineData parts."""
        if not self.is_available():
            raise ValueError("Gemini API Key is not configured. Save it to Keychain or set GEMINI_API_KEY.")
        paths = [Path(path).expanduser().resolve() for path in image_paths]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"Missing image files for vision request: {missing}")

        parts: List[Dict[str, Any]] = []
        for path in paths:
            mime_type, _ = mimetypes.guess_type(str(path))
            if not mime_type:
                mime_type = "image/png"
            with open(path, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode("ascii")
            parts.append({"inlineData": {"mimeType": mime_type, "data": data_b64}})

        parts.append({"text": prompt})

        req_model = kwargs.get("model") or self.model
        payload: Dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        response_schema = kwargs.get("response_schema")
        if response_mime_type:
            payload["generationConfig"]["responseMimeType"] = response_mime_type
        if response_schema:
            payload["generationConfig"]["responseSchema"] = response_schema
        if thinking_budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        total_keys = len(self.api_keys)
        attempts = max_retries or max(3, total_keys * 2)

        candidate_models = [req_model]
        if req_model == "gemini-3.8-flash":
            candidate_models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.7-flash":
            candidate_models = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.6-flash":
            candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.5-flash":
            candidate_models = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]

        for active_model in candidate_models:
            active_payload = json.loads(json.dumps(payload))
            if active_model in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash") and thinking_budget is None:
                active_payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 64}
            data = json.dumps(active_payload).encode("utf-8")
            for attempt in range(attempts):
                current_key = self.api_keys[self.current_key_idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_model}:generateContent?key={current_key}"
                req = urllib.request.Request(url, headers={"Content-Type": "application/json", "User-Agent": "StudyVault-AICore/1.0"}, data=data)
                try:
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                    usage = res.get("usageMetadata", {})
                    try:
                        from ..usage_tracker import record_usage
                        record_usage("gemini-vision", active_model, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), usage.get("totalTokenCount", 0))
                    except Exception:
                        pass
                    candidates = res.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        text = "".join(part.get("text", "") for part in candidates[0]["content"].get("parts", []) if isinstance(part, dict))
                        if text.strip():
                            return text.strip()
                    raise RuntimeError(f"Gemini image request ({active_model}) returned no usable text.")
                except urllib.error.HTTPError as exc:
                    if exc.code in (429, 500, 502, 503, 504):
                        if total_keys > 1 and exc.code == 429:
                            old_idx = self.current_key_idx
                            self.current_key_idx = (self.current_key_idx + 1) % total_keys
                            print(f"  [Gemini-Vision 429 Limit] Key #{old_idx+1} reached quota ({active_model}). Failover to Key #{self.current_key_idx+1}/{total_keys}...", file=sys.stderr, flush=True)
                            time.sleep(0.5)
                            continue
                        elif attempt < attempts - 1:
                            wait = 1.5 * (1.5 ** attempt)
                            reason = "429 Rate Limit" if exc.code == 429 else f"{exc.code} Server Error"
                            print(f"  [Gemini-Vision {reason}] Retrying in {wait:.1f}s ({active_model}, attempt {attempt+1}/{attempts})...", file=sys.stderr, flush=True)
                            time.sleep(wait)
                            continue
                        else:
                            # Inner retry loop exhausted for this model; fall through to next candidate model
                            break
                    detail = exc.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Gemini API Error {exc.code}: {detail}") from exc
                except Exception:
                    if attempt == attempts - 1:
                        break
                    time.sleep(1.5)
            if len(candidate_models) > 1 and active_model != candidate_models[-1]:
                next_model = candidate_models[candidate_models.index(active_model) + 1]
                print(f"  [Gemini Vision Model Fallback] '{active_model}' unavailable. Falling back to '{next_model}'...", file=sys.stderr, flush=True)

        raise RuntimeError(f"All Gemini keys and models exhausted without a usable image response for models {candidate_models}.")

    def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """Yield incremental text deltas from Gemini streamGenerateContent (SSE)."""
        if not self.is_available():
            raise ValueError("Gemini API Key is not configured. Save it to Keychain or set GEMINI_API_KEY.")

        self.last_stream_usage = []
        req_model = kwargs.get("model") or self.model
        thinking_budget = kwargs.get("thinking_budget")

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if thinking_budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}

        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        total_keys = len(self.api_keys)
        max_retries = kwargs.get("max_retries", max(3, total_keys * 2))

        candidate_models = [req_model]
        if req_model == "gemini-3.8-flash":
            candidate_models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.7-flash":
            candidate_models = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.6-flash":
            candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif req_model == "gemini-3.5-flash":
            candidate_models = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]

        yielded_any = False
        for active_model in candidate_models:
            active_payload = json.loads(json.dumps(payload))
            if active_model in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash") and thinking_budget is None:
                active_payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 64}
            data = json.dumps(active_payload).encode("utf-8")
            for attempt in range(max_retries):
                current_key = self.api_keys[self.current_key_idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_model}:streamGenerateContent?alt=sse&key={current_key}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "StudyVault-AICore/1.0",
                    },
                    data=data,
                )
                usage_attempt = StreamUsageAttempt("gemini", active_model)
                try:
                    finish_reason = None
                    with urllib.request.urlopen(req, timeout=40) as resp:
                        for raw_line in resp:
                            line = raw_line.decode("utf-8", errors="ignore").strip()
                            if not line or not line.startswith("data:"):
                                continue
                            chunk = line[5:].strip()
                            try:
                                parsed = json.loads(chunk)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(parsed, dict):
                                usage_attempt.observe(parsed)
                            if not isinstance(parsed, dict) or parsed.get("error") is not None:
                                raise RuntimeError("Gemini stream did not complete normally.")
                            feedback = parsed.get("promptFeedback") or {}
                            if not isinstance(feedback, dict) or feedback.get("blockReason"):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            candidates = parsed.get("candidates") or []
                            if not candidates:
                                continue
                            if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            candidate = candidates[0]
                            index = candidate.get("index", 0)
                            if type(index) is not int or index != 0:
                                raise RuntimeError("Gemini stream did not complete normally.")
                            reason = candidate.get("finishReason")
                            if reason not in (None, "", "STOP"):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            content = candidate.get("content") or {}
                            if not isinstance(content, dict):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            parts = content.get("parts") or []
                            if not isinstance(parts, list):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            for part in parts:
                                if isinstance(part, dict) and not part.get("thought", False):
                                    text = part.get("text")
                                    if text is not None and not isinstance(text, str):
                                        raise RuntimeError("Gemini stream did not complete normally.")
                            if finish_reason is not None and any(
                                isinstance(part, dict) and not part.get("thought", False) and part.get("text")
                                for part in parts
                            ):
                                raise RuntimeError("Gemini stream did not complete normally.")
                            if reason == "STOP":
                                finish_reason = reason
                            for part in parts:
                                if isinstance(part, dict) and not part.get("thought", False):
                                    text = part.get("text", "")
                                    if text:
                                        yielded_any = True
                                        yield text
                    if yielded_any and finish_reason == "STOP":
                        return
                    raise RuntimeError("Gemini stream did not complete normally.")
                except urllib.error.HTTPError as e:
                    if yielded_any:
                        raise
                    if e.code in (429, 500, 502, 503, 504):
                        if total_keys > 1 and e.code == 429:
                            old_idx = self.current_key_idx
                            self.current_key_idx = (self.current_key_idx + 1) % total_keys
                            print(f"  [Gemini-Stream 429 Limit] Key #{old_idx+1} reached quota ({active_model}). Failover to Key #{self.current_key_idx+1}/{total_keys}...", file=sys.stderr, flush=True)
                            time.sleep(0.5)
                            continue
                        elif attempt < max_retries - 1:
                            wait = 1.5 * (1.5 ** attempt)
                            reason = "429 Rate Limit" if e.code == 429 else f"{e.code} Server Error"
                            print(f"  [Gemini-Stream {reason}] Retrying in {wait:.1f}s ({active_model}, attempt {attempt+1}/{max_retries})...", file=sys.stderr, flush=True)
                            time.sleep(wait)
                            continue
                        else:
                            break
                    else:
                        err_msg = e.read().decode("utf-8", errors="ignore")
                        raise RuntimeError(f"Gemini API Error {e.code}: {err_msg}")
                except Exception as e:
                    if yielded_any:
                        raise
                    if attempt < max_retries - 1:
                        time.sleep(1.5)
                    else:
                        break
                finally:
                    self.last_stream_usage.append(usage_attempt.finalize())
            if len(candidate_models) > 1 and active_model != candidate_models[-1]:
                next_model = candidate_models[candidate_models.index(active_model) + 1]
                print(f"  [Gemini Stream Model Fallback] '{active_model}' unavailable. Falling back to '{next_model}'...", file=sys.stderr, flush=True)

        raise RuntimeError(f"Gemini API stream retries exhausted without a usable response for models {candidate_models}.")


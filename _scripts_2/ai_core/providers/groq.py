"""Groq Cloud LLM Provider."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..keychain import get_api_key
from ..stream_usage import StreamUsageAttempt
from .base import BaseLLMProvider

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


class GroqProvider(BaseLLMProvider):
    """Groq Cloud API Provider with automatic Keychain resolution."""

    def __init__(self, api_key: Optional[str] = None, api_keys: Optional[List[str]] = None, model: str = DEFAULT_GROQ_MODEL):
        if api_keys:
            keys = [k.strip() for k in api_keys if k and len(k.strip()) > 5]
        elif api_key:
            keys = [api_key.strip()]
        else:
            from ..keychain import get_api_keys
            keys = get_api_keys("groq")

        self.api_keys: List[str] = keys
        self.current_key_idx: int = 0
        primary = self.api_keys[0] if self.api_keys else ""
        super().__init__(api_key=primary, model=model)

    @property
    def provider_name(self) -> str:
        return "groq"

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
            raise ValueError("Groq API Key is not configured. Save it to Keychain or set GROQ_API_KEY.")

        messages: List[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        req_model = kwargs.get("model") or self.model
        if req_model.startswith("gemini-"):
            req_model = self.model

        payload: Dict[str, Any] = {
            "model": req_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        response_schema = kwargs.get("response_schema")
        response_mime_type = kwargs.get("response_mime_type")
        if response_schema or response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}
            if response_schema and messages:
                schema_hint = f"\n\nReturn strict JSON following this schema:\n{json.dumps(response_schema, ensure_ascii=False)}"
                messages[-1]["content"] += schema_hint

        data = json.dumps(payload).encode("utf-8")
        total_keys = len(self.api_keys)
        max_retries = kwargs.get("max_retries", max(4, total_keys * 2))
        backoff_base = kwargs.get("backoff_base", 1.5)

        for attempt in range(max_retries):
            current_key = self.api_keys[self.current_key_idx]
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {current_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "StudyVault-AICore/1.0",
                },
                data=data,
            )

            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    usage = res.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                    try:
                        from ..usage_tracker import record_usage
                        record_usage("groq", req_model, prompt_tokens, completion_tokens, total_tokens)
                    except Exception:
                        pass

                    choices = res.get("choices", [])
                    if choices and "message" in choices[0]:
                        content = choices[0]["message"].get("content", "")
                        if isinstance(content, str) and content.strip():
                            return content
                    raise RuntimeError("Groq API returned no usable text.")
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if total_keys > 1:
                        old_idx = self.current_key_idx
                        self.current_key_idx = (self.current_key_idx + 1) % total_keys
                        print(f"  [Groq 429 Limit] Key #{old_idx+1} reached quota. Failover to Key #{self.current_key_idx+1}/{total_keys}...", file=sys.stderr, flush=True)
                        time.sleep(0.5)
                        continue
                    else:
                        wait = backoff_base * (2 ** attempt)
                        print(f"  [Groq 429 Rate Limit] Retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})...", file=sys.stderr, flush=True)
                        time.sleep(wait)
                else:
                    err_msg = e.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Groq API Error {e.code}: {err_msg}")
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                else:
                    raise e
        raise RuntimeError("Groq API retries exhausted without a usable response.")

    def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """Yield incremental text deltas from Groq chat completions (SSE)."""
        if not self.is_available():
            raise ValueError("Groq API Key is not configured. Save it to Keychain or set GROQ_API_KEY.")

        self.last_stream_usage = []
        messages: List[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        req_model = kwargs.get("model") or self.model
        if req_model.startswith("gemini-"):
            req_model = self.model

        payload: Dict[str, Any] = {
            "model": req_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        data = json.dumps(payload).encode("utf-8")
        total_keys = len(self.api_keys)
        max_retries = kwargs.get("max_retries", max(4, total_keys * 2))
        backoff_base = kwargs.get("backoff_base", 1.5)
        yielded_any = False

        for attempt in range(max_retries):
            current_key = self.api_keys[self.current_key_idx]
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {current_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "StudyVault-AICore/1.0",
                },
                data=data,
            )
            usage_attempt = StreamUsageAttempt("groq", req_model)
            try:
                finish_reason = None
                done_received = False
                with urllib.request.urlopen(req, timeout=120) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            done_received = True
                            break
                        try:
                            parsed = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(parsed, dict):
                            usage_attempt.observe(parsed)
                        if not isinstance(parsed, dict) or parsed.get("error") is not None:
                            raise RuntimeError("Groq stream did not complete normally.")
                        metadata = parsed.get("x_groq") or {}
                        if not isinstance(metadata, dict) or metadata.get("error") is not None:
                            raise RuntimeError("Groq stream did not complete normally.")
                        choices = parsed.get("choices") or []
                        if not choices:
                            continue
                        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                            raise RuntimeError("Groq stream did not complete normally.")
                        choice = choices[0]
                        index = choice.get("index", 0)
                        if type(index) is not int or index != 0:
                            raise RuntimeError("Groq stream did not complete normally.")
                        reason = choice.get("finish_reason")
                        if reason not in (None, "stop"):
                            raise RuntimeError("Groq stream did not complete normally.")
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            raise RuntimeError("Groq stream did not complete normally.")
                        piece = delta.get("content")
                        if piece is not None and not isinstance(piece, str):
                            raise RuntimeError("Groq stream did not complete normally.")
                        if finish_reason is not None and piece:
                            raise RuntimeError("Groq stream did not complete normally.")
                        if reason == "stop":
                            finish_reason = reason
                        if isinstance(piece, str) and piece:
                            yielded_any = True
                            yield piece
                if yielded_any and finish_reason == "stop" and done_received:
                    return
                raise RuntimeError("Groq stream did not complete normally.")
            except urllib.error.HTTPError as e:
                if yielded_any:
                    raise
                if e.code == 429:
                    if total_keys > 1:
                        old_idx = self.current_key_idx
                        self.current_key_idx = (self.current_key_idx + 1) % total_keys
                        print(
                            f"  [Groq 429 Limit] Key #{old_idx+1} reached quota. "
                            f"Failover to Key #{self.current_key_idx+1}/{total_keys}...",
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(0.5)
                        continue
                    wait = backoff_base * (2**attempt)
                    print(
                        f"  [Groq 429 Rate Limit] Retrying in {wait:.1f}s "
                        f"(attempt {attempt+1}/{max_retries})...",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(wait)
                else:
                    err_msg = e.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Groq API Error {e.code}: {err_msg}")
            except Exception as e:
                if yielded_any:
                    raise
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                else:
                    raise e
            finally:
                self.last_stream_usage.append(usage_attempt.finalize())
        raise RuntimeError("Groq API stream retries exhausted.")

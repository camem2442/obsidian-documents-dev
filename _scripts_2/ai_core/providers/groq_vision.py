"""Groq Qwen Vision provider for OCR and document-image understanding."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .base import BaseLLMProvider


DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.6-27b"
MAX_IMAGES = 5
MAX_RAW_IMAGE_BYTES = 14 * 1024 * 1024


def _parse_groq_retry_delay(exc: urllib.error.HTTPError, error_body: str) -> Optional[float]:
    """Extract retry wait time in seconds from Groq 429 response headers or body."""
    if exc.headers:
        reset_hdr = exc.headers.get("x-ratelimit-reset-tokens") or exc.headers.get("retry-after")
        if reset_hdr:
            val = reset_hdr.strip().lower()
            if val.endswith("ms"):
                try:
                    return float(val[:-2]) / 1000.0
                except ValueError:
                    pass
            elif val.endswith("s"):
                try:
                    return float(val[:-1])
                except ValueError:
                    pass
            elif val.replace(".", "", 1).isdigit():
                return float(val)

    if error_body:
        m = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)s", error_body)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        m_ms = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)ms", error_body)
        if m_ms:
            try:
                return float(m_ms.group(1)) / 1000.0
            except ValueError:
                pass
        m_m = re.search(r"try again in ([0-9]+)m([0-9]+(?:\.[0-9]+)?)s", error_body)
        if m_m:
            try:
                return float(m_m.group(1)) * 60 + float(m_m.group(2))
            except ValueError:
                pass

    return None


class GroqVisionProvider(BaseLLMProvider):
    """Vision provider using the same macOS Keychain Groq key pool."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_keys: Optional[List[str]] = None,
        model: str = DEFAULT_GROQ_VISION_MODEL,
    ):
        if api_keys:
            keys = [key.strip() for key in api_keys if key and len(key.strip()) > 5]
        elif api_key:
            keys = [api_key.strip()]
        else:
            from ..keychain import get_api_keys

            keys = get_api_keys("groq")
        self.api_keys = keys
        self.current_key_idx = 0
        super().__init__(api_key=keys[0] if keys else "", model=model)

    @property
    def provider_name(self) -> str:
        return "groq-vision"

    def is_available(self) -> bool:
        return bool(self.api_keys)

    def generate(self, prompt: str, **kwargs) -> str:
        image_paths = kwargs.pop("image_paths", None)
        if not image_paths:
            raise ValueError("Groq Vision requires at least one image path.")
        return self.generate_images(image_paths, prompt=prompt, **kwargs)

    def generate_images(
        self,
        image_paths: Sequence[Path | str],
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        kwargs.pop("model", None)
        if not self.is_available():
            raise ValueError("Groq API key is not configured for Qwen Vision OCR.")
        paths = [Path(path).expanduser().resolve() for path in image_paths]
        if not 1 <= len(paths) <= MAX_IMAGES:
            raise ValueError(f"Groq Qwen Vision accepts 1-{MAX_IMAGES} images per request.")
        total_bytes = sum(path.stat().st_size for path in paths if path.is_file())
        if any(not path.is_file() for path in paths):
            raise FileNotFoundError("One or more OCR image files do not exist.")
        if total_bytes > MAX_RAW_IMAGE_BYTES:
            raise ValueError("Encoded image request may exceed Groq's 20MB request limit.")

        user_content = [{"type": "text", "text": prompt}]
        for path in paths:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": user_content})
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_completion_tokens": max_tokens,
                "reasoning_effort": "none",
            }
        ).encode("utf-8")

        total_keys = len(self.api_keys)
        attempts = max_retries or max(5, total_keys * 3)
        last_error_detail = ""

        for attempt in range(attempts):
            key = self.api_keys[self.current_key_idx]
            request = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    result = json.loads(response.read().decode("utf-8"))
                usage = result.get("usage", {})
                try:
                    from ..usage_tracker import record_usage

                    record_usage(
                        "groq-vision",
                        self.model,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        usage.get("total_tokens", 0),
                    )
                except Exception:
                    pass
                choices = result.get("choices", [])
                if choices and choices[0].get("message"):
                    content = choices[0]["message"].get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                raise RuntimeError("Groq Qwen Vision returned no usable OCR text.")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                last_error_detail = detail
                if exc.code == 429:
                    if total_keys > 1:
                        self.current_key_idx = (self.current_key_idx + 1) % total_keys
                        time.sleep(0.5)
                        continue
                    delay = _parse_groq_retry_delay(exc, detail)
                    wait = (delay + 1.0) if delay is not None else min(2.0 * (2**attempt), 30.0)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Groq Vision API Error {exc.code}: {detail}") from exc
            except Exception as e:
                last_error_detail = str(e)
                if attempt == attempts - 1:
                    raise
                time.sleep(1.5)

        msg = f"All Groq Vision keys exhausted without a usable OCR response. (Key pool: {total_keys}, attempts: {attempts})"
        if last_error_detail:
            msg += f" Last error: {last_error_detail}"
        raise RuntimeError(msg)

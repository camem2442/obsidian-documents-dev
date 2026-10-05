"""Unified AI Client with Auto-Fallback, Task Helpers, and Public Contract v1.

Seamlessly dispatches tasks between Gemini and Groq based on availability, quotas, and semantic intent.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .prompts import (
    ACADEMIC_SUMMARY_PROMPT,
    ACADEMIC_TRANSLATE_PROMPT,
    PDF_FORMULA_POLISHER_PROMPT,
    QUALITY_AUDITOR_PROMPT,
)
from .providers.base import BaseLLMProvider
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.groq_vision import GroqVisionProvider


# ==============================================================================
# Public Exceptions
# ==============================================================================

class AIError(RuntimeError):
    """Base exception for all AI Core failures."""
    pass


class AIUnavailableError(AIError):
    """Raised when no AI providers or keys are configured."""
    pass


class AIRateLimitError(AIError):
    """Raised when provider rate limits / quotas are completely exhausted."""
    pass


class AITimeoutError(AIError):
    """Raised when request times out across providers."""
    pass


class AIResponseError(AIError):
    """Raised when provider returns an empty or invalid response."""
    pass


def _map_exception(exc: Exception) -> AIError:
    """Classify arbitrary underlying exceptions into standard AIError hierarchy."""
    if isinstance(exc, AIError):
        return exc
    msg = str(exc).lower()
    if "rate limit" in msg or "429" in msg or "quota" in msg or "resourceexhausted" in msg:
        return AIRateLimitError(f"AI rate limit / quota exhausted: {exc}")
    if "timeout" in msg or "timed out" in msg or isinstance(exc, TimeoutError):
        return AITimeoutError(f"AI request timed out: {exc}")
    if "empty response" in msg or "no usable text" in msg or "unusable" in msg:
        return AIResponseError(f"AI response invalid: {exc}")
    if "not configured" in msg or "no ai providers" in msg:
        return AIUnavailableError(f"AI providers unavailable: {exc}")
    return AIError(f"AI operation failed: {exc}")


def _safe_mapped_exception(exc: Exception) -> AIError:
    """Retain standard failure categories without publishing provider details."""
    mapped = _map_exception(exc)
    for kind, message in (
        (AIUnavailableError, "AI providers unavailable."),
        (AIRateLimitError, "AI rate limit / quota exhausted."),
        (AITimeoutError, "AI request timed out."),
        (AIResponseError, "AI response invalid."),
    ):
        if isinstance(mapped, kind):
            return kind(message)
    return AIError("AI operation failed.")


def _report_provider_failure(provider_name: str, exc: Exception, *, vision: bool = False) -> None:
    """Emit only known provider identity and a standard category to stderr."""
    identity = (provider_name if type(provider_name) is str
                and provider_name in ("gemini", "groq", "groq-vision") else "unknown")
    category = type(_safe_mapped_exception(exc)).__name__
    port = "AIClient Vision" if vision else "AIClient"
    print(f"  [{port}] Provider '{identity}' failed ({category}). Attempting next provider...",
          file=sys.stderr, flush=True)


# ==============================================================================
# Public Metadata Structure
# ==============================================================================

@dataclass
class GenerationMeta:
    """Structured telemetry and trace metadata for an AI completion."""
    task: Optional[str] = None
    profile: Optional[str] = None
    provider: Optional[str] = None
    requested_model: Optional[str] = None
    actual_model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    fallback_used: bool = False
    fallback_chain: List[str] = field(default_factory=list)
    latency_ms: float = 0.0

    @property
    def model(self) -> Optional[str]:
        """Convenience property returning the effective model."""
        return self.actual_model or self.requested_model

    def to_dict(self) -> Dict[str, Any]:

        return {
            "task": self.task,
            "profile": self.profile,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "model": self.actual_model or self.requested_model,
            "ai_tokens_used": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "fallback_used": self.fallback_used,
            "fallback_chain": list(self.fallback_chain),
            "latency_ms": self.latency_ms,
        }

    def __getitem__(self, item: str) -> Any:
        return self.to_dict()[item]

    def get(self, item: str, default: Any = None) -> Any:
        return self.to_dict().get(item, default)


# ==============================================================================
# Task-to-Model Mapping
# ==============================================================================

TASK_MODEL_MAPPING: Dict[str, str] = {
    # Geometric/spatial calculation and strict structural code generation
    "svg_graph": "gemini-3.8-flash",

    # Deep reasoning, logical argument ladders, philosophical/economic concept trees
    "outline_planning": "gemini-3.7-flash",
    "deep_reasoning": "gemini-3.7-flash",

    # Comprehensive academic synthesis and lecture summary
    "academic_synthesis": "gemini-3.8-flash",

    # High-quality structured markdown from complex slides/PDFs (tables, formulas, columns)
    "slide_markdown": "gemini-3.5-flash",

    # High-throughput text tasks: zero thinking overhead, generous quotas (1,500 RPD)
    "bulk_text": "gemini-3.5-flash-lite",
    "transcription_polish": "gemini-3.5-flash-lite",
    "formula_polish": "gemini-3.5-flash-lite",
    "ocr_cleanup": "gemini-3.5-flash-lite",
    "study_assistant_tutor": "gemini-3.5-flash-lite",
}


# ==============================================================================
# Unified Client
# ==============================================================================

class AIClient:
    """Unified client for executing LLM tasks with automatic provider fallback and task-based routing."""

    def __init__(
        self,
        primary_provider: str = "auto",
        gemini_model: Optional[str] = None,
        groq_model: Optional[str] = None,
        profile: str = "hybrid",
    ):
        try:
            self._gemini = GeminiProvider(model=gemini_model) if gemini_model else GeminiProvider()
            self._groq = GroqProvider(model=groq_model) if groq_model else GroqProvider()
            self._groq_vision = GroqVisionProvider()
            self.primary_provider = primary_provider.lower()
            self.profile = profile.lower()  # 'hybrid' (default), 'precision', 'economy'
            self.last_generation_meta: Dict[str, Any] = {}
        except Exception as exc:
            # Setup is outside generation catches; retain details only as cause.
            raise AIError("AI client setup failed.") from exc

    # --------------------------------------------------------------------------
    # Internal routing helpers (Public aliases provided for gentle migration)
    # --------------------------------------------------------------------------

    def _resolve_model(self, task_type: Optional[str] = None, profile: Optional[str] = None) -> str:
        """Resolve the optimal model for a given task type and operational profile."""
        active_profile = (profile or self.profile).lower()
        if active_profile == "precision":
            return "gemini-3.8-flash"
        elif active_profile in ("reasoning", "thinking"):
            return "gemini-3.7-flash"
        elif active_profile in ("standard", "balanced"):
            return "gemini-3.5-flash"
        elif active_profile == "economy":
            return "gemini-3.5-flash-lite"
        # 'hybrid': task-tailored routing
        if task_type:
            return TASK_MODEL_MAPPING.get(task_type.lower(), "gemini-3.5-flash-lite")
        return "gemini-3.5-flash-lite"

    def get_model_for_task(self, task_type: str, profile: Optional[str] = None) -> str:
        """Legacy alias for _resolve_model."""
        return self._resolve_model(task_type, profile=profile)

    def _get_provider_order(self) -> List[BaseLLMProvider]:
        """Determine execution order of text providers based on preference and key availability."""
        if self.primary_provider == "gemini":
            return [self._gemini, self._groq]
        elif self.primary_provider == "groq":
            return [self._groq, self._gemini]
        else:
            # 'auto': Prioritize Gemini for generous rate limits (1M TPM), fallback to Groq
            order = []
            if self._gemini.is_available():
                order.append(self._gemini)
            if self._groq.is_available():
                order.append(self._groq)
            return order

    def get_provider_order(self) -> List[BaseLLMProvider]:
        """Legacy alias for _get_provider_order."""
        return self._get_provider_order()

    def _get_vision_provider_order(self) -> List[Any]:
        """Determine execution order of vision/OCR providers based on preference and key availability."""
        if self.primary_provider == "gemini":
            return [self._gemini, self._groq_vision]
        elif self.primary_provider == "groq":
            return [self._groq_vision, self._gemini]
        else:
            # 'auto': Prioritize Gemini for generous rate limits (1M TPM), fallback to Groq Vision
            order = []
            if self._gemini.is_available():
                order.append(self._gemini)
            if self._groq_vision.is_available():
                order.append(self._groq_vision)
            return order

    def get_vision_provider_order(self) -> List[Any]:
        """Legacy alias for _get_vision_provider_order."""
        return self._get_vision_provider_order()

    # --------------------------------------------------------------------------
    # Public Contract v1: generate()
    # --------------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        task: Optional[str] = None,
        profile: Optional[str] = None,
        model_override: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate completion with transparent automatic fallback and task-based model routing."""
        providers = self._get_provider_order()
        if not providers:
            raise AIUnavailableError("No AI providers configured. Please save a Gemini or Groq key to Keychain.")

        target_model = model_override or kwargs.pop("model", None) or self._resolve_model(task, profile=profile)
        kwargs["model"] = target_model

        from .usage_tracker import get_last_api_usage

        last_error = None
        provider_attempts = 0
        fallback_chain: List[str] = []
        t0 = time.time()

        for prov in providers:
            if not prov.is_available():
                continue
            provider_attempts += 1
            fallback_chain.append(prov.provider_name)
            try:
                result = prov.generate(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if not isinstance(result, str) or not result.strip():
                    raise AIResponseError("Provider returned an empty response.")

                latency_ms = (time.time() - t0) * 1000.0
                usage = get_last_api_usage()
                meta = GenerationMeta(
                    task=task,
                    profile=profile or self.profile,
                    provider=usage.get("provider") or prov.provider_name,
                    requested_model=target_model,
                    actual_model=usage.get("model") or target_model,
                    input_tokens=int(usage.get("prompt_tokens") or usage.get("ai_tokens_used") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("ai_tokens_used") or 0),
                    fallback_used=provider_attempts > 1,
                    fallback_chain=fallback_chain,
                    latency_ms=latency_ms,
                )
                self.last_generation_meta = meta.to_dict()
                return result
            except Exception as e:
                _report_provider_failure(prov.provider_name, e)
                last_error = e

        cause = last_error or RuntimeError("All configured providers failed.")
        raise _safe_mapped_exception(cause) from cause

    # --------------------------------------------------------------------------
    # Public Contract v1: generate_images()
    # --------------------------------------------------------------------------


    def generate_images(
        self,
        image_paths: Sequence[Path | str],
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        task: Optional[str] = None,
        profile: Optional[str] = None,
        model_override: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate multimodal/OCR completion with transparent fallback (Gemini <-> Groq Vision)."""
        providers = self._get_vision_provider_order()
        if not providers:
            raise AIUnavailableError("No Vision AI providers configured. Please save a Gemini or Groq key to Keychain.")

        target_model = model_override or kwargs.pop("model", None) or self._resolve_model(task, profile=profile)
        kwargs["model"] = target_model

        from .usage_tracker import get_last_api_usage

        last_error = None
        provider_attempts = 0
        fallback_chain: List[str] = []
        t0 = time.time()

        for prov in providers:
            if not prov.is_available():
                continue
            provider_attempts += 1
            fallback_chain.append(prov.provider_name)
            try:
                result = prov.generate_images(
                    image_paths=image_paths,
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if not isinstance(result, str) or not result.strip():
                    raise AIResponseError("Vision provider returned an empty response.")

                latency_ms = (time.time() - t0) * 1000.0
                usage = get_last_api_usage()
                meta = GenerationMeta(
                    task=task,
                    profile=profile or self.profile,
                    provider=usage.get("provider") or prov.provider_name,
                    requested_model=target_model,
                    actual_model=usage.get("model") or target_model,
                    input_tokens=int(usage.get("prompt_tokens") or usage.get("ai_tokens_used") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("ai_tokens_used") or 0),
                    fallback_used=provider_attempts > 1,
                    fallback_chain=fallback_chain,
                    latency_ms=latency_ms,
                )
                self.last_generation_meta = meta.to_dict()
                return result
            except Exception as e:
                _report_provider_failure(prov.provider_name, e, vision=True)
                last_error = e

        cause = last_error or RuntimeError("All configured vision providers failed.")
        raise _safe_mapped_exception(cause) from cause

    # --------------------------------------------------------------------------
    # Public Contract v1: generate_stream()
    # --------------------------------------------------------------------------

    def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        task: Optional[str] = None,
        profile: Optional[str] = None,
        model_override: Optional[str] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Fail over before output; a started stream has one provider."""
        providers = self._get_provider_order()
        if not providers:
            raise AIUnavailableError("No AI providers configured. Please save a Gemini or Groq key to Keychain.")

        target_model = model_override or kwargs.pop("model", None) or self._resolve_model(task, profile=profile)
        kwargs["model"] = target_model

        last_error = None
        for prov in providers:
            if not prov.is_available():
                continue
            yielded_anything = False
            try:
                stream_iter = prov.generate_stream(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                for chunk in stream_iter:
                    if chunk:
                        yielded_anything = True
                        yield chunk
                if yielded_anything:
                    return
                raise AIResponseError("Stream finished without yielding any content.")
            except Exception as e:
                if yielded_anything:
                    raise AIResponseError("AI stream interrupted after output.") from e
                last_error = e

        cause = last_error or RuntimeError("All configured streaming providers failed.")
        mapped = _map_exception(cause)
        # Preserve the public hierarchy without constructing arbitrary subclasses.
        error_type = next(
            (kind for kind in (AIUnavailableError, AIRateLimitError, AITimeoutError, AIResponseError)
             if isinstance(mapped, kind)),
            AIError,
        )
        raise error_type("AI streaming operation failed.") from cause

    # --------------------------------------------------------------------------
    # Higher-level Domain Helpers (Standardized on Public Port)
    # --------------------------------------------------------------------------

    def polish_markdown_math(self, raw_section: str) -> str:
        """Clean OCR/PDF math anomalies and standardize LaTeX delimiters."""
        res = self.generate(
            prompt=f'Raw Section:\n"""\n{raw_section}\n"""',
            system_instruction=PDF_FORMULA_POLISHER_PROMPT,
            temperature=0.1,
            task="formula_polish",
        )
        res = re.sub(r"^```(?:markdown)?\s*", "", res)
        res = re.sub(r"\s*```$", "", res)
        res = re.sub(r"\\\[(.*?)\\\]", r"$$\1$$", res, flags=re.DOTALL)
        res = re.sub(r"\\\((.*?)\\\)", r"$\1$", res, flags=re.DOTALL)
        return res.strip() + "\n\n"

    def summarize_academic(self, text: str) -> str:
        """Generate structured academic note summary with wikilinks."""
        return self.generate(
            prompt=f'Text to summarize:\n"""\n{text}\n"""',
            system_instruction=ACADEMIC_SUMMARY_PROMPT,
            temperature=0.2,
            task="academic_synthesis",
        )

    def translate_academic(self, text: str) -> str:
        """Translate academic prose to high-fidelity Korean."""
        return self.generate(
            prompt=f'Text to translate:\n"""\n{text}\n"""',
            system_instruction=ACADEMIC_TRANSLATE_PROMPT,
            temperature=0.1,
            task="bulk_text",
        )

    def audit_note(self, markdown_content: str, filename: str = "Note") -> Dict[str, Any]:
        """Perform 4-domain quality audit on a markdown note."""
        cutoff = markdown_content[:6000].rfind("\n\n")
        excerpt = markdown_content[:cutoff] if cutoff > 1000 else markdown_content[:5000]

        prompt = f"""{QUALITY_AUDITOR_PROMPT}

Target Document: {filename}
Excerpt:
\"\"\"{excerpt}\"\"\""""

        raw = self.generate(prompt=prompt, temperature=0.1, task="deep_reasoning")
        json_m = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_m:
            try:
                return json.loads(json_m.group(0))
            except Exception:
                pass
        return {"overall_status": "UNKNOWN", "raw_response": raw}

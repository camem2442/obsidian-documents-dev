"""AI Core - Unified AI Infrastructure for macOS Keychain & Multi-Provider LLMs."""

from .classifier import AIClassifier, Category, ClassificationResult
from .client import (
    AIClient,
    AIError,
    AIResponseError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
    GenerationMeta,
)
from .keychain import delete_api_key, get_api_key, list_configured_providers, set_api_key
from .prompts import (
    ACADEMIC_SUMMARY_PROMPT,
    ACADEMIC_TRANSLATE_PROMPT,
    PDF_FORMULA_POLISHER_PROMPT,
    QUALITY_AUDITOR_PROMPT,
)
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.groq_vision import GroqVisionProvider
from .usage_tracker import (
    SessionTokenStats,
    SessionTokenTracker,
    get_today_summary,
    record_usage,
)

__all__ = [
    "AIClient",
    "GenerationMeta",
    "AIError",
    "AIUnavailableError",
    "AIRateLimitError",
    "AITimeoutError",
    "AIResponseError",
    "AIClassifier",
    "Category",
    "ClassificationResult",
    "GeminiProvider",
    "GroqProvider",
    "GroqVisionProvider",
    "SessionTokenTracker",
    "SessionTokenStats",
    "record_usage",
    "get_today_summary",
    "get_api_key",
    "set_api_key",
    "delete_api_key",
    "list_configured_providers",
    "PDF_FORMULA_POLISHER_PROMPT",
    "ACADEMIC_SUMMARY_PROMPT",
    "ACADEMIC_TRANSLATE_PROMPT",
    "QUALITY_AUDITOR_PROMPT",
]



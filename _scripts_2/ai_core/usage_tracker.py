"""Token and Request Usage Tracker for AI Core."""

import copy
import fcntl
import hashlib
import inspect
import sys
import types
import logging
import stat
import tempfile
import threading
from contextlib import contextmanager
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

USAGE_FILE = Path(__file__).resolve().parent / "usage.json"
_LOG = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


class UsageStorageError(RuntimeError):
    """The ledger could not be read or persisted; never treat it as empty."""


def _validate_usage(data):
    def counts(row, keys):
        return isinstance(row, dict) and all(
            type(row.get(key)) is int and row[key] >= 0 for key in keys
        )

    if not isinstance(data, dict):
        raise UsageStorageError("usage ledger must be an object")
    for day, providers in data.items():
        try:
            if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ValueError
        except (ValueError, TypeError):
            raise UsageStorageError("invalid usage date") from None
        if not isinstance(providers, dict):
            raise UsageStorageError("invalid provider rows")
        for provider, row in providers.items():
            if not provider or not counts(row, ("requests", "prompt_tokens", "completion_tokens", "total_tokens")):
                raise UsageStorageError("invalid provider counters")
            models = row.get("models")
            if not isinstance(models, dict) or any(
                not model or not counts(value, ("requests", "tokens"))
                for model, value in models.items()
            ):
                raise UsageStorageError("invalid model counters")


@contextmanager
def _usage_write_lock(path):
    # Stable sidecar inode: never unlink it while writers may be using it.
    # All writers must run this version; old processes do not honor this lock.
    with _WRITE_LOCK, path.with_name(path.name + ".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_save(path, data):
    # Serialize before touching the destination, then replace on the same FS.
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".usage-", suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            os.fchmod(out.fileno(), mode)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



@dataclass
class SessionTokenStats:
    """Statistics for tokens consumed and saved during a specific session."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    by_provider: Dict[str, Dict[str, int]] = field(default_factory=dict)
    skipped_graph_count: int = 0
    estimated_saved_tokens: int = 0

    def to_frontmatter_dict(self) -> Dict[str, Any]:
        """Format token stats into a dictionary suitable for Obsidian YAML frontmatter."""
        result: Dict[str, Any] = {
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
        if self.estimated_saved_tokens > 0:
            result["estimated_saved_tokens"] = self.estimated_saved_tokens
            result["deduplicated_slides"] = self.skipped_graph_count
        return result

    def summary_text(self) -> str:
        """Human-readable one-line summary for logging and UI status bars."""
        parts = [f"총 토큰: {self.total_tokens:,} (입력 {self.prompt_tokens:,} / 생성 {self.completion_tokens:,})"]
        if self.by_provider:
            prov_strs = []
            for prov, data in self.by_provider.items():
                tok = data.get("total_tokens", 0)
                if tok > 0:
                    prov_strs.append(f"{prov}: {tok:,}")
            if prov_strs:
                parts.append(f"[{', '.join(prov_strs)}]")
        if self.estimated_saved_tokens > 0:
            parts.append(f"| 빌드업 압축 절감: ~{self.estimated_saved_tokens:,} 토큰 ({self.skipped_graph_count}개 슬라이드)")
        return " ".join(parts)


class SessionTokenTracker:
    """Tracks token consumption during a single processing session across all AI Core operations."""

    AVERAGE_GRAPH_TOKENS = 2600  # Average prompt + completion tokens per vision SVG generation

    def __init__(self):
        self.baseline: Dict[str, Any] = {}
        self.skipped_graph_count: int = 0
        self.start()

    def start(self) -> None:
        """Capture baseline usage from AI Core."""
        try:
            self.baseline = copy.deepcopy(get_today_summary())
        except Exception:
            self.baseline = {}
        self.skipped_graph_count = 0

    def record_skipped_graph(self, count: int = 1) -> None:
        """Record skipped intermediate build-up graph slides for savings calculation."""
        self.skipped_graph_count += count

    def get_stats(self) -> SessionTokenStats:
        """Calculate token delta since session started."""
        try:
            current = get_today_summary()
        except Exception:
            current = {}

        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        by_provider: Dict[str, Dict[str, int]] = {}

        for provider, curr_data in current.items():
            base_data = self.baseline.get(provider, {})
            p_prompt = max(0, curr_data.get("prompt_tokens", 0) - base_data.get("prompt_tokens", 0))
            p_comp = max(0, curr_data.get("completion_tokens", 0) - base_data.get("completion_tokens", 0))
            p_total = max(0, curr_data.get("total_tokens", 0) - base_data.get("total_tokens", 0))

            if p_total > 0 or p_prompt > 0 or p_comp > 0:
                by_provider[provider] = {
                    "prompt_tokens": p_prompt,
                    "completion_tokens": p_comp,
                    "total_tokens": p_total,
                }
                total_prompt += p_prompt
                total_completion += p_comp
                total_tokens += p_total

        saved_tokens = self.skipped_graph_count * self.AVERAGE_GRAPH_TOKENS

        return SessionTokenStats(
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_tokens,
            by_provider=by_provider,
            skipped_graph_count=self.skipped_graph_count,
            estimated_saved_tokens=saved_tokens,
        )



_LAST_API_USAGE: Dict[str, Any] = {}


def get_last_api_usage() -> Dict[str, Any]:
    """Most recent token usage from record_usage (process-local)."""
    return dict(_LAST_API_USAGE)


def record_usage(provider: str, model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> bool:
    """Return persistence success; failure is logged without replaying the API call."""
    global _LAST_API_USAGE
    _LAST_API_USAGE = {
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "ai_tokens_used": total_tokens,
    }
    try:
        if (not isinstance(provider, str) or not provider or
                not isinstance(model, str) or not model or
                any(type(n) is not int or n < 0 for n in
                    (prompt_tokens, completion_tokens, total_tokens))):
            raise UsageStorageError("invalid usage increment")
        path = Path(USAGE_FILE)
        with _usage_write_lock(path):
            data = load_usage()
            today = datetime.now().strftime("%Y-%m-%d")
            pdata = data.setdefault(today, {}).setdefault(provider, {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "models": {},
            })
            pdata["requests"] += 1
            pdata["prompt_tokens"] += prompt_tokens
            pdata["completion_tokens"] += completion_tokens
            pdata["total_tokens"] += total_tokens
            mdata = pdata["models"].setdefault(model, {"requests": 0, "tokens": 0})
            mdata["requests"] += 1
            mdata["tokens"] += total_tokens
            _atomic_save(path, data)
        return True
    except (OSError, UsageStorageError, ValueError, TypeError) as exc:
        # A persistence failure must not replay an already completed paid call.
        # After replace/fsync failure persistence can be uncertain: never retry here.
        _LOG.error("Usage persistence failed (%s); no automatic retry. "
                   "Existing ledger is not reset; inspect before recovery.", type(exc).__name__)
        return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UsageStorageError("duplicate usage JSON key")
        result[key] = value
    return result


def load_usage() -> Dict[str, Any]:
    """Missing is empty; unreadable, malformed or invalid ledgers raise."""
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f, object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise UsageStorageError("usage ledger cannot be read") from exc
    _validate_usage(data)
    return data


def get_today_summary() -> Dict[str, Any]:
    """Get aggregated summary of today's usage."""
    today = datetime.now().strftime("%Y-%m-%d")
    data = load_usage()
    return data.get(today, {})


def fetch_live_limits() -> Dict[str, Any]:
    """Query live rate limits directly from provider endpoints."""
    from .keychain import get_api_key
    import urllib.request
    
    live = {}
    # Groq live limit
    groq_key = get_api_key("groq")
    if groq_key:
        try:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "StudyVault-AICore/1.0",
                },
                data=json.dumps({
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }).encode("utf-8"),
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                hdrs = resp.headers
                live["groq"] = {
                    "limit_tokens": hdrs.get("x-ratelimit-limit-tokens", "N/A"),
                    "remaining_tokens": hdrs.get("x-ratelimit-remaining-tokens", "N/A"),
                    "reset_tokens": hdrs.get("x-ratelimit-reset-tokens", "N/A"),
                    "limit_requests": hdrs.get("x-ratelimit-limit-requests", "N/A"),
                    "remaining_requests": hdrs.get("x-ratelimit-remaining-requests", "N/A"),
                }
        except Exception as e:
            live["groq"] = {"error": str(e)}

    return live


# This identifies loaded storage code, not source bytes or the entire AI Core app.
# Capture once: disk edits after import must not change an existing process's ID.
def _code_identity_value(value):
    if isinstance(value, types.CodeType):
        return {
            "bytecode": value.co_code.hex(),
            "constants": [_code_identity_value(v) for v in value.co_consts],
            "names": value.co_names, "varnames": value.co_varnames,
            "freevars": value.co_freevars, "cellvars": value.co_cellvars,
            "argcount": value.co_argcount,
            "posonlyargcount": getattr(value, "co_posonlyargcount", 0),
            "kwonlyargcount": value.co_kwonlyargcount,
            "flags": value.co_flags, "stacksize": value.co_stacksize,
            "exceptiontable": getattr(value, "co_exceptiontable", b"").hex(),
        }
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [_code_identity_value(v) for v in value]}
    if isinstance(value, frozenset):
        values = [_code_identity_value(v) for v in value]
        return {"frozenset": sorted(values, key=lambda v: json.dumps(v, sort_keys=True))}
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) in (float, complex):
        return {type(value).__name__: repr(value)}
    if value is Ellipsis:
        return {"ellipsis": True}
    raise TypeError("unsupported code constant")


def _capture_storage_identity():
    metadata = {
        "schema_version": 1,
        "scope": "ai_core.usage_storage",
        "algorithm": "python-code-v1",
        "python_cache_tag": sys.implementation.cache_tag,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "module_path_sha256": hashlib.sha256(str(Path(__file__).absolute()).encode()).hexdigest(),
        "ledger_path_at_import_sha256": hashlib.sha256(str(USAGE_FILE).encode()).hexdigest(),
    }
    try:
        functions = (record_usage, load_usage, _validate_usage, _unique_object,
                     _usage_write_lock, _atomic_save)
        payload = {"runtime_contract": {
            "thread_lock_type": type(_WRITE_LOCK).__module__ + "." + type(_WRITE_LOCK).__qualname__,
            "storage_error_bases": [base.__name__ for base in UsageStorageError.__bases__],
        }}
        for function in functions:
            loaded = inspect.unwrap(function)
            payload[loaded.__name__] = {
                "code": _code_identity_value(loaded.__code__),
                "defaults": _code_identity_value(loaded.__defaults__),
                "kwdefaults": {key: _code_identity_value(value) for key, value in
                               (loaded.__kwdefaults__ or {}).items()},
            }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode()
        return dict(metadata, status="available",
                    implementation_sha256=hashlib.sha256(encoded).hexdigest())
    except (TypeError, ValueError, AttributeError):
        # Never invent an identity from disk if loaded-code inspection fails.
        return dict(metadata, status="unavailable")


_STORAGE_IDENTITY_AT_IMPORT = _capture_storage_identity()


def get_usage_storage_identity() -> Dict[str, Any]:
    """Copy of import-time code identity; no disk, keys, ledger or provider I/O.

    Compare algorithm AND Python tag/version before comparing implementation IDs.
    Debug locations/paths are excluded from the code digest. Path hashes identify
    bindings at import. This is observability, not tamper attestation; monkeypatches,
    imported stdlib code and provider behavior are outside its declared scope.
    """
    return dict(_STORAGE_IDENTITY_AT_IMPORT)

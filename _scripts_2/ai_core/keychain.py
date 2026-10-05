"""macOS Keychain Manager for AI API Keys.

Securely stores and retrieves API credentials without hardcoding them in files.
Supports Gemini, Groq, Cerebras, OpenAI, Anthropic, etc.
Supports Key Pooling (multiple keys per provider for quota rotation and failover).
"""

import os
import subprocess
from typing import Any, Dict, List, Optional

# Standard service name prefixes for macOS Keychain
KEYCHAIN_SERVICE_PREFIX = "local.study.ai"

DEFAULT_SERVICES: Dict[str, str] = {
    "gemini": f"{KEYCHAIN_SERVICE_PREFIX}.gemini",
    "groq": f"{KEYCHAIN_SERVICE_PREFIX}.groq",
    "cerebras": f"{KEYCHAIN_SERVICE_PREFIX}.cerebras",
    "openai": f"{KEYCHAIN_SERVICE_PREFIX}.openai",
    "anthropic": f"{KEYCHAIN_SERVICE_PREFIX}.anthropic",
}

# Legacy services for backwards compatibility with existing scripts
LEGACY_SERVICES: Dict[str, str] = {
    "gemini": "local.study.lecturetranscriber.gemini",
    "groq": "local.study.lecturetranscriber.groq",
}


def get_service_name(provider: str) -> str:
    """Return canonical service name for a given provider."""
    return DEFAULT_SERVICES.get(provider.lower(), f"{KEYCHAIN_SERVICE_PREFIX}.{provider.lower()}")


def mask_key(key: str) -> str:
    """Safely mask an API key for display (e.g. 'AQ.Ab8R...91ATw')."""
    if not key:
        return "None"
    if len(key) <= 10:
        return f"{key[:2]}...{key[-2:]}"
    return f"{key[:8]}...{key[-5:]}"


class KeychainReadError(RuntimeError):
    """A read-only observation failed; absence must not be inferred."""


def get_api_keys(provider: str, *, read_only: bool = False) -> List[str]:
    """Retrieve pooled keys; read_only skips migration and raises on read failure.

    Default callers retain legacy migration and best-effort lookup behavior.
    """
    provider = provider.lower()
    keys: List[str] = []
    read = (lambda service, account: _read_keychain(service, account, strict=True)) if read_only else _read_keychain

    # 1. Environment variables
    env_multi = os.environ.get(f"{provider.upper()}_API_KEYS")
    if env_multi:
        for k in env_multi.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)

    env_single = os.environ.get(f"{provider.upper()}_API_KEY")
    if env_single and env_single.strip() not in keys:
        keys.append(env_single.strip())

    # 2. Primary key: check canonical slot 1 / primary account
    canonical_svc = get_service_name(provider)
    primary = read(canonical_svc, f"{provider}_api_key") or read(canonical_svc, f"{provider}_api_key_1")
    if primary and primary not in keys:
        keys.append(primary)

    # 3. Legacy services (for backward compatibility)
    if provider in LEGACY_SERVICES:
        legacy_svc = LEGACY_SERVICES[provider]
        for account in [f"{provider}_api_key", f"{provider.upper()}_API_KEY"]:
            k = read(legacy_svc, account)
            if k and k not in keys:
                keys.append(k)
                # Auto-sync legacy to canonical primary so future lookups are clean
                if not primary and not read_only:
                    set_api_key(provider, k, slot=1)
                    primary = k

    # 4. Additional pooled slots (2 to 10)
    for slot in range(2, 11):
        k = read(canonical_svc, f"{provider}_api_key_{slot}")
        if k and k not in keys:
            keys.append(k)

    return keys



def get_api_key(provider: str, *, read_only: bool = False) -> Optional[str]:
    """Retrieve the primary API key for a provider."""
    keys = get_api_keys(provider, read_only=True) if read_only else get_api_keys(provider)
    return keys[0] if keys else None


def set_api_key(provider: str, api_key: str, slot: int = 1) -> bool:
    """Store or update an API key in macOS Keychain at a specific slot."""
    provider = provider.lower()
    service = get_service_name(provider)
    key_val = api_key.strip()
    if not key_val:
        return False

    accounts = [f"{provider}_api_key"] if slot == 1 else [f"{provider}_api_key_{slot}"]
    if slot == 1:
        accounts.append(f"{provider}_api_key_1")

    success = True
    for account in accounts:
        try:
            res = subprocess.run(
                ["/usr/bin/security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", key_val],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode != 0:
                success = False
        except Exception as e:
            print(f"[Keychain Error] Failed to set key for {provider} account {account}: {e}")
            success = False
    return success


def add_api_key(provider: str, api_key: str) -> int:
    """Add a new API key to the next available slot. Returns assigned slot number."""
    existing_keys = get_api_keys(provider)
    key_val = api_key.strip()

    if key_val in existing_keys:
        return existing_keys.index(key_val) + 1

    next_slot = len(existing_keys) + 1
    ok = set_api_key(provider, key_val, slot=next_slot)
    return next_slot if ok else -1


def delete_api_key(provider: str, slot: Optional[int] = None) -> bool:
    """Delete an API key or all keys for a provider from macOS Keychain."""
    provider = provider.lower()
    service = get_service_name(provider)

    if slot is not None:
        accounts = [f"{provider}_api_key"] if slot == 1 else [f"{provider}_api_key_{slot}"]
        if slot == 1:
            accounts.append(f"{provider}_api_key_1")
    else:
        accounts = [f"{provider}_api_key"] + [f"{provider}_api_key_{i}" for i in range(1, 11)]

    any_deleted = False
    for account in accounts:
        try:
            res = subprocess.run(
                ["/usr/bin/security", "delete-generic-password", "-s", service, "-a", account],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode == 0:
                any_deleted = True
        except Exception:
            pass

    return any_deleted


def list_configured_providers(*, read_only: bool = False) -> Dict[str, Dict[str, Any]]:
    """List key status; read_only never returns partial success on lookup failure."""
    status = {}
    for prov in DEFAULT_SERVICES.keys():
        keys = get_api_keys(prov, read_only=True) if read_only else get_api_keys(prov)
        status[prov] = {
            "configured": len(keys) > 0,
            "count": len(keys),
            "keys_masked": [mask_key(k) for k in keys]
        }
    return status


def _read_keychain(service: str, account: str, *, strict: bool = False) -> Optional[str]:
    """Internal helper to query macOS security CLI."""
    try:
        res = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        if strict and res.returncode != 44:
            raise KeychainReadError("Keychain observation unavailable")
    except Exception as exc:
        if strict:
            raise KeychainReadError("Keychain observation unavailable") from exc
    return None

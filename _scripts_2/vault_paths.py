"""Documents workspace vs Obsidian vault folder resolution for _scripts_2.

**Documents root** (workspace parent): iCloud ``.../Documents`` on macOS, or the git
checkout root on Linux. Holds ``_scripts_2``, zero or more Obsidian vaults (child
folders with ``.obsidian/``), and other material. This is **not** an Obsidian vault.

**Obsidian vault root**: a child of the documents root (e.g. ``공부``, ``KS``, ``영어``).
The set of vaults is open-ended — use :func:`obsidian_vault_root` with any folder name.

Namespaced roots and configuration are defined in ``docs/path-contract.md``.
``OBS_VAULT_ROOT`` takes precedence over these legacy Documents aliases.
Invalid selected roots fail closed; unconfigured compatibility defaults remain.

Legacy environment for the **documents root** (first match wins):

``DOCUMENTS_ROOT``, ``STUDY_TOOLS_DOCUMENTS_ROOT``, then aliases ``VAULT_ROOT``,
``OBSIDIAN_VAULT_ROOT``, ``OBSIDIAN_VAULT`` (same meaning: workspace parent).

Per-vault path overrides: ``STUDY_VAULT``, ``KS_VAULT`` (absolute paths), or
``STUDY_VAULT_NAME`` / ``KS_VAULT_NAME`` to pick a folder name under documents.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from . import path_contract
except ImportError:  # Existing standalone scripts import vault_paths from _scripts_2.
    import path_contract

MAC_ICLOUD_OBSIDIAN = (
    Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
)
PHONE_ICLOUD_CONTAINER_PREFIX = "NK37SPV8GQ~cn~winat~"
PHONE_RECORDINGS_SYMLINK_NAME = "phone-lecture-sync"
_PHONE_RECORDINGS_ENV_KEYS = ("PHONE_RECORDINGS_INPUT_DIR", "EASYVOICE_INPUT_DIR")

_DOCUMENTS_ENV_KEYS = path_contract.DOCUMENTS_ENV_KEYS

_VAULT_PATH_OVERRIDE_ENV: dict[str, tuple[str, ...]] = {
    "공부": ("STUDY_VAULT",),
    "KS": ("KS_VAULT", "EXAM_PROCESSOR_KS", "EBSI_VAULT_ROOT"),
}


def documents_root_from_env() -> Path | None:
    return path_contract.legacy_documents_root()


def vault_root_from_env() -> Path | None:
    """Alias for :func:`documents_root_from_env` (legacy name)."""
    return documents_root_from_env()


def study_tools_root(*start: Path) -> Path:
    """PROJECT-owned tools. Legacy seed arguments no longer select caller cwd."""
    return path_contract.project_root() / "_scripts_2"


def default_documents_root(*start: Path) -> Path:
    """Workspace / iCloud Documents parent (not a single Obsidian vault)."""
    selected = path_contract.configured_root("VAULT_ROOT", legacy_documents=documents_root_from_env)
    if selected is not None:
        return selected
    if sys.platform == "darwin":
        return MAC_ICLOUD_OBSIDIAN
    return study_tools_root(*start).parent


def default_obsidian_documents_root(*start: Path) -> Path:
    """Deprecated alias for :func:`default_documents_root`."""
    return default_documents_root(*start)


def obsidian_vault_root(vault_name: str, *start: Path) -> Path:
    """Resolve one Obsidian vault folder under the documents root."""
    for env_key in _VAULT_PATH_OVERRIDE_ENV.get(vault_name, ()):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            return Path(raw).expanduser()
    return default_documents_root(*start) / vault_name


def default_study_vault(*start: Path) -> Path:
    name = os.environ.get("STUDY_VAULT_NAME", "공부").strip() or "공부"
    return obsidian_vault_root(name, *start)


def default_ks_vault(*start: Path) -> Path:
    name = os.environ.get("KS_VAULT_NAME", "KS").strip() or "KS"
    return obsidian_vault_root(name, *start)


def default_study_subtree(*start: Path) -> Path:
    """Study vault root (same as :func:`default_study_vault`)."""
    return default_study_vault(*start)


def default_university_subtree(*start: Path) -> Path:
    return default_study_vault(*start) / "0 대학"


def iter_obsidian_vaults(*start: Path) -> list[Path]:
    """Obsidian vault folders under documents root (have ``.obsidian/``)."""
    root = default_documents_root(*start)
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and (p / ".obsidian").is_dir()
    )


def phone_recordings_input_from_env() -> Path | None:
    for key in _PHONE_RECORDINGS_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def discover_mac_phone_recordings_documents() -> Path | None:
    """``Mobile Documents/…/Documents`` where phone lecture audio syncs (macOS)."""
    mobile = Path.home() / "Library/Mobile Documents"
    linked = mobile / PHONE_RECORDINGS_SYMLINK_NAME / "Documents"
    if linked.is_dir():
        return linked
    if not mobile.is_dir():
        return None
    for child in sorted(mobile.iterdir()):
        if child.is_dir() and child.name.startswith(PHONE_ICLOUD_CONTAINER_PREFIX):
            docs = child / "Documents"
            if docs.is_dir():
                return docs
    return None


def default_phone_recordings_input_dir(*start: Path) -> Path:
    """Inbox for phone/iCloud lecture recordings (``.m4a``, etc.)."""
    override = phone_recordings_input_from_env()
    if override is not None:
        return override
    if sys.platform == "darwin":
        discovered = discover_mac_phone_recordings_documents()
        if discovered is not None:
            return discovered
        return (
            Path.home()
            / "Library/Mobile Documents"
            / PHONE_RECORDINGS_SYMLINK_NAME
            / "Documents"
        )
    tools = study_tools_root(*start)
    legacy = tools / "data" / "easyvoice_input"
    if legacy.is_dir():
        return legacy
    return tools / "data" / "phone_recordings_input"


def default_easyvoice_input_dir(*start: Path) -> Path:
    """Deprecated alias for :func:`default_phone_recordings_input_dir`."""
    return default_phone_recordings_input_dir(*start)


def canonical_job_path(path: Path | str) -> str:
    """Neutral ``phone-lecture-sync`` prefix in saved job metadata when possible."""
    text = str(Path(path).expanduser())
    mobile = Path.home() / "Library/Mobile Documents"
    if not mobile.is_dir():
        return text
    for child in mobile.iterdir():
        if child.is_dir() and child.name.startswith(PHONE_ICLOUD_CONTAINER_PREFIX):
            prefix = str(child)
            if text.startswith(prefix):
                return text.replace(
                    f"{prefix}/",
                    f"{mobile}/{PHONE_RECORDINGS_SYMLINK_NAME}/",
                    1,
                )
    return text


def resolve_job_path(stored: str, *, input_dir: Path | None = None) -> Path:
    """Resolve stored job path to an existing audio file."""
    path = Path(stored).expanduser()
    candidates = [path]
    text = str(path)
    if PHONE_RECORDINGS_SYMLINK_NAME in text:
        mobile = Path.home() / "Library/Mobile Documents"
        if mobile.is_dir():
            for child in mobile.iterdir():
                if child.is_dir() and child.name.startswith(PHONE_ICLOUD_CONTAINER_PREFIX):
                    candidates.append(
                        Path(text.replace(PHONE_RECORDINGS_SYMLINK_NAME, child.name, 1))
                    )
                    break
    inbox = input_dir or default_phone_recordings_input_dir()
    candidates.append(inbox / path.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return path


def app_support_dir(app_name: str) -> Path:
    override = os.environ.get(f"{app_name.upper().replace(' ', '_')}_DATA", "").strip()
    if override:
        return Path(override).expanduser()
    selected = path_contract.configured_root("STATE_ROOT")
    if selected is not None:
        return selected / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local/share"
    return base / app_name

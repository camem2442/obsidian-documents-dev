"""Filesystem roots; see docs/path-contract.md. Resolution never creates files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Callable, Mapping

ROOT_NAMES = ("PROJECT_ROOT", "VAULT_ROOT", "STATE_ROOT", "CACHE_ROOT")
ENV_NAMES = {name: "OBS_" + name for name in ROOT_NAMES}
DOCUMENTS_ENV_KEYS = (
    "DOCUMENTS_ROOT", "STUDY_TOOLS_DOCUMENTS_ROOT", "VAULT_ROOT",
    "OBSIDIAN_VAULT_ROOT", "OBSIDIAN_VAULT",
)
Config = Mapping[str, str] | Path | str | None


class PathConfigurationError(ValueError):
    """An explicit root/configuration is unusable; do not search another store."""


def _source_project() -> Path:
    # Source-relative discovery is appropriate for PROJECT, never for data.
    return Path(__file__).resolve().parent.parent


def _directory(raw: str | Path, owner: str, *, project: bool = False) -> Path:
    if not str(raw).strip():
        raise PathConfigurationError(f"{owner}: root must not be empty")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise PathConfigurationError(f"{owner}: root must be absolute: {path}")
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise PathConfigurationError(f"{owner}: root is not a directory: {path}")
        if project and not (path / "_scripts_2/program_catalog.json").is_file():
            raise PathConfigurationError(f"{owner}: missing _scripts_2/program_catalog.json: {path}")
    except (OSError, RuntimeError) as exc:
        raise PathConfigurationError(f"{owner}: root is unavailable: {path}") from exc
    return path


def _configuration(config: Config) -> Mapping[str, str]:
    if isinstance(config, Mapping):
        values = config
    else:
        if config is None:
            if "OBS_PATH_CONFIG" in os.environ:
                config = os.environ["OBS_PATH_CONFIG"]
                if not config.strip():
                    raise PathConfigurationError("OBS_PATH_CONFIG must not be empty")
            else:
                candidate = Path(__file__).resolve().parent / "path-roots.local.json"
                if not candidate.exists():
                    return {}
                config = candidate
        path = Path(config).expanduser()
        if not path.is_absolute():
            raise PathConfigurationError(f"path config must be absolute: {path}")
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PathConfigurationError(f"cannot read path config: {path}") from exc
    if not isinstance(values, Mapping):
        raise PathConfigurationError("path config must be a JSON object")
    for key, raw in values.items():
        if key not in ROOT_NAMES or not isinstance(raw, str):
            raise PathConfigurationError(f"invalid path config key/value: {key}")
    return values


def legacy_documents_root() -> Path | None:
    """Read the existing documents aliases; validation belongs to resolution."""
    for key in DOCUMENTS_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def configured_root(name: str, *, explicit: str | Path | None = None,
                    config: Config = None,
                    legacy_documents: Callable[[], Path | None] | None = None) -> Path | None:
    """CLI/API > namespaced env > legacy env > config; None means unset.

    Legacy VAULT_ROOT denotes the documents container, not a child .obsidian
    vault. STUDY_TOOLS_ROOT denotes _scripts_2, not PROJECT_ROOT.
    """
    if name not in ROOT_NAMES:
        raise PathConfigurationError(f"unknown root: {name}")
    env_key = ENV_NAMES[name]
    if explicit is not None:
        raw, owner = explicit, name + " argument"
    elif env_key in os.environ:
        raw, owner = os.environ[env_key], env_key
    elif name == "PROJECT_ROOT" and os.environ.get("STUDY_TOOLS_ROOT", "").strip():
        tools = _directory(os.environ["STUDY_TOOLS_ROOT"], "STUDY_TOOLS_ROOT")
        if tools.name != "_scripts_2":
            raise PathConfigurationError("STUDY_TOOLS_ROOT must name the _scripts_2 directory")
        raw, owner = tools.parent, "STUDY_TOOLS_ROOT"
    elif name == "VAULT_ROOT" and (legacy := (legacy_documents or legacy_documents_root)()) is not None:
        raw, owner = legacy, "legacy Documents root"
    else:
        values = _configuration(config)
        if name not in values:
            return None
        raw, owner = values[name], "path config " + name
    return _directory(raw, owner, project=name == "PROJECT_ROOT")


def resolve_root(name: str, *, explicit: str | Path | None = None,
                 config: Config = None) -> Path:
    selected = configured_root(name, explicit=explicit, config=config)
    if selected is not None:
        return selected
    if name == "PROJECT_ROOT":
        fallback = _source_project()
    elif name == "VAULT_ROOT":
        # Documented current-layout compatibility only; no cwd search.
        fallback = (Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
                    if sys.platform == "darwin" else project_root(config=config))
    elif name == "STATE_ROOT":
        fallback = (Path.home() / "Library/Application Support" if sys.platform == "darwin"
                    else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"))
    else:
        fallback = (Path.home() / "Library/Caches" if sys.platform == "darwin"
                    else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"))
    return _directory(fallback, name + " compatibility default", project=name == "PROJECT_ROOT")


def project_root(*, explicit: str | Path | None = None, config: Config = None) -> Path:
    return resolve_root("PROJECT_ROOT", explicit=explicit, config=config)


def vault_root(*, explicit: str | Path | None = None, config: Config = None) -> Path:
    return resolve_root("VAULT_ROOT", explicit=explicit, config=config)


def state_root(*, explicit: str | Path | None = None, config: Config = None) -> Path:
    return resolve_root("STATE_ROOT", explicit=explicit, config=config)


def cache_root(*, explicit: str | Path | None = None, config: Config = None) -> Path:
    return resolve_root("CACHE_ROOT", explicit=explicit, config=config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", choices=["project", "vault", "state", "cache", "all"])
    parser.add_argument("--config", type=Path)
    for name in ROOT_NAMES:
        parser.add_argument("--" + name.lower().replace("_", "-"))
    args = parser.parse_args(argv)
    names = ROOT_NAMES if args.root == "all" else (args.root.upper() + "_ROOT",)
    try:
        roots = {name: str(resolve_root(name, explicit=getattr(args, name.lower()), config=args.config))
                 for name in names}
    except PathConfigurationError as exc:
        parser.exit(2, f"path configuration error: {exc}\n")
    print(json.dumps(roots, ensure_ascii=False) if args.root == "all" else roots[names[0]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Personal Study Tools branding — replace default Python Dock/window icons."""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
_ICONS_DIR = Path(__file__).resolve().parent / "assets" / "icons"
_CATALOG_PATH = _SCRIPTS_ROOT / "program_catalog.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    with _CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def program_meta(program_id: str) -> dict[str, Any] | None:
    for program in _catalog().get("programs", []):
        if program.get("id") == program_id:
            return program
    return None


def icon_path(program_id: str) -> Path | None:
    path = _ICONS_DIR / f"{program_id}.png"
    return path if path.is_file() else None


def set_macos_dock_icon(path: Path) -> bool:
    """Best-effort Dock icon swap on macOS (avoids the Python rocket)."""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApplication, NSImage  # type: ignore
    except ImportError:
        return False
    image = NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is None:
        return False
    NSApplication.sharedApplication().setApplicationIconImage_(image)
    return True


def apply_qt_branding(app: Any, program_id: str) -> None:
    """Apply display name + window/Dock icon to a QApplication."""
    meta = program_meta(program_id) or {}
    name = str(meta.get("name") or program_id)
    app.setApplicationName(name)
    if hasattr(app, "setApplicationDisplayName"):
        app.setApplicationDisplayName(name)
    if hasattr(app, "setOrganizationName"):
        app.setOrganizationName("Study Tools")

    path = icon_path(program_id)
    if path is None:
        return

    try:
        from PyQt6.QtGui import QIcon
    except ImportError:
        set_macos_dock_icon(path)
        return

    icon = QIcon(str(path))
    app.setWindowIcon(icon)
    set_macos_dock_icon(path)


def apply_native_branding(program_id: str) -> Path | None:
    """Set Dock icon for non-Qt hosts (pywebview, etc.) and return icon path."""
    meta = program_meta(program_id) or {}
    name = str(meta.get("name") or program_id)
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle  # type: ignore

            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = bundle.infoDictionary()
                if info is not None:
                    info["CFBundleName"] = name
                    info["CFBundleDisplayName"] = name
        except Exception:
            pass

    path = icon_path(program_id)
    if path is not None:
        set_macos_dock_icon(path)
    return path

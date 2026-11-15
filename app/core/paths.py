"""Zentrale Pfadverwaltung.

Alle veränderlichen Daten (Einstellungen, Verlauf, Logs, Backups, Arbeitskopien) liegen
unter ``%LOCALAPPDATA%\\WIDS-Access-AI``. Für Tests kann das Verzeichnis über die
Umgebungsvariable ``WIDS_DATA_DIR`` umgeleitet werden.
"""
from __future__ import annotations

import os
from pathlib import Path

from app import APP_ID

_ENV_OVERRIDE = "WIDS_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    return _sub("logs")


def backups_dir() -> Path:
    return _sub("backups")


def workcopies_dir() -> Path:
    return _sub("workcopies")


def database_file() -> Path:
    return data_dir() / "wids.sqlite3"



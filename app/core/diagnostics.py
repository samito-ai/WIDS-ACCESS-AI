"""Diagnoseberichte NUR für schwerwiegende Fehler. Kleinere, automatisch behobene Probleme stehen im Log und im
Verlauf – sie erzeugen keinen Bericht und keine Meldung.

Ein Bericht enthält Kategorie, Komponente, Fehlermeldung, anonymisierte Technikdaten, versuchte Korrekturen, deren
Ergebnis und Log-Auszüge – ohne API-Schlüssel (``redact``), ohne Benutzernamen/-pfade und ohne Inhalte von
Datenbanken oder Aufgabentexten.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

from app import __version__
from app.core import paths
from app.core.errors import (
    AccessError,
    AccessNotInstalledError,
    BackupError,
    CostLimitError,
    DatabaseLockedError,
    DatabaseNotFoundError,
    DatabaseReadOnlyError,
    InvalidNameError,
    ObjectExistsError,
    ObjectNotFoundError,
    PolicyViolationError,
    ProviderAuthError,
    ProviderBillingError,
    ProviderNetworkError,
    ProviderNotConfiguredError,
    ResponseFormatError,
    SqlSyntaxError,
    TaskCancelledError,
    UnsupportedOperationError,
    WidsError,
)
from app.core.logging_setup import redact

#: Fehler, die der Benutzer selbst beheben kann bzw. die gewollte Sperren sind – kein schwerwiegender Fehler
_NOT_SEVERE = (TaskCancelledError, CostLimitError, ProviderNotConfiguredError, ProviderAuthError, ProviderBillingError,
               ProviderNetworkError, PolicyViolationError, AccessNotInstalledError, DatabaseNotFoundError,
               DatabaseLockedError, DatabaseReadOnlyError, ObjectExistsError, ObjectNotFoundError, InvalidNameError,
               SqlSyntaxError, UnsupportedOperationError)


def diagnose_dir() -> Path:
    folder = paths.data_dir() / "diagnose"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def classify(exc: BaseException) -> tuple[str, str] | None:
    """(Kategorie, Komponente) für einen schwerwiegenden Fehler – ``None`` für normale, erklärbare Fehler."""
    if isinstance(exc, _NOT_SEVERE):
        return None
    if isinstance(exc, BackupError):
        return "Sicherung/Wiederherstellung fehlgeschlagen", "Sicherung"
    if isinstance(exc, ResponseFormatError):
        return "Schwerwiegender Fehler der KI-Verarbeitung", "KI-Anbindung"
    if isinstance(exc, AccessError):
        return "Nicht behebbarer Access-Fehler", "Access-Automatisierung"
    if isinstance(exc, WidsError):
        return None  # erklärbare Fehlermeldung mit Handlungshinweis
    return "Programmfehler (unerwartete Ausnahme)", type(exc).__module__.split(".")[1] if "." in type(exc).__module__ \
        else "Programm"


_PATH = re.compile(r"(?:[A-Za-z]:|\\\\[^\\\s]+)\\[^\s\"'<>|]*")
_QUOTED = re.compile(r"'[^'\n]*'|„[^“\n]*“|\"[^\"\n]*\"")


def anonymize(text: str) -> str:
    """API-Schlüssel, Benutzername, Pfade (auch Netzlaufwerke) und Werte in Anführungszeichen (SQL-Literale,
    Aufgaben- und Datenbankinhalte) entfernen – der Bericht enthält nur Technik, keine Inhalte."""
    text = redact(text)
    text = _PATH.sub(lambda m: "<Pfad>" + (Path(m.group(0)).suffix or ""), text)
    text = _QUOTED.sub("‹…›", text)
    home = str(Path.home())
    for value in {home, home.replace("\\", "/"), os.environ.get("USERPROFILE", "")}:
        if value:
            text = text.replace(value, "%USERPROFILE%")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user and len(user) >= 3:
        text = re.sub(rf"(?i)\b{re.escape(user)}\b", "%USERNAME%", text)
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<E-Mail>", text)


def _log_excerpt(lines: int = 60) -> str:
    log_file = paths.logs_dir() / "wids.log.jsonl"
    if not log_file.exists():
        return "(kein Log vorhanden)"
    out = []
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
        try:  # JSON-Zeile: nur Zeit, Stufe, Modul und (bereinigte) Meldung – ohne Zusatzfelder
            entry = json.loads(line)
            line = " ".join(str(entry.get(k, "")) for k in ("ts", "level", "logger", "msg"))
        except (ValueError, AttributeError):
            pass
        out.append(anonymize(line)[:400])
    return "\n".join(out)


def write_report(category: str, component: str, message: str, *, attempts: list[str] | None = None,
                 outcome: str = "", task_id: int | None = None) -> Path:
    """Schreibt einen schwerwiegenden Diagnosebericht und liefert seinen Pfad."""
    now = datetime.now()
    tried = "\n".join(f"- {anonymize(a)}" for a in attempts or []) or "- keine"
    body = f"""# WIDS-Diagnose – schwerwiegender Fehler

| | |
|---|---|
| Zeitpunkt | {now:%d.%m.%Y %H:%M:%S} |
| Kategorie | {category} |
| Komponente | {component} |
| Aufgabe | {task_id if task_id is not None else '–'} |
| WIDS-Version | {__version__} |
| Windows | {platform.platform()} |
| Programmform | {'EXE' if getattr(sys, 'frozen', False) else 'Quellcode'} |

## Fehlermeldung
{anonymize(message)}

## Bereits versuchte Korrekturen
{tried}

## Ergebnis der Selbstkorrektur
{anonymize(outcome) or '–'}

## Log-Auszug (anonymisiert, ohne Datenbankinhalte)
```
{_log_excerpt()}
```
"""
    path = diagnose_dir() / f"diagnose_{now:%Y%m%d_%H%M%S}.md"
    path.write_text(body, encoding="utf-8")
    return path


def latest_report() -> Path | None:
    reports = sorted(diagnose_dir().glob("diagnose_*.md"))
    return reports[-1] if reports else None

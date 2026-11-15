"""24-Stunden-Freigabe je te.comp-Lernprogramm bzw. -Workshop – unabhängig vom Dateinamensschema.

Freigabe-Einheiten („Programme“):

- **Lernprogramme** aus te.comp (``access\\packages\\Kap.NN.xml``: Name, z. B. „DA - Tabellen“, und Kapitelfilter,
  z. B. ``02*``),
- **Workshops** aus te.comp (je Ordner ``access\\workshops\\<Name>`` mit Vorlagendatenbank).

Eine Datei gehört AUTOMATISCH zu einem Programm, wenn sie direkt im te.comp-Arbeitsordner liegt
(``%USERPROFILE%\\te.comp\\te.comp-training``) und eindeutig zugeordnet werden kann:

- nummerierte Übung (``<Kapitel 4-stellig><Übung>_<Nummer>.accdb``) mit vorhandener Aufgabenseite und genau einem
  passenden Lernprogramm-Filter, oder
- Workshop-Datei (``<Vorlage>_<Nummer>.accdb``), deren Vorlage/Ordner genau EINEM Workshop entspricht.

Alles andere – beliebige Dateinamen, mehrdeutige Workshops, eigene Übungsdatenbanken – ordnet der Benutzer EINMAL im
Dashboard zu; diese Zuordnung gilt für genau diesen Dateipfad (nie für andere Dateien gleichen Namens) und wird
dauerhaft gespeichert. Die Freigabe ersetzt nur die Routinebestätigung „Übungsdatenbank“ – Prüfungsschutz, Sicherung und
die Bestätigung löschender Schritte gelten unverändert. Was WIDS technisch kann, hängt nie vom Namen ab.
"""
from __future__ import annotations

import fnmatch
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.settings import ProgramApproval
from app.vision import tecomp

APPROVAL_HOURS = 24
#: Freigaben bleiben zur Nachvollziehbarkeit so lange gespeichert (auch abgelaufene)
KEEP_HISTORY_DAYS = 30
_EXERCISE_NAME = re.compile(r"(\d{4})(\d{1,2})_\d+\.(?:accdb|mdb)", re.IGNORECASE)
WORKSHOP_PREFIX = "WS:"


@dataclass(frozen=True)
class Program:
    id: str
    caption: str
    filters: tuple[str, ...]

    def covers_chapter(self, chapter: str) -> bool:
        return any(fnmatch.fnmatchcase(chapter, pattern) for pattern in self.filters)

    @property
    def is_workshop(self) -> bool:
        return self.id.startswith(WORKSHOP_PREFIX)


def default_workdir() -> Path:
    return Path.home() / "te.comp" / "te.comp-training"


def file_key(db_path: str) -> str:
    """Vergleichsschlüssel einer Datei für gespeicherte Zuordnungen (voller, normalisierter Pfad)."""
    return os.path.normcase(os.path.abspath(db_path)) if db_path else ""


def load_programs(install: Path | None = None) -> list[Program]:
    """Alle Access-Lernprogramme und -Workshops aus te.comp (leer, wenn te.comp nicht installiert ist)."""
    install = install or tecomp.install_dir()
    if install is None:
        return []
    programs: list[Program] = []
    for path in sorted((install / "access" / "packages").glob("Kap.*.xml")):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        caption = (root.findtext("global/caption") or "").strip()
        filters = tuple(c.text.strip() for c in root.iterfind("filter/c") if c.text and c.text.strip())
        if caption and filters:
            programs.append(Program(id=path.stem, caption=caption, filters=filters))
    workshops = install / "access" / "workshops"
    if workshops.is_dir():
        for folder in sorted(d for d in workshops.iterdir() if d.is_dir() and any(d.glob("*.accdb"))):
            programs.append(Program(id=f"{WORKSHOP_PREFIX}{folder.name}", caption=f"Workshop {folder.name}", filters=()))
    return programs


def _in_workdir(path: Path, workdir: Path) -> bool:
    return os.path.normcase(os.path.abspath(path.parent)) == os.path.normcase(os.path.abspath(workdir))


def program_for(db_path: str, *, install: Path | None = None, workdir: Path | None = None,
                programs: list[Program] | None = None, assignments: dict[str, str] | None = None) -> Program | None:
    """Programm, zu dem diese Datei gehört – gespeicherte Zuordnung (genau dieser Pfad) oder eindeutige automatische
    Zuordnung (siehe Modulbeschreibung). Sonst ``None``."""
    if not db_path:
        return None
    install = install or tecomp.install_dir()
    known = programs if programs is not None else load_programs(install)
    assigned = (assignments or {}).get(file_key(db_path))
    if assigned:
        return next((p for p in known if p.id == assigned), None)
    path = Path(db_path)
    if install is None or not _in_workdir(path, workdir or default_workdir()):
        return None
    match = _EXERCISE_NAME.fullmatch(path.name)
    if match is not None:
        if tecomp.task_page(path.name, install) is None:
            return None
        candidates = [p for p in known if not p.is_workshop and p.covers_chapter(match.group(1))]
        return candidates[0] if len(candidates) == 1 else None
    folder = tecomp.workshop_for(db_path, install)
    if folder is None:
        return None
    return next((p for p in known if p.id == f"{WORKSHOP_PREFIX}{folder.name}"), None)


def assign(assignments: dict[str, str], db_path: str, program: Program) -> None:
    """Datei einmalig (vom Benutzer bestätigt) einem Programm zuordnen – gilt nur für genau diesen Pfad."""
    assignments[file_key(db_path)] = program.id


def unassign(assignments: dict[str, str], db_path: str) -> bool:
    return assignments.pop(file_key(db_path), None) is not None


def active_approval(approvals: list[ProgramApproval], program_id: str,
                    now: datetime | None = None) -> ProgramApproval | None:
    """Die gültige (nicht abgelaufene, nicht widerrufene) Freigabe dieses Programms."""
    valid = [a for a in approvals if a.program_id == program_id and a.active(now)]
    return max(valid, key=lambda a: a.expires_at) if valid else None


def approve(approvals: list[ProgramApproval], program: Program, now: datetime | None = None) -> ProgramApproval:
    """Neue 24-Stunden-Freigabe anhängen (ältere Einträge über ``KEEP_HISTORY_DAYS`` werden entfernt)."""
    now = now or datetime.now(tz=UTC)
    entry = ProgramApproval(program_id=program.id, caption=program.caption, approved_at=now.isoformat(),
                            expires_at=(now + timedelta(hours=APPROVAL_HOURS)).isoformat())
    cutoff = now - timedelta(days=KEEP_HISTORY_DAYS)
    approvals[:] = [a for a in approvals if datetime.fromisoformat(a.expires_at) > cutoff] + [entry]
    return entry


def revoke(approvals: list[ProgramApproval], program_id: str, now: datetime | None = None) -> bool:
    """Gültige Freigaben dieses Programms widerrufen (bleiben als widerrufen sichtbar)."""
    hit = False
    for approval in approvals:
        if approval.program_id == program_id and approval.active(now):
            approval.revoked = True
            hit = True
    return hit

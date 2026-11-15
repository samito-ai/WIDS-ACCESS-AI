"""Objekte zwischen Access-Datenbanken übernehmen (te.comp 1001: Sicherungsdatenbank, Import, Verknüpfen).

Andere Datenbanken werden nur im Übungsordner der geöffneten Datenbank (bzw. den te.comp-Arbeitsordnern) gesucht und
angelegt. Eine vorhandene Zieldatenbank wird vor einem Export in den WIDS-Sicherungsordner kopiert; nichts wird
überschrieben.
"""
from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from app.access import constants as c
from app.access.comerrors import com_guard
from app.access.session import AccessSession, dao_engine
from app.access.specs import StrictModel
from app.core.errors import AccessError, ObjectExistsError, ObjectNotFoundError
from app.core.logging_setup import get_logger

log = get_logger(__name__)

TransferDirection = Literal["export", "import", "link"]
ObjectKind = Literal["table", "query", "form", "report"]
_TRANSFER = {"import": 0, "export": 1, "link": 2}  # AcDataTransferType
_KIND = {"table": c.AC_TABLE, "query": c.AC_QUERY, "form": c.AC_FORM, "report": c.AC_REPORT}
_CONTAINER = {"form": "Forms", "report": "Reports"}
_DB_SUFFIXES = (".accdb", ".mdb")


class TransferItem(StrictModel):
    type: ObjectKind = "table"
    name: str = Field(min_length=1)
    #: Name in der Zieldatenbank (Standard: gleicher Name)
    target_name: str | None = None

    @property
    def target(self) -> str:
        return self.target_name or self.name


def resolve_database(name: str, db_path: str, *, create: bool = False) -> Path:
    """Findet eine andere Access-Datenbank der Übung (``create``: Pfad für eine neue im Ordner der geöffneten)."""
    from app.access.importer import search_dirs

    wanted = Path(name)
    if wanted.suffix.lower() not in _DB_SUFFIXES:
        wanted = wanted.with_name(wanted.name + ".accdb")
    current = Path(db_path).resolve() if db_path else None
    allowed = [d.resolve() for d in search_dirs(db_path)]
    if wanted.is_absolute():
        if wanted.parent.resolve() not in allowed:
            raise AccessError(f"„{wanted}“ liegt nicht im Übungsordner – andere Orte sind nicht erlaubt.")
        candidates = [wanted]
    else:
        candidates = [d / wanted.name for d in allowed]
    for path in candidates:
        found = next((p for p in path.parent.glob("*") if p.name.lower() == path.name.lower()), None) \
            if path.parent.is_dir() else None
        if found is not None and found.is_file():
            if current is not None and found.resolve() == current:
                raise AccessError("Die andere Datenbank ist die geöffnete Datenbank selbst.")
            if create:
                raise ObjectExistsError("Datenbank", found.name)
            return found
    if create and current is not None:
        return (candidates[0] if wanted.is_absolute() else current.parent / wanted.name)
    raise ObjectNotFoundError("Datenbank", wanted.name)


def object_names(path: Path) -> dict[str, set[str]]:
    """Objekte einer anderen Datenbank (nur lesend per DAO, ohne Access): Art → Namen in Kleinbuchstaben."""
    db = dao_engine().OpenDatabase(str(path), False, True)
    try:
        names: dict[str, set[str]] = {
            "table": {str(t.Name).lower() for t in db.TableDefs if not str(t.Name).startswith(("MSys", "~"))},
            "query": {str(q.Name).lower() for q in db.QueryDefs if not str(q.Name).startswith("~")},
        }
        for kind, container in _CONTAINER.items():
            try:
                names[kind] = {str(d.Name).lower() for d in db.Containers(container).Documents}
            except Exception:
                names[kind] = set()
        return names
    finally:
        with contextlib.suppress(Exception):
            db.Close()


class TransferOps:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    def transfer(self, direction: TransferDirection, database: str, items: list[TransferItem], *,
                 structure_only: bool = False, create_new: bool = False) -> Path:
        """Überträgt die Objekte wie „Externe Daten > Access“ bzw. „Exportieren > Access“. Rückgabe: andere Datei."""
        if direction == "link" and any(i.type != "table" for i in items):
            raise AccessError("Verknüpfen ist nur für Tabellen möglich.")
        if create_new and direction != "export":
            raise AccessError("Eine neue Datenbank kann nur als Exportziel angelegt werden.")
        path = resolve_database(database, self._s.path, create=create_new)
        local = self._local_names()
        if direction == "export":
            missing = [i.name for i in items if i.name.lower() not in local[i.type]]
            if missing:
                raise ObjectNotFoundError("Objekt", ", ".join(missing))
            if create_new:
                with com_guard(f"Datenbank „{path.name}“ anlegen"):
                    version = c.DB_VERSION_40 if path.suffix.lower() == ".mdb" else c.DB_VERSION_120
                    dao_engine().CreateDatabase(str(path), c.DB_LANG_GENERAL, version).Close()
            else:
                taken = object_names(path)
                clash = [i.target for i in items if i.target.lower() in taken[i.type]]
                if clash:
                    raise ObjectExistsError("Objekt in der Zieldatenbank", ", ".join(clash))
                self._backup(path)
        else:
            available = object_names(path)
            missing = [i.name for i in items if i.name.lower() not in available[i.type]]
            if missing:
                raise ObjectNotFoundError(f"Objekt in „{path.name}“", ", ".join(missing))
            clash = [i.target for i in items if any(i.target.lower() in names for names in local.values())]
            if clash:
                raise ObjectExistsError("Objekt", ", ".join(clash))
        app = self._s.application()
        for item in items:
            with com_guard(f"„{item.name}“ übertragen"):
                app.DoCmd.TransferDatabase(_TRANSFER[direction], "Microsoft Access", str(path), _KIND[item.type],
                                           item.name, item.target, structure_only and item.type == "table")
        self._s.refresh_db()
        self._s.refresh_navigation()
        log.info("%d Objekt(e) übertragen (%s) mit %s", len(items), direction, path.name)
        return path

    def _local_names(self) -> dict[str, set[str]]:
        db = self._s.db
        db.TableDefs.Refresh()
        db.QueryDefs.Refresh()
        names = {"table": {str(t.Name).lower() for t in db.TableDefs if not str(t.Name).startswith(("MSys", "~"))},
                 "query": {str(q.Name).lower() for q in db.QueryDefs if not str(q.Name).startswith("~")}}
        for kind, container in _CONTAINER.items():
            try:
                names[kind] = {str(d.Name).lower() for d in db.Containers(container).Documents}
            except Exception:
                names[kind] = set()
        return names

    @staticmethod
    def _backup(path: Path) -> Path:
        from app.core.paths import backups_dir

        target = backups_dir() / "export-ziele" / f"{path.stem}_{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target


def transferred(path: Path, items: list[TransferItem]) -> dict[str, bool]:
    """Prüft nach einem Export, welche Objekte in der anderen Datenbank angekommen sind (Objektname → vorhanden)."""
    names = object_names(path)
    return {i.target: i.target.lower() in names[i.type] for i in items}


def link_source(tabledef: Any) -> str:
    """Pfad der Quelldatenbank einer verknüpften Tabelle (leer = nicht verknüpft)."""
    connect = str(getattr(tabledef, "Connect", "") or "")
    return connect.split("DATABASE=", 1)[1].split(";")[0] if "DATABASE=" in connect.upper() else ""

"""Fassade der Access-Automatisierung: bündelt alle Operationen einer Sitzung."""
from __future__ import annotations

import os

from app.access.forms_reports import FormReportOps
from app.access.inspector import DatabaseInspector
from app.access.models import DatabaseSchema
from app.access.queries import QueryOps
from app.access.relations import RelationOps
from app.access.session import AccessSession
from app.access.tables import TableOps
from app.access.transfer import TransferOps


class AccessEngine:
    """Alle Datenbankoperationen für genau eine geöffnete Datenbank.

    Die Engine ist an den Thread gebunden, in dem die Sitzung geöffnet wurde (COM-Apartment).
    """

    def __init__(self, session: AccessSession) -> None:
        self.session = session
        self.tables = TableOps(session)
        self.relations = RelationOps(session)
        self.queries = QueryOps(session)
        self.objects = FormReportOps(session)
        self.transfer = TransferOps(session)
        self.inspector = DatabaseInspector(session)

    # ------------------------------------------------------------------ Fabrikmethoden
    @classmethod
    def open(
        cls, path: str | os.PathLike[str], *, prefer_running: bool = True, read_only: bool = False
    ) -> AccessEngine:
        return cls(AccessSession.open(path, prefer_running=prefer_running, read_only=read_only))

    @classmethod
    def attach_active(cls) -> AccessEngine:
        return cls(AccessSession.attach_active())

    @classmethod
    def create(cls, path: str | os.PathLike[str], *, overwrite: bool = False) -> AccessEngine:
        return cls(AccessSession.create(path, overwrite=overwrite))

    # ------------------------------------------------------------------ Komfort
    @property
    def path(self) -> str:
        return self.session.path

    @property
    def mode(self) -> str:
        return self.session.mode

    def schema(self, *, detailed_objects: bool = False) -> DatabaseSchema:
        return self.inspector.inspect(detailed_objects=detailed_objects)

    def object_exists(self, name: str) -> str | None:
        """Objekttyp, falls unter diesem Namen bereits ein Objekt existiert."""
        if self.tables.table_exists(name):
            return "Tabelle"
        if self.queries.exists(name):
            return "Abfrage"
        if self.objects.form_exists(name):
            return "Formular"
        if self.objects.report_exists(name):
            return "Bericht"
        return None

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> AccessEngine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

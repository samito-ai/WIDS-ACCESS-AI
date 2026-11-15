"""Abfragen: gespeicherte QueryDefs erstellen, prüfen, ausführen."""
from __future__ import annotations

import contextlib
from typing import Any

from pydantic import BaseModel, Field

from app.access import constants as c
from app.access.comerrors import com_guard
from app.access.models import QueryInfo, QueryParameter
from app.access.naming import validate_name
from app.access.session import AccessSession
from app.access.sqlbuilder import READ_ONLY_KINDS, classify_sql, ensure_single_statement
from app.access.tables import collection_has, datasheet_summary, get_property, recordset_to_rows, set_property
from app.access.values import to_dao
from app.core.errors import (
    AccessError,
    ApprovalRequiredError,
    ObjectExistsError,
    ObjectNotFoundError,
    SqlSyntaxError,
)
from app.core.logging_setup import get_logger

log = get_logger(__name__)


class SqlCheck(BaseModel):
    """Ergebnis der Vorabprüfung einer SQL-Anweisung durch die Access-Engine."""

    ok: bool
    kind: str = "unknown"
    error: str = ""
    fields: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)


def _param_name(param: Any) -> str:
    """DAO liefert Parameternamen mit Sonderzeichen in eckigen Klammern – diese entfernen."""
    name = str(param.Name)
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1]
    return name


class QueryOps:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    @property
    def _db(self) -> Any:
        return self._s.db

    def exists(self, name: str) -> bool:
        self._db.QueryDefs.Refresh()
        return collection_has(self._db.QueryDefs, name)

    def _require(self, name: str) -> Any:
        if not self.exists(name):
            raise ObjectNotFoundError("Abfrage", name)
        return self._db.QueryDefs(name)

    # ------------------------------------------------------------------ Prüfen
    def check_sql(self, sql: str) -> SqlCheck:
        """Lässt Access die Anweisung kompilieren, ohne etwas zu speichern oder auszuführen.

        Nicht auflösbare Namen behandelt Access als Parameter – sie erscheinen in
        ``parameters`` und weisen meist auf falsch geschriebene Feldnamen hin.
        """
        try:
            text = ensure_single_statement(sql)
        except SqlSyntaxError as exc:
            return SqlCheck(ok=False, error=exc.user_message)
        kind = classify_sql(text)
        try:
            with com_guard("SQL prüfen"):
                temp = self._db.CreateQueryDef("", text)
                try:
                    parameters = [_param_name(p) for p in temp.Parameters]
                    fields: list[str] = []
                    if kind in READ_ONLY_KINDS:
                        with contextlib.suppress(Exception):
                            fields = [str(f.Name) for f in temp.Fields]
                finally:
                    with contextlib.suppress(Exception):
                        temp.Close()
        except AccessError as exc:
            return SqlCheck(ok=False, kind=kind, error=exc.user_message)
        return SqlCheck(ok=True, kind=kind, fields=fields, parameters=parameters)

    # ------------------------------------------------------------------ Erstellen / Ändern
    def create_query(
        self, name: str, sql: str, *, replace: bool = False, description: str | None = None
    ) -> QueryInfo:
        validate_name(name, "Abfrage")
        text = ensure_single_statement(sql)
        self._db.TableDefs.Refresh()
        if collection_has(self._db.TableDefs, name):
            raise ObjectExistsError("Tabelle", name)
        already = self.exists(name)
        if already and not replace:
            raise ObjectExistsError("Abfrage", name)
        with com_guard(f"Abfrage „{name}“ speichern"):
            if already:
                querydef = self._db.QueryDefs(name)
                querydef.SQL = text
            else:
                querydef = self._db.CreateQueryDef(name, text)
            if description:
                set_property(querydef, "Description", description)
            self._db.QueryDefs.Refresh()
        self._s.refresh_navigation()
        log.info("Abfrage gespeichert: %s", name)
        return self.describe(self._db.QueryDefs(name))

    def delete_query(self, name: str) -> None:
        self._require(name)
        with com_guard(f"Abfrage „{name}“ löschen"):
            self._db.QueryDefs.Delete(name)
            self._db.QueryDefs.Refresh()
        self._s.refresh_navigation()

    def rename_query(self, old: str, new: str) -> None:
        validate_name(new, "Abfrage")
        querydef = self._require(old)
        if old.lower() != new.lower():
            self._db.TableDefs.Refresh()
            if self.exists(new) or collection_has(self._db.TableDefs, new):
                raise ObjectExistsError("Objekt", new)
        with com_guard(f"Abfrage „{old}“ umbenennen"):
            querydef.Name = new
            self._db.QueryDefs.Refresh()
        self._s.refresh_navigation()

    def get_sql(self, name: str) -> str:
        return str(self._require(name).SQL).strip()

    # ------------------------------------------------------------------ Beschreiben
    def describe(self, querydef: Any) -> QueryInfo:
        type_code = int(querydef.Type)
        fields: list[str] = []
        if type_code in (c.DB_Q_SELECT, c.DB_Q_CROSSTAB, c.DB_Q_SET_OPERATION):
            with contextlib.suppress(Exception):
                fields = [str(f.Name) for f in querydef.Fields]
        parameters: list[QueryParameter] = []
        with contextlib.suppress(Exception):
            parameters = [QueryParameter(name=_param_name(p), type=int(p.Type)) for p in querydef.Parameters]
        return QueryInfo(
            name=str(querydef.Name),
            sql=str(querydef.SQL).strip(),
            type_code=type_code,
            type_name=c.QUERY_TYPE_NAMES.get(type_code, f"Abfrage (Typ {type_code})"),
            is_action=type_code in c.ACTION_QUERY_TYPES,
            fields=fields,
            parameters=parameters,
            description=str(get_property(querydef, "Description", "")),
            datasheet=datasheet_summary(querydef, with_formats=True),
        )

    def list_queries(self) -> list[QueryInfo]:
        self._db.QueryDefs.Refresh()
        result: list[QueryInfo] = []
        with com_guard("Abfragen lesen"):
            for querydef in self._db.QueryDefs:
                name = str(querydef.Name)
                if name.startswith("~"):  # interne Abfragen von Formularen/Berichten
                    continue
                result.append(self.describe(querydef))
        return sorted(result, key=lambda q: q.name.lower())

    # ------------------------------------------------------------------ Ausführen
    def run_select(
        self, name: str, *, parameters: dict[str, Any] | None = None, limit: int = 200
    ) -> tuple[list[str], list[list[Any]]]:
        """Führt eine gespeicherte Auswahlabfrage aus und liefert Spalten und Zeilen."""
        querydef = self._require(name)
        if int(querydef.Type) in c.ACTION_QUERY_TYPES:
            raise AccessError(f"„{name}“ ist eine Aktionsabfrage und liefert keine Datensätze.")
        return self._open(querydef, parameters, limit)

    def run_select_sql(
        self, sql: str, *, parameters: dict[str, Any] | None = None, limit: int = 200
    ) -> tuple[list[str], list[list[Any]]]:
        text = ensure_single_statement(sql)
        if classify_sql(text) not in READ_ONLY_KINDS:
            raise AccessError("Hier sind nur lesende Abfragen (SELECT) erlaubt.")
        with com_guard("Abfrage ausführen"):
            temp = self._db.CreateQueryDef("", text)
        try:
            return self._open(temp, parameters, limit)
        finally:
            with contextlib.suppress(Exception):
                temp.Close()

    def run_select_isolated(self, sql: str, *, limit: int = 200) -> tuple[list[str], list[list[Any]]]:
        """Lesende Abfrage über eine EIGENE, schreibgeschützte DAO-Verbindung ohne Access: auch präpariertes SQL kann
        so weder Daten ändern noch VBA-Funktionen der Datenbank aufrufen (für Datenfragen aus KI-Plänen)."""
        from app.access.session import dao_engine

        text = ensure_single_statement(sql)
        if classify_sql(text) not in READ_ONLY_KINDS:
            raise AccessError("Hier sind nur lesende Abfragen (SELECT) erlaubt.")
        with com_guard("Datenfrage auswerten"):
            db = dao_engine().OpenDatabase(self._s.path, False, True)
        try:
            with com_guard("Datenfrage auswerten"):
                temp = db.CreateQueryDef("", text)
            try:
                return self._open(temp, None, limit)
            finally:
                with contextlib.suppress(Exception):
                    temp.Close()
        finally:
            with contextlib.suppress(Exception):
                db.Close()

    def _open(
        self, querydef: Any, parameters: dict[str, Any] | None, limit: int
    ) -> tuple[list[str], list[list[Any]]]:
        supplied = {k.lower().strip("[]"): v for k, v in (parameters or {}).items()}
        with com_guard("Abfrage ausführen"):
            missing: list[str] = []
            for param in querydef.Parameters:
                key = _param_name(param).lower()
                if key in supplied:
                    param.Value = self._param_value(supplied[key])
                else:
                    missing.append(_param_name(param))
            if missing:
                raise SqlSyntaxError(
                    "Die Abfrage erwartet Werte für: "
                    + ", ".join(missing)
                    + " (Parameterabfrage oder falsch geschriebener Feldname)."
                )
            rs = querydef.OpenRecordset(c.DB_OPEN_SNAPSHOT)
            try:
                return recordset_to_rows(rs, limit)
            finally:
                rs.Close()

    @staticmethod
    def _param_value(value: Any) -> Any:
        from app.access.constants import AccessType

        if isinstance(value, str):
            with contextlib.suppress(Exception):
                import re

                if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", value.strip()):
                    return to_dao(value, AccessType.DATETIME)
        return value

    def count_rows(self, name: str, *, parameters: dict[str, Any] | None = None) -> int:
        _, rows = self.run_select(name, parameters=parameters, limit=1_000_000)
        return len(rows)

    def run_action(
        self,
        *,
        name: str | None = None,
        sql: str | None = None,
        parameters: dict[str, Any] | None = None,
        max_affected: int | None = None,
    ) -> int:
        """Führt eine Aktionsabfrage in einer Transaktion aus und liefert die Zahl betroffener Datensätze.

        Bei Überschreitung von ``max_affected`` wird vollständig zurückgerollt
        (``ApprovalRequiredError``); es bleibt keine Änderung zurück.
        """
        if (name is None) == (sql is None):
            raise AccessError("Entweder 'name' oder 'sql' angeben.")
        if name is not None:
            querydef = self._require(name)
            if int(querydef.Type) not in c.ACTION_QUERY_TYPES:
                raise AccessError(f"„{name}“ ist keine Aktionsabfrage.")
            temp = None
        else:
            assert sql is not None
            text = ensure_single_statement(sql)
            if classify_sql(text) in READ_ONLY_KINDS:
                raise AccessError("Die Anweisung ist keine Aktionsabfrage.")
            with com_guard("Aktionsabfrage vorbereiten"):
                temp = self._db.CreateQueryDef("", text)
            querydef = temp
        supplied = {k.lower().strip("[]"): v for k, v in (parameters or {}).items()}
        context = f"Aktionsabfrage „{name}“ ausführen" if name else "Aktionsabfrage ausführen"
        ws = self._s.workspace()
        try:
            with com_guard(context):
                missing = []
                for param in querydef.Parameters:
                    key = _param_name(param).lower()
                    if key in supplied:
                        param.Value = self._param_value(supplied[key])
                    else:
                        missing.append(_param_name(param))
                if missing:
                    raise SqlSyntaxError(
                        "Die Abfrage erwartet Werte für: " + ", ".join(missing)
                        + " (Parameter oder falsch geschriebener Feldname)."
                    )
                ws.BeginTrans()
                try:
                    querydef.Execute(c.DB_FAIL_ON_ERROR)
                    affected = int(querydef.RecordsAffected)
                except Exception:
                    ws.Rollback()
                    raise
                if max_affected is not None and affected > max_affected:
                    ws.Rollback()
                    error = ApprovalRequiredError(
                        f"{context}: {affected} Datensätze wären betroffen (Grenze {max_affected}). "
                        "Dafür ist eine zusätzliche Bestätigung nötig."
                    )
                    error.affected = affected  # type: ignore[attr-defined]
                    raise error
                ws.CommitTrans()
        finally:
            if temp is not None:
                with contextlib.suppress(Exception):
                    temp.Close()
        self._db.TableDefs.Refresh()
        self._s.refresh_navigation()
        return affected

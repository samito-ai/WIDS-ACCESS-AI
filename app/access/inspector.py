"""Database Inspector: liest die vollständige Struktur einer Access-Datenbank."""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from app.access import constants as c
from app.access.comerrors import com_guard
from app.access.constants import access_type_from_dao
from app.access.forms_reports import FormReportOps
from app.access.models import DatabaseSchema, FieldInfo, FormInfo, IndexInfo, ReportInfo, TableInfo
from app.access.queries import QueryOps
from app.access.relations import RelationOps
from app.access.session import AccessSession
from app.access.tables import datasheet_summary, get_property, is_system_table
from app.core.logging_setup import get_logger

log = get_logger(__name__)


def describe_table(tabledef: Any) -> TableInfo:
    indexes: list[IndexInfo] = []
    primary_key: list[str] = []
    field_index: dict[str, str] = {}
    with contextlib.suppress(Exception):
        for idx in tabledef.Indexes:
            names = [str(f.Name) for f in idx.Fields]
            descending = [bool(int(f.Attributes) & 1) for f in idx.Fields]
            info = IndexInfo(
                name=str(idx.Name),
                fields=names,
                descending=descending,
                primary=bool(idx.Primary),
                unique=bool(idx.Unique),
                required=bool(idx.Required),
                ignore_nulls=bool(idx.IgnoreNulls),
                foreign=bool(idx.Foreign),
            )
            indexes.append(info)
            if info.primary:
                primary_key = names
            if len(names) == 1 and not info.foreign:
                key = names[0].lower()
                mode = "unique" if info.unique else "duplicates"
                if field_index.get(key) != "unique":
                    field_index[key] = mode
    pk_lower = {n.lower() for n in primary_key}
    fields: list[FieldInfo] = []
    for fld in tabledef.Fields:
        dao_type = int(fld.Type)
        attributes = int(fld.Attributes)
        name = str(fld.Name)
        decimal_places = get_property(fld, "DecimalPlaces", None)
        is_lookup = int(get_property(fld, "DisplayControl", c.AC_TEXT_BOX) or c.AC_TEXT_BOX) in (c.AC_LIST_BOX, c.AC_COMBO_BOX)
        fields.append(
            FieldInfo(
                name=name,
                type=access_type_from_dao(dao_type, attributes),
                dao_type=dao_type,
                size=int(fld.Size) if dao_type == c.DB_TEXT else 0,
                required=bool(fld.Required),
                allow_zero_length=bool(get_property(fld, "AllowZeroLength", True)),
                default_value=str(fld.DefaultValue or ""),
                # DAO liefert diese Texte teils mit abschließendem NUL-Zeichen (gemessen: „Len([PLZ])=4\x00“)
                validation_rule=str(fld.ValidationRule or "").rstrip("\x00"),
                validation_text=str(fld.ValidationText or "").rstrip("\x00"),
                description=str(get_property(fld, "Description", "")),
                caption=str(get_property(fld, "Caption", "")),
                format=str(get_property(fld, "Format", "")),
                decimal_places=int(decimal_places) if decimal_places not in (None, "", 255) else None,
                input_mask=str(get_property(fld, "InputMask", "")),
                lookup_source=str(get_property(fld, "RowSource", "")) if is_lookup else "",
                lookup_limit_to_list=is_lookup and bool(get_property(fld, "LimitToList", False)),
                lookup_allow_edits=is_lookup and bool(get_property(fld, "AllowValueListEdits", False)),
                primary_key=name.lower() in pk_lower,
                indexed=field_index.get(name.lower(), "no"),  # type: ignore[arg-type]
                ordinal=int(fld.OrdinalPosition),
            )
        )
    connect = ""
    with contextlib.suppress(Exception):
        connect = str(tabledef.Connect or "")
    record_count = -1
    if not connect:
        with contextlib.suppress(Exception):
            record_count = int(tabledef.RecordCount)
    return TableInfo(
        name=str(tabledef.Name),
        fields=fields,
        indexes=indexes,
        primary_key=primary_key,
        record_count=record_count,
        linked=bool(connect),
        description=str(get_property(tabledef, "Description", "")),
        order_by=str(get_property(tabledef, "OrderBy", "")) if get_property(tabledef, "OrderByOn", False) else "",
        filter=str(get_property(tabledef, "Filter", "")) if get_property(tabledef, "FilterOnLoad", False) else "",
        validation_rule=str(getattr(tabledef, "ValidationRule", "") or "").rstrip("\x00"),
        datasheet=datasheet_summary(tabledef, with_formats=False),
    )


class DatabaseInspector:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    def inspect(self, *, detailed_objects: bool = False) -> DatabaseSchema:
        """Liest Tabellen, Beziehungen, Abfragen sowie Formular-/Berichtsnamen.

        ``detailed_objects=True`` öffnet Formulare und Berichte zusätzlich unsichtbar im Entwurf
        (benötigt Access) und liest Datenquelle und Steuerelemente aus.
        """
        self._s.refresh_db()
        db = self._s.db
        tables: list[TableInfo] = []
        with com_guard("Tabellen lesen"):
            db.TableDefs.Refresh()
            for tabledef in db.TableDefs:
                if is_system_table(tabledef):
                    continue
                tables.append(describe_table(tabledef))
        tables.sort(key=lambda t: t.name.lower())
        relations = RelationOps(self._s).list_relations()
        queries = QueryOps(self._s).list_queries()
        objects = FormReportOps(self._s)
        forms: list[FormInfo] = []
        reports: list[ReportInfo] = []
        with contextlib.suppress(Exception):
            for name in objects.form_names():
                forms.append(self._form(objects, name, detailed_objects))
        with contextlib.suppress(Exception):
            for name in objects.report_names():
                reports.append(self._report(objects, name, detailed_objects))
        properties: dict[str, str] = {}
        with contextlib.suppress(Exception):
            properties["Version"] = str(db.Version)
        with contextlib.suppress(Exception):
            properties["Sortierung"] = str(db.CollatingOrder)
        with contextlib.suppress(Exception):
            properties["Aktualisierbar"] = "Ja" if bool(db.Updatable) else "Nein"
        suffix = Path(self._s.path).suffix.lower()
        return DatabaseSchema(
            path=self._s.path,
            name=self._s.name,
            file_format="Access 2007–2019 (.accdb)" if suffix == ".accdb" else "Access 2000–2003 (.mdb)",
            read_only=self._s.read_only or properties.get("Aktualisierbar") == "Nein",
            attached_to_running_instance=self._s.mode == "attached",
            tables=tables,
            relations=relations,
            queries=queries,
            forms=forms,
            reports=reports,
            properties=properties,
        )

    @staticmethod
    def _form(objects: FormReportOps, name: str, detailed: bool) -> FormInfo:
        if detailed:
            try:
                return objects.inspect_form(name)
            except Exception as exc:
                log.debug("Formular %s nicht im Detail lesbar: %s", name, exc)
        return FormInfo(name=name)

    @staticmethod
    def _report(objects: FormReportOps, name: str, detailed: bool) -> ReportInfo:
        if detailed:
            try:
                return objects.inspect_report(name)
            except Exception as exc:
                log.debug("Bericht %s nicht im Detail lesbar: %s", name, exc)
        return ReportInfo(name=name)

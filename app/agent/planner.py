"""Solution Planner: prüft einen Lösungsplan gegen die tatsächliche Datenbankstruktur.

Der Plan wird Schritt für Schritt auf einer Kopie der Struktur „durchgespielt“. So werden
fehlende Tabellen/Felder, Namenskonflikte und Reihenfolgefehler erkannt, bevor irgendetwas
an der Datenbank verändert wird.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.access.constants import AccessType
from app.access.models import (
    DatabaseSchema,
    FieldInfo,
    FormInfo,
    IndexInfo,
    QueryInfo,
    RelationInfo,
    ReportInfo,
    TableInfo,
)
from app.access.specs import FieldSpec, QuerySpec
from app.access.sqlbuilder import (
    READ_ONLY_KINDS,
    build_sql,
    classify_sql,
    ensure_safe_fragment,
    ensure_single_statement,
)
from app.core.errors import WidsError
from app.tasks import models as m

Severity = Literal["error", "warning", "conflict"]


class PlanIssue(BaseModel):
    step_id: int
    severity: Severity
    message: str
    object_type: str = ""
    object_name: str = ""


class ValidationReport(BaseModel):
    issues: list[PlanIssue] = Field(default_factory=list)
    #: Aufgelöstes SQL je Schritt (für Vorschau und Ausführung)
    sql_by_step: dict[int, str] = Field(default_factory=dict)

    @property
    def errors(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def conflicts(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "conflict"]

    @property
    def warnings(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_text(self) -> str:
        return "\n".join(f"- Schritt {i.step_id}: {i.message}" for i in self.errors)


def expected_columns(spec: QuerySpec) -> list[str] | None:
    """Erwartete Ausgabespalten einer Auswahlabfrage (``None``, wenn nicht bestimmbar, z. B. bei ``*``)."""
    if spec.kind not in ("select",):
        return None
    totals = any(f.aggregate for f in spec.fields)
    columns: list[str] = []
    for fld in spec.fields:
        if not fld.show or fld.aggregate == "where":
            continue
        if fld.field and fld.field.strip().endswith("*"):
            return None
        if fld.alias:
            columns.append(fld.alias)
        elif totals and fld.aggregate and fld.aggregate not in ("group_by", "where") and fld.field:
            from app.access.sqlbuilder import _AGG_ALIAS_DE

            columns.append(f"{_AGG_ALIAS_DE[fld.aggregate]}{fld.field.strip('[]').split('.')[-1]}")
        elif fld.field:
            columns.append(fld.field.strip("[]").split(".")[-1])
    return columns


class _State:
    """Veränderliche Kopie der Datenbankstruktur."""

    def __init__(self, schema: DatabaseSchema | None) -> None:
        self.known = schema is not None
        self.schema = schema.model_copy(deep=True) if schema else DatabaseSchema(path="", name="")
        self._initial_structure = self._structure()

    def _structure(self) -> tuple[set[str], set[tuple[str, str]]]:
        return {t.name.lower() for t in self.schema.tables}, {(q.name.lower(), q.sql) for q in self.schema.queries}

    def structure_changed(self) -> bool:
        """Hat ein früherer Planschritt Tabellen oder Abfragen angelegt/geändert (z. B. eine Quelle repariert)?"""
        return self._structure() != self._initial_structure

    def table(self, name: str) -> TableInfo | None:
        return self.schema.table(name)

    def query(self, name: str) -> QueryInfo | None:
        return self.schema.query(name)

    def source_exists(self, name: str) -> bool:
        return self.table(name) is not None or self.query(name) is not None

    def object_type(self, name: str) -> str | None:
        kind = self.schema.has_object(name)
        if kind:
            return kind
        lowered = name.lower()
        if any(f.name.lower() == lowered for f in self.schema.forms):
            return "Formular"
        if any(r.name.lower() == lowered for r in self.schema.reports):
            return "Bericht"
        return None

    def remove_table(self, name: str) -> None:
        self.schema.tables = [t for t in self.schema.tables if t.name.lower() != name.lower()]

    def remove_query(self, name: str) -> None:
        self.schema.queries = [q for q in self.schema.queries if q.name.lower() != name.lower()]


def _field_info(spec: FieldSpec, primary: bool = False) -> FieldInfo:
    return FieldInfo(
        name=spec.name,
        type=spec.type,
        size=spec.size or (255 if spec.type == AccessType.SHORT_TEXT else 0),
        required=spec.required,
        primary_key=primary or spec.primary_key,
    )


class PlanValidator:
    def validate(self, plan: m.ExecutionPlan, schema: DatabaseSchema | None) -> ValidationReport:
        report = ValidationReport()
        state = _State(schema)
        for step in plan.steps:
            handler = getattr(self, f"_check_{step.action.action}", None)
            if handler is None:  # pragma: no cover - alle Aktionen sind abgedeckt
                report.issues.append(
                    PlanIssue(step_id=step.id, severity="error", message="Unbekannte Aktion.")
                )
                continue
            try:
                handler(step, state, report)
            except WidsError as exc:
                report.issues.append(PlanIssue(step_id=step.id, severity="error", message=exc.user_message))
        return report

    # ------------------------------------------------------------------ Hilfen
    @staticmethod
    def _err(report: ValidationReport, step: m.PlanStep, message: str) -> None:
        report.issues.append(PlanIssue(step_id=step.id, severity="error", message=message))

    @staticmethod
    def _warn(report: ValidationReport, step: m.PlanStep, message: str) -> None:
        report.issues.append(PlanIssue(step_id=step.id, severity="warning", message=message))

    @staticmethod
    def _conflict(report: ValidationReport, step: m.PlanStep, kind: str, name: str) -> None:
        report.issues.append(
            PlanIssue(
                step_id=step.id,
                severity="conflict",
                message=f"{kind} „{name}“ existiert bereits.",
                object_type=kind,
                object_name=name,
            )
        )

    def _need_table(self, report: ValidationReport, step: m.PlanStep, state: _State, name: str) -> TableInfo | None:
        table = state.table(name)
        if table is None and state.known:
            hint = ""
            if state.query(name):
                hint = " (Es gibt eine Abfrage mit diesem Namen – hier wird eine Tabelle benötigt.)"
            self._err(report, step, f"Die Tabelle „{name}“ existiert nicht.{hint}")
        return table

    def _need_field(
        self, report: ValidationReport, step: m.PlanStep, state: _State, table: TableInfo | None, field: str
    ) -> FieldInfo | None:
        if table is None:
            return None
        info = table.field(field)
        if info is None:
            available = ", ".join(table.field_names[:20])
            self._err(
                report, step, f"Das Feld „{field}“ gibt es in „{table.name}“ nicht. Vorhanden: {available}"
            )
        return info

    def _name_free(self, report: ValidationReport, step: m.PlanStep, state: _State, name: str) -> bool:
        kind = state.object_type(name)
        if kind in ("Tabelle", "Abfrage"):
            self._conflict(report, step, kind, name)
            return False
        return True

    # ------------------------------------------------------------------ Tabellen
    def _check_create_table(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateTable = step.action  # type: ignore[assignment]
        names = [f.name.lower() for f in action.fields]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            self._err(report, step, f"Doppelte Feldnamen: {', '.join(duplicates)}")
        pk = action.primary_key or [f.name for f in action.fields if f.primary_key]
        for key in pk:
            if key.lower() not in names:
                self._err(report, step, f"Das Primärschlüsselfeld „{key}“ ist nicht in der Feldliste enthalten.")
        if sum(1 for f in action.fields if f.type == AccessType.AUTONUMBER) > 1:
            self._err(report, step, "Eine Tabelle darf nur ein AutoWert-Feld enthalten.")
        if not pk:
            self._warn(report, step, f"Die Tabelle „{action.name}“ erhält keinen Primärschlüssel.")
        self._name_free(report, step, state, action.name)
        state.remove_table(action.name)
        pk_lower = {k.lower() for k in pk}
        state.schema.tables.append(
            TableInfo(
                name=action.name,
                fields=[_field_info(f, f.name.lower() in pk_lower) for f in action.fields],
                primary_key=list(pk),
                record_count=0,
            )
        )

    def _check_rename_table(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RenameTable = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.old_name)
        if action.old_name.lower() != action.new_name.lower():
            self._name_free(report, step, state, action.new_name)
        if table is not None:
            for rel in state.schema.relations:
                if rel.table.lower() == table.name.lower():
                    rel.table = action.new_name
                if rel.foreign_table.lower() == table.name.lower():
                    rel.foreign_table = action.new_name
            table.name = action.new_name

    def _check_copy_table(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CopyTable = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.source)
        self._name_free(report, step, state, action.new_name)
        if table is not None:
            copy = table.model_copy(deep=True)
            copy.name = action.new_name
            if not action.with_data:
                copy.record_count = 0
            state.remove_table(action.new_name)
            state.schema.tables.append(copy)

    def _check_delete_table(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteTable = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.name)
        if table is None:
            return
        used = [
            r.name
            for r in state.schema.relations
            if table.name.lower() in (r.table.lower(), r.foreign_table.lower())
        ]
        if used:
            self._err(
                report,
                step,
                f"„{table.name}“ wird in Beziehungen verwendet ({', '.join(used)}). Diese müssen zuerst gelöscht werden.",
            )
        state.remove_table(action.name)

    def _check_add_field(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.AddField = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is None:
            return
        if table.field(action.field.name):
            self._conflict(report, step, "Feld", f"{table.name}.{action.field.name}")
            return
        if action.field.type == AccessType.AUTONUMBER and any(
            f.type == AccessType.AUTONUMBER for f in table.fields
        ):
            self._err(report, step, f"„{table.name}“ enthält bereits ein AutoWert-Feld.")
        table.fields.append(_field_info(action.field))

    def _check_remove_field(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RemoveField = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        info = self._need_field(report, step, state, table, action.field)
        if table is None or info is None:
            return
        if info.primary_key:
            self._err(report, step, f"„{info.name}“ ist Primärschlüssel von „{table.name}“ und kann so nicht entfernt werden.")
        for rel in state.schema.relations:
            for primary, foreign in rel.fields:
                if (rel.table.lower() == table.name.lower() and primary.lower() == info.name.lower()) or (
                    rel.foreign_table.lower() == table.name.lower() and foreign.lower() == info.name.lower()
                ):
                    self._err(report, step, f"„{info.name}“ wird in der Beziehung „{rel.name}“ verwendet.")
        table.fields = [f for f in table.fields if f.name.lower() != info.name.lower()]

    def _check_rename_field(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RenameField = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        info = self._need_field(report, step, state, table, action.old_name)
        if table is None or info is None:
            return
        if action.old_name.lower() != action.new_name.lower() and table.field(action.new_name):
            self._conflict(report, step, "Feld", f"{table.name}.{action.new_name}")
            return
        dependents = [
            q.name for q in state.schema.queries if f"[{info.name.lower()}]" in q.sql.lower() or info.name.lower() in q.sql.lower()
        ]
        if dependents:
            self._warn(
                report, step,
                f"Abfragen, die „{info.name}“ verwenden, müssen eventuell angepasst werden: {', '.join(dependents[:5])}",
            )
        table.primary_key = [action.new_name if k.lower() == info.name.lower() else k for k in table.primary_key]
        info.name = action.new_name

    def _check_change_field_type(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.ChangeFieldType = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        info = self._need_field(report, step, state, table, action.field)
        if info is None or table is None:
            return
        if action.new_type in (AccessType.HYPERLINK, AccessType.ATTACHMENT, AccessType.BIGINT):
            self._err(report, step, "Dieser Datentyp kann nicht per automatischer Umwandlung gesetzt werden.")
        if action.new_type == AccessType.AUTONUMBER and table.record_count > 0:
            self._err(report, step, "Ein Feld mit vorhandenen Daten kann nicht in AutoWert umgewandelt werden.")
        if action.size and action.new_type != AccessType.SHORT_TEXT:
            self._warn(report, step, "Die Feldgröße wird nur bei „Kurzer Text“ berücksichtigt.")
        if table.record_count > 0 and info.type != action.new_type:
            self._warn(
                report, step,
                f"„{table.name}“ enthält {table.record_count} Datensätze – nicht umwandelbare Werte gehen verloren.",
            )
        info.type = action.new_type
        info.size = action.size or (info.size if action.new_type == AccessType.SHORT_TEXT else 0)

    def _check_set_field_properties(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetFieldProperties = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        info = self._need_field(report, step, state, table, action.field)
        if info is None:
            return
        props = action.properties
        if not props.model_dump(exclude_none=True):
            self._err(report, step, "Es wurde keine zu ändernde Eigenschaft angegeben.")
        if props.size is not None and info.type != AccessType.SHORT_TEXT:
            self._err(
                report, step,
                f"Eine Feldgröße in Zeichen gibt es nur bei Textfeldern („{info.name}“ ist {info.type_label}).",
            )
        if props.required and info.type == AccessType.AUTONUMBER:
            self._warn(report, step, "AutoWert-Felder sind immer gefüllt; „Eingabe erforderlich“ ist wirkungslos.")
        if props.size is not None:
            info.size = props.size
        if props.required is not None:
            info.required = props.required

    def _check_set_primary_key(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetPrimaryKey = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is None:
            return
        for name in action.fields:
            self._need_field(report, step, state, table, name)
        for rel in state.schema.relations:
            if rel.table.lower() == table.name.lower() and table.primary_key:
                self._warn(
                    report, step,
                    f"Der bisherige Primärschlüssel wird in der Beziehung „{rel.name}“ verwendet – Access kann die "
                    "Änderung ablehnen.",
                )
                break
        table.primary_key = list(action.fields)
        wanted = {n.lower() for n in action.fields}
        for fld in table.fields:
            fld.primary_key = fld.name.lower() in wanted

    def _check_create_index(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateIndex = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is None:
            return
        for name in action.fields:
            self._need_field(report, step, state, table, name)
        if any(i.name.lower() == action.name.lower() for i in table.indexes):
            self._conflict(report, step, "Index", f"{table.name}.{action.name}")
        else:  # Folgeschritte desselben Plans (z. B. delete_index) kennen den neuen Index
            table.indexes.append(IndexInfo(name=action.name, fields=list(action.fields), unique=action.unique))

    def _check_delete_index(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteIndex = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is not None and table.indexes and not any(i.name.lower() == action.name.lower() for i in table.indexes):
            self._err(report, step, f"Den Index „{action.name}“ gibt es in „{table.name}“ nicht.")
        elif table is not None:
            table.indexes = [i for i in table.indexes if i.name.lower() != action.name.lower()]

    def _check_set_table_sort_filter(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetTableSortFilter = step.action  # type: ignore[assignment]
        if state.known and not state.source_exists(action.table):
            self._err(report, step, f"Tabelle oder Abfrage „{action.table}“ gibt es nicht.")
        if action.order_by is None and action.filter is None:
            self._err(report, step, "Weder Sortierung noch Filter angegeben.")
        if action.order_by:
            ensure_safe_fragment(action.order_by, "Sortierung")
        if action.filter:
            ensure_safe_fragment(action.filter, "Filterbedingung")

    def _check_set_datasheet_layout(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetDatasheetLayout = step.action  # type: ignore[assignment]
        table, query = state.table(action.object), state.query(action.object)
        if table is None and query is None:
            if state.known:
                self._err(report, step, f"Die Tabelle/Abfrage „{action.object}“ existiert nicht.")
            return
        known = {f.lower() for f in (table.field_names if table else query.fields if query else [])}
        missing = sorted(n for n in action.layout.columns() if known and n.lower() not in known)
        if missing:
            self._err(report, step, f"Diese Spalten gibt es in „{action.object}“ nicht: {', '.join(missing)}")

    # ------------------------------------------------------------------ Daten
    def _check_insert_records(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.InsertRecords = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is None:
            return
        for number, record in enumerate(action.records, start=1):
            unknown = [k for k in record if table.field(k) is None]
            if unknown:
                self._err(report, step, f"Datensatz {number}: unbekannte Felder {', '.join(unknown)}")
            missing = [
                f.name
                for f in table.fields
                if f.required and f.type != AccessType.AUTONUMBER and not f.default_value
                and record.get(f.name) in (None, "") and f.name.lower() not in {k.lower() for k in record}
            ]
            if missing:
                self._err(report, step, f"Datensatz {number}: Pflichtfelder fehlen ({', '.join(missing)})")
        if table.record_count >= 0:
            table.record_count += len(action.records)

    def _check_import_data(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        from app.access import importer

        action: m.ImportData = step.action  # type: ignore[assignment]
        try:
            source = importer.resolve_source(action.file, state.schema.path)
            rows = importer.read_rows(source, delimiter=action.delimiter, widths=action.column_widths or None,
                                      sheet=action.sheet, has_header=action.has_header)
        except WidsError as exc:
            self._err(report, step, f"Quelldatei „{action.file}“: {exc.user_message} (gesucht im te.comp-Übungsordner "
                                    "und neben der Datenbank)")
            return
        header = rows[0] if action.has_header and rows else None
        data = rows[1:] if header is not None else rows
        width = max((len(r) for r in data), default=0)
        if not data:
            self._err(report, step, f"„{source.name}“ enthält keine Datensätze.")
            return
        if action.fields and len(action.fields) != width:
            self._err(report, step, f"„{source.name}“ hat {width} Spalten, angegeben sind {len(action.fields)} Felder.")
        table = state.table(action.table)
        if table is not None:
            for spec in action.fields:
                self._need_field(report, step, state, table, spec.name)
            if table.record_count >= 0:
                table.record_count += len(data)
            return
        if not self._name_free(report, step, state, action.table):
            return
        specs = list(action.fields) or importer.infer_fields(header, data)
        if action.add_id:
            specs = [FieldSpec(name=action.add_id, type=AccessType.AUTONUMBER), *specs]
        keys = action.primary_key or ([action.add_id] if action.add_id else [])
        names = {s.name.lower() for s in specs}
        for key in keys:
            if key.lower() not in names:
                self._err(report, step, f"Das Primärschlüsselfeld „{key}“ gibt es in der importierten Tabelle nicht.")
        keys_lower = {k.lower() for k in keys}
        state.schema.tables.append(TableInfo(name=action.table, fields=[_field_info(s, s.name.lower() in keys_lower) for s in specs],
                                             primary_key=list(keys), record_count=len(data)))

    def _check_update_records(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.UpdateRecords = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is not None:
            for name in action.values:
                info = self._need_field(report, step, state, table, name)
                if info is not None and info.type == AccessType.AUTONUMBER:
                    self._err(report, step, f"AutoWert-Felder („{info.name}“) können nicht geändert werden.")
        if action.where:
            ensure_safe_fragment(action.where)
        else:
            self._warn(report, step, "Ohne Bedingung werden ALLE Datensätze der Tabelle geändert.")

    def _check_delete_records(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteRecords = step.action  # type: ignore[assignment]
        self._need_table(report, step, state, action.table)
        if action.where:
            ensure_safe_fragment(action.where)
        else:
            self._warn(report, step, "Ohne Bedingung werden ALLE Datensätze der Tabelle gelöscht.")

    # ------------------------------------------------------------------ Beziehungen
    def _check_create_relationship(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateRelationship = step.action  # type: ignore[assignment]
        primary = self._need_table(report, step, state, action.table)
        foreign = self._need_table(report, step, state, action.foreign_table)
        for pair in action.fields:
            p_info = self._need_field(report, step, state, primary, pair.primary)
            f_info = self._need_field(report, step, state, foreign, pair.foreign)
            if p_info and f_info and not _types_compatible(p_info.type, f_info.type):
                self._err(
                    report, step,
                    f"Die Datentypen passen nicht zusammen: {action.table}.{pair.primary} ({p_info.type_label}) und "
                    f"{action.foreign_table}.{pair.foreign} ({f_info.type_label}).",
                )
        if primary is not None and action.enforce_integrity:
            keys = sorted(p.primary.lower() for p in action.fields)
            pk = sorted(k.lower() for k in primary.primary_key)
            unique_single = len(keys) == 1 and any(
                f.name.lower() == keys[0] and (f.primary_key or f.indexed == "unique") for f in primary.fields
            )
            if keys != pk and not unique_single:
                self._warn(
                    report, step,
                    f"Für referenzielle Integrität muss {action.table}.{', '.join(p.primary for p in action.fields)} "
                    "Primärschlüssel oder eindeutig indiziert sein.",
                )
        wanted = sorted((p.primary.lower(), p.foreign.lower()) for p in action.fields)
        for rel in state.schema.relations:
            if (
                rel.table.lower() == action.table.lower()
                and rel.foreign_table.lower() == action.foreign_table.lower()
                and sorted((a.lower(), b.lower()) for a, b in rel.fields) == wanted
            ):
                self._conflict(report, step, "Beziehung", f"{action.table} → {action.foreign_table}")
                return
        state.schema.relations.append(
            RelationInfo(
                name=action.name or f"{action.table}{action.foreign_table}",
                table=action.table,
                foreign_table=action.foreign_table,
                fields=[(p.primary, p.foreign) for p in action.fields],
                enforce_integrity=action.enforce_integrity,
                cascade_update=action.cascade_update,
                cascade_delete=action.cascade_delete,
            )
        )

    def _check_delete_relationship(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteRelationship = step.action  # type: ignore[assignment]
        found = None
        for rel in state.schema.relations:
            if (action.name and rel.name.lower() == action.name.lower()) or (
                not action.name
                and action.table
                and action.foreign_table
                and rel.table.lower() == action.table.lower()
                and rel.foreign_table.lower() == action.foreign_table.lower()
            ):
                found = rel
        if found is None:
            if state.known:
                self._err(report, step, f"Die Beziehung „{action.target()}“ existiert nicht.")
            return
        state.schema.relations.remove(found)

    # ------------------------------------------------------------------ Abfragen
    def _check_create_query(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateQuery = step.action  # type: ignore[assignment]
        fields: list[str] = []
        if action.spec is not None:
            spec = action.spec
            for source in spec.tables:
                if state.known and not state.source_exists(source):
                    self._err(report, step, f"Die Tabelle/Abfrage „{source}“ existiert nicht.")
            if state.known:
                self._check_spec_fields(step, state, report, spec)
            if spec.kind in ("append",) and spec.target_table and state.known and state.table(spec.target_table) is None:
                self._err(report, step, f"Die Zieltabelle „{spec.target_table}“ existiert nicht.")
            if spec.kind == "make_table" and spec.target_table and state.table(spec.target_table) is not None:
                self._warn(
                    report, step,
                    f"Beim Ausführen der Abfrage würde die vorhandene Tabelle „{spec.target_table}“ ersetzt.",
                )
            if len(spec.tables) > 1 and not spec.joins and state.known:
                from app.access.sqlbuilder import infer_joins

                if not infer_joins(spec.tables, state.schema.relations):
                    self._warn(
                        report, step,
                        "Zwischen den Tabellen besteht keine Beziehung und es ist keine Verknüpfung angegeben – "
                        "das Ergebnis wäre ein Kreuzprodukt.",
                    )
            sql = build_sql(spec, state.schema if state.known else None)
            fields = expected_columns(spec) or []
        else:
            assert action.sql is not None
            sql = ensure_single_statement(action.sql)
            kind = classify_sql(sql)
            if kind == "unknown":
                self._err(report, step, "Die SQL-Anweisung ist keine gültige Access-Abfrage (SELECT/UPDATE/DELETE/INSERT/TRANSFORM).")
            if kind == "ddl":
                self._warn(report, step, "Die Abfrage ist eine Datendefinitionsabfrage (ändert die Struktur beim Ausführen).")
        report.sql_by_step[step.id] = sql
        kind_name = state.object_type(action.name)
        if action.modify_existing:
            if kind_name != "Abfrage" and state.known:
                self._err(report, step, f"„{action.name}“ ist keine vorhandene Abfrage (modify_existing).")
        elif kind_name in ("Tabelle", "Abfrage"):
            self._conflict(report, step, kind_name, action.name)
        state.remove_query(action.name)
        state.schema.queries.append(QueryInfo(name=action.name, sql=sql, fields=fields))

    def _check_spec_fields(self, step: m.PlanStep, state: _State, report: ValidationReport, spec: QuerySpec) -> None:
        available: dict[str, set[str]] = {
            source.lower(): {f.lower() for f in state.schema.source_fields(source)} for source in spec.tables
        }
        everything = set().union(*available.values()) if available else set()
        unknown_sources = [s for s, fields in available.items() if not fields]

        def check(name: str | None, table: str | None) -> None:
            if not name or name.strip() in ("*",) or name.strip().endswith(".*"):
                return
            clean = name.strip().strip("[]")
            owner = table.strip("[]").lower() if table else None
            if "." in clean and owner is None:
                owner, clean = clean.split(".", 1)
                owner = owner.strip("[]").lower()
                clean = clean.strip("[]")
            if owner is not None:
                if owner not in available:
                    self._err(report, step, f"Die Tabelle „{table or owner}“ ist nicht Teil der Abfrage.")
                elif available[owner] and clean.lower() not in available[owner]:
                    self._err(report, step, f"Das Feld „{clean}“ gibt es in „{owner}“ nicht.")
            elif clean.lower() not in everything and not unknown_sources:
                self._err(
                    report, step,
                    f"Das Feld „{clean}“ gibt es in {', '.join(spec.tables)} nicht.",
                )

        for fld in spec.fields:
            check(fld.field, fld.table)
        for join in spec.joins:
            check(join.left_field, join.left_table)
            check(join.right_field, join.right_table)
        for name in spec.set_values:
            check(name, None)
        if spec.crosstab:
            for name in (*spec.crosstab.row_headings, spec.crosstab.column_heading, spec.crosstab.value_field):
                check(name, None)

    def _check_delete_query(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteQuery = step.action  # type: ignore[assignment]
        if state.known and state.query(action.name) is None:
            self._err(report, step, f"Die Abfrage „{action.name}“ existiert nicht.")
        state.remove_query(action.name)

    def _check_rename_query(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RenameQuery = step.action  # type: ignore[assignment]
        query = state.query(action.old_name)
        if query is None:
            if state.known:
                self._err(report, step, f"Die Abfrage „{action.old_name}“ existiert nicht.")
            return
        if action.old_name.lower() != action.new_name.lower():
            self._name_free(report, step, state, action.new_name)
        query.name = action.new_name

    def _check_run_action_query(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RunActionQuery = step.action  # type: ignore[assignment]
        if action.name is not None:
            if state.known and state.query(action.name) is None:
                self._err(report, step, f"Die Abfrage „{action.name}“ existiert nicht.")
            return
        assert action.sql is not None
        sql = ensure_single_statement(action.sql)
        kind = classify_sql(sql)
        if kind in ("select", "union", "crosstab", "unknown"):
            self._err(report, step, "Die Anweisung ist keine Aktionsabfrage.")
        report.sql_by_step[step.id] = sql

    # ------------------------------------------------------------------ Formulare / Berichte
    def _check_create_form(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateForm = step.action  # type: ignore[assignment]
        form = action.form
        self._check_broken_source(step, state, report, form.record_source)
        if form.record_source and state.known:
            if not state.source_exists(form.record_source) and not form.record_source.upper().startswith("SELECT "):
                self._err(report, step, f"Die Datenquelle „{form.record_source}“ existiert nicht.")
            else:
                available = {f.lower() for f in state.schema.source_fields(form.record_source)}
                bound = [*form.fields, *(c.field for c in form.controls if c.field and not c.field.startswith("="))]
                missing = [f for f in bound if available and f.lower() not in available]
                if missing:
                    self._err(report, step, f"Felder fehlen in „{form.record_source}“: {', '.join(missing)}")
        for ctl in form.controls:
            if ctl.type == "subform" and ctl.source_object and "." not in ctl.source_object and state.known \
                    and not self._form_known(state, ctl.source_object):
                self._err(report, step, f"Das Formular „{ctl.source_object}“ für das Unterformular existiert nicht "
                                        "(vorher mit create_form anlegen).")
        if not form.fields and not form.controls:
            self._warn(report, step, "Das Formular enthält keine Steuerelemente.")
        if not form.record_source and (form.fields or any(c.field for c in form.controls)):
            self._err(report, step, "Für gebundene Felder muss eine Datenquelle (record_source) angegeben werden.")
        if any(f.name.lower() == form.name.lower() for f in state.schema.forms):
            self._conflict(report, step, "Formular", form.name)
        else:
            state.schema.forms.append(FormInfo(name=form.name, record_source=form.record_source or ""))

    def _check_create_report(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateReport = step.action  # type: ignore[assignment]
        spec = action.report
        self._check_broken_source(step, state, report, spec.record_source)
        if state.known:
            if not state.source_exists(spec.record_source) and not spec.record_source.upper().startswith("SELECT "):
                self._err(report, step, f"Die Datenquelle „{spec.record_source}“ existiert nicht.")
            else:
                available = {f.lower() for f in state.schema.source_fields(spec.record_source)}
                needed = [*spec.fields, *(g.field for g in spec.group_by), *(s.field for s in spec.sort_by)]
                needed += [t.field for t in spec.totals if t.field != "*"]
                missing = sorted({f for f in needed if available and f.lower() not in available})
                if missing:
                    self._err(report, step, f"Felder fehlen in „{spec.record_source}“: {', '.join(missing)}")
                from app.access.forms_reports import expression_names

                own = {(ctl.name or "").lower() for ctl in spec.controls} | {"page", "pages", "seite", "seiten"}
                unknown = sorted({n for ctl in spec.controls if ctl.expression and ctl.expression.startswith("=")
                                  for n in expression_names(ctl.expression[1:])} - available - own) if available else []
                if unknown:  # Access zeigt sonst #Name? bzw. fragt beim Öffnen nach Parametern
                    self._warn(report, step, f"Unbekannte Namen in Ausdrücken: {', '.join(unknown)}")
        if any(t.scope in ("group", "both") for t in spec.totals) and not spec.group_by:
            self._warn(report, step, "Gruppensummen sind angegeben, aber keine Gruppierung – sie erscheinen nur am Berichtsende.")
        if any(r.name.lower() == spec.name.lower() for r in state.schema.reports):
            self._conflict(report, step, "Bericht", spec.name)
        else:
            state.schema.reports.append(ReportInfo(name=spec.name, record_source=spec.record_source))

    def _check_rename_object(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.RenameObject = step.action  # type: ignore[assignment]
        items = state.schema.forms if action.object_type == "form" else state.schema.reports
        label = "Formular" if action.object_type == "form" else "Bericht"
        match = next((i for i in items if i.name.lower() == action.old_name.lower()), None)
        if match is None:
            if state.known:
                self._err(report, step, f"{label} „{action.old_name}“ existiert nicht.")
            return
        if any(i.name.lower() == action.new_name.lower() for i in items if i is not match):
            self._conflict(report, step, label, action.new_name)
        match.name = action.new_name

    @staticmethod
    def _form_known(state: _State, name: str) -> bool:
        return any(f.name.lower() == name.lower() for f in state.schema.forms)

    @staticmethod
    def _report_known(state: _State, name: str) -> bool:
        return any(r.name.lower() == name.lower() for r in state.schema.reports)

    def _check_new_source(self, step: m.PlanStep, state: _State, report: ValidationReport, source: str | None) -> None:
        """Neue Datenquelle eines vorhandenen Formulars/Berichts: Tabelle/Abfrage muss existieren (oder SELECT)."""
        is_sql = source is not None and source.lstrip()[:6].upper() == "SELECT" and source.lstrip()[6:7].isspace()
        if source and state.known and not state.source_exists(source) and not is_sql:
            self._err(report, step, f"Die neue Datenquelle „{source}“ existiert nicht.")
        self._check_broken_source(step, state, report, source)

    def _check_broken_source(self, step: m.PlanStep, state: _State, report: ValidationReport, source: str | None) -> None:
        """Eine Abfrage, die Access nicht ausführen kann, ist keine Datenquelle (Formular bliebe leer/fragt nach
        Parametern) – die KI soll die zugrunde liegende Tabelle nehmen oder die Abfrage zuerst reparieren."""
        query = state.schema.query(source) if source and state.known else None
        # nach einer Strukturänderung im Plan (Quelle repariert/angelegt) ist der gelesene Fehler evtl. überholt
        if query is not None and query.error and not state.structure_changed():
            self._err(report, step, f"Die Abfrage „{source}“ ist nicht ausführbar ({query.error}). Verwende die "
                                    "Tabelle mit den benötigten Feldern oder repariere die Abfrage zuerst.")

    def _check_modify_report(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.ModifyReport = step.action  # type: ignore[assignment]
        if state.known and not self._report_known(state, action.name):
            self._err(report, step, f"Bericht „{action.name}“ existiert nicht.")
        self._check_new_source(step, state, report, action.changes.record_source)
        for fragment in (action.changes.filter, action.changes.order_by):
            if fragment:
                ensure_safe_fragment(fragment, "Filter/Sortierung des Berichts")

    def _check_set_field_from_file(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetFieldFromFile = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is not None:
            self._need_field(report, step, state, table, action.field)

    def _check_map_keys(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.MapKeys = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is not None:
            for name in [action.target_field, *action.source_fields]:
                self._need_field(report, step, state, table, name)
        lookup = self._need_table(report, step, state, action.lookup_table)
        if lookup is not None:
            for name in [action.lookup_key, *action.lookup_fields]:
                self._need_field(report, step, state, lookup, name)

    def _check_attach_files(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.AttachFiles = step.action  # type: ignore[assignment]
        table = self._need_table(report, step, state, action.table)
        if table is None:
            return
        self._need_field(report, step, state, table, action.key_field)
        target = self._need_field(report, step, state, table, action.field)
        if target is not None and target.type != AccessType.ATTACHMENT:
            self._err(report, step, f"„{table.name}.{target.name}“ ist kein Anlagefeld (Typ „attachment“).")

    def _check_transfer_objects(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.TransferObjects = step.action  # type: ignore[assignment]
        if action.direction == "link" and any(i.type != "table" for i in action.objects):
            self._err(report, step, "Verknüpfen ist nur für Tabellen möglich.")
        if action.create_new and action.direction != "export":
            self._err(report, step, "Eine neue Datenbank kann nur als Exportziel angelegt werden.")
        if not state.known:
            return
        if action.direction == "export":
            known = {"table": state.table, "query": state.query,
                     "form": lambda n: self._form_known(state, n) or None,
                     "report": lambda n: self._report_known(state, n) or None}
            missing = [i.name for i in action.objects if not known[i.type](i.name)]
            if missing:
                self._err(report, step, f"Diese Objekte gibt es nicht: {', '.join(missing)}")
            return
        for item in action.objects:
            if state.object_type(item.target):
                self._conflict(report, step, state.object_type(item.target) or "Objekt", item.target)
            elif item.type == "table":
                state.schema.tables.append(TableInfo(name=item.target, linked=action.direction == "link"))

    def _check_answer_query(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.AnswerQuery = step.action  # type: ignore[assignment]
        try:
            kind = classify_sql(ensure_single_statement(action.sql))
        except WidsError as exc:
            self._err(report, step, exc.user_message)
            return
        if kind not in READ_ONLY_KINDS:
            self._err(report, step, "Eine Datenfrage darf nur mit einer lesenden Abfrage (SELECT) beantwortet werden.")

    def _check_calculate_value(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        from app.agent import calc

        action: m.CalculateValue = step.action  # type: ignore[assignment]
        try:
            calc.evaluate(action.expression)
            unknown = calc.unknown_numbers(action.expression, action.given)
        except calc.CalcError as exc:
            self._err(report, step, f"Rechenausdruck nicht zulässig: {exc}")
            return
        if unknown:
            self._err(report, step, "Rechenausdruck enthält Zahlen, die nicht in den Angaben stehen (nie erfinden): "
                      + ", ".join(unknown))

    def _check_modify_form(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.ModifyForm = step.action  # type: ignore[assignment]
        if state.known and not self._form_known(state, action.name):
            self._err(report, step, f"Formular „{action.name}“ existiert nicht.")
        self._check_new_source(step, state, report, action.changes.record_source)

    def _check_set_conditional_format(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.SetConditionalFormat = step.action  # type: ignore[assignment]
        known = self._form_known if action.object_type == "form" else self._report_known
        if state.known and not known(state, action.name):
            label = "Formular" if action.object_type == "form" else "Bericht"
            self._err(report, step, f"{label} „{action.name}“ existiert nicht.")

    def _check_create_navigation_form(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.CreateNavigationForm = step.action  # type: ignore[assignment]
        for tab in action.tabs:
            known = self._form_known if tab.target_type == "form" else self._report_known
            if state.known and not known(state, tab.target):
                self._err(report, step, f"Ziel „{tab.target}“ der Registerkarte „{tab.caption}“ existiert nicht.")
        if self._form_known(state, action.name):
            self._conflict(report, step, "Formular", action.name)
        else:
            state.schema.forms.append(FormInfo(name=action.name))

    def _check_export_data(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.ExportData = step.action  # type: ignore[assignment]
        if state.known and not state.source_exists(action.source):
            self._err(report, step, f"Die Tabelle/Abfrage „{action.source}“ existiert nicht.")

    def _check_import_odbc(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.ImportOdbc = step.action  # type: ignore[assignment]
        if self._name_free(report, step, state, action.table):
            state.schema.tables.append(TableInfo(name=action.table, linked=action.link))

    def _check_delete_object(self, step: m.PlanStep, state: _State, report: ValidationReport) -> None:
        action: m.DeleteObject = step.action  # type: ignore[assignment]
        label = "Formular" if action.object_type == "form" else "Bericht"
        if action.object_type == "form":
            if state.known and not any(f.name.lower() == action.name.lower() for f in state.schema.forms):
                self._err(report, step, f"{label} „{action.name}“ existiert nicht.")
            state.schema.forms = [f for f in state.schema.forms if f.name.lower() != action.name.lower()]
        else:
            if state.known and not any(r.name.lower() == action.name.lower() for r in state.schema.reports):
                self._err(report, step, f"{label} „{action.name}“ existiert nicht.")
            state.schema.reports = [r for r in state.schema.reports if r.name.lower() != action.name.lower()]


_GROUPS = [
    {AccessType.LONG, AccessType.AUTONUMBER},
    {AccessType.SHORT_TEXT},
    {AccessType.INTEGER},
    {AccessType.BYTE},
    {AccessType.DOUBLE},
    {AccessType.SINGLE},
    {AccessType.CURRENCY},
    {AccessType.DATETIME},
    {AccessType.GUID},
    {AccessType.DECIMAL},
    {AccessType.BIGINT},
]


def _types_compatible(a: AccessType, b: AccessType) -> bool:
    return any(a in group and b in group for group in _GROUPS)

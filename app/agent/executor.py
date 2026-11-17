"""Access Executor: setzt genau eine validierte Aktion über die Access-Engine um.

Der Executor kennt ausschließlich die Aktionen aus ``app.tasks.models``. Es gibt keinen Pfad,
über den Text aus einer KI-Antwort als Programmcode oder Shell-Befehl ausgeführt würde.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.access.engine import AccessEngine
from app.access.models import DatabaseSchema
from app.access.naming import quote_ident
from app.access.sqlbuilder import build_sql, classify_sql, ensure_single_statement
from app.core.errors import ObjectNotFoundError, WidsError
from app.core.logging_setup import get_logger
from app.tasks import models as m

log = get_logger(__name__)

StepStatus = Literal["ok", "skipped", "failed"]


class StepResult(BaseModel):
    step_id: int
    status: StepStatus = "ok"
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    error_type: str = ""
    sql: str = ""
    #: Tatsächlich verwendeter Objektname (weicht nach einer Konfliktlösung „umbenennen“ ab)
    final_name: str = ""
    duration_s: float = 0.0
    attempts: int = 1


class ExecOptions(BaseModel):
    #: Vorhandenes gleichnamiges Objekt ersetzen (nur nach ausdrücklicher Entscheidung)
    replace: bool = False
    #: Objekt unter anderem Namen anlegen
    rename_to: str | None = None
    #: Obergrenze betroffener Datensätze; ``None`` = vom Benutzer bestätigt, keine Grenze
    max_affected: int | None = None
    #: Korrigiertes SQL (Recovery) statt des geplanten
    sql_override: str | None = None


#: so viele Schlüssel geänderter Datensätze merkt sich WIDS für die Prüfung (darüber: Prüfung über die Bedingung)
_MAX_TRACKED_KEYS = 2000


class AccessExecutor:
    def execute(self, engine: AccessEngine, step: m.PlanStep, options: ExecOptions | None = None) -> StepResult:
        """Führt den Schritt aus. Fehler der Engine werden als ``WidsError`` weitergereicht."""
        opts = options or ExecOptions()
        handler = getattr(self, f"_do_{step.action.action}")
        started = time.perf_counter()
        result: StepResult = handler(engine, step.action, opts)
        result.step_id = step.id
        result.duration_s = round(time.perf_counter() - started, 3)
        log.info("Schritt %d (%s) ausgeführt: %s", step.id, step.action.action, result.message)
        return result

    def resolve_sql(self, action: m.CreateQuery, schema: DatabaseSchema | None) -> str:
        if action.spec is not None:
            return build_sql(action.spec, schema)
        assert action.sql is not None
        return ensure_single_statement(action.sql)

    # ------------------------------------------------------------------ Tabellen
    def _do_create_table(self, e: AccessEngine, a: m.CreateTable, o: ExecOptions) -> StepResult:
        name = o.rename_to or a.name
        replaced = False
        if o.replace and e.tables.table_exists(name):
            e.tables.delete_table(name)
            replaced = True
        e.tables.create_table(name, a.fields, a.primary_key or None, a.description)
        text = f"Tabelle „{name}“ mit {len(a.fields)} Feldern erstellt"
        return StepResult(step_id=0, message=text + (" (vorhandene ersetzt)" if replaced else ""), final_name=name,
                          details={"replaced": replaced})

    def _do_rename_table(self, e: AccessEngine, a: m.RenameTable, o: ExecOptions) -> StepResult:
        new = o.rename_to or a.new_name
        e.tables.rename_table(a.old_name, new)
        return StepResult(step_id=0, message=f"Tabelle „{a.old_name}“ in „{new}“ umbenannt", final_name=new)

    def _do_copy_table(self, e: AccessEngine, a: m.CopyTable, o: ExecOptions) -> StepResult:
        new = o.rename_to or a.new_name
        if o.replace and e.tables.table_exists(new):
            e.tables.delete_table(new)
        copied = e.tables.copy_table(a.source, new, with_data=a.with_data)
        return StepResult(step_id=0, message=f"Tabelle „{a.source}“ nach „{new}“ kopiert ({copied} Datensätze)",
                          final_name=new, details={"copied": copied})

    def _do_delete_table(self, e: AccessEngine, a: m.DeleteTable, o: ExecOptions) -> StepResult:
        count = e.tables.count_records(a.name)
        e.tables.delete_table(a.name)
        return StepResult(step_id=0, message=f"Tabelle „{a.name}“ gelöscht ({count} Datensätze)", details={"records": count})

    def _do_add_field(self, e: AccessEngine, a: m.AddField, o: ExecOptions) -> StepResult:
        e.tables.add_field(a.table, a.field)
        return StepResult(step_id=0, message=f"Feld „{a.field.name}“ zu „{a.table}“ hinzugefügt")

    def _do_remove_field(self, e: AccessEngine, a: m.RemoveField, o: ExecOptions) -> StepResult:
        e.tables.remove_field(a.table, a.field)
        return StepResult(step_id=0, message=f"Feld „{a.field}“ aus „{a.table}“ entfernt")

    def _do_rename_field(self, e: AccessEngine, a: m.RenameField, o: ExecOptions) -> StepResult:
        e.tables.rename_field(a.table, a.old_name, a.new_name)
        return StepResult(step_id=0, message=f"Feld „{a.table}.{a.old_name}“ in „{a.new_name}“ umbenannt")

    def _do_change_field_type(self, e: AccessEngine, a: m.ChangeFieldType, o: ExecOptions) -> StepResult:
        e.tables.change_field_type(a.table, a.field, a.new_type, a.size)
        return StepResult(step_id=0, message=f"Datentyp von „{a.table}.{a.field}“ geändert")

    def _do_set_field_properties(self, e: AccessEngine, a: m.SetFieldProperties, o: ExecOptions) -> StepResult:
        e.tables.set_field_properties(a.table, a.field, a.properties)
        changed = ", ".join(a.properties.model_dump(exclude_none=True))
        return StepResult(step_id=0, message=f"Eigenschaften von „{a.table}.{a.field}“ gesetzt ({changed})")

    def _do_set_primary_key(self, e: AccessEngine, a: m.SetPrimaryKey, o: ExecOptions) -> StepResult:
        e.tables.set_primary_key(a.table, a.fields)
        return StepResult(step_id=0, message=f"Primärschlüssel von „{a.table}“: {', '.join(a.fields)}")

    def _do_create_index(self, e: AccessEngine, a: m.CreateIndex, o: ExecOptions) -> StepResult:
        if o.replace:
            try:
                e.tables.delete_index(a.table, a.name)
            except ObjectNotFoundError:
                pass
        e.tables.create_index(a.table, a.name, a.fields, unique=a.unique)
        return StepResult(step_id=0, message=f"Index „{a.name}“ auf „{a.table}“ erstellt")

    def _do_delete_index(self, e: AccessEngine, a: m.DeleteIndex, o: ExecOptions) -> StepResult:
        e.tables.delete_index(a.table, a.name)
        return StepResult(step_id=0, message=f"Index „{a.name}“ von „{a.table}“ gelöscht")

    def _do_set_table_sort_filter(self, e: AccessEngine, a: m.SetTableSortFilter, o: ExecOptions) -> StepResult:
        e.tables.set_table_sort_filter(a.table, order_by=a.order_by, filter_expr=a.filter)
        return StepResult(step_id=0, message=f"Sortierung/Filter für „{a.table}“ gespeichert")

    def _do_set_datasheet_layout(self, e: AccessEngine, a: m.SetDatasheetLayout, o: ExecOptions) -> StepResult:
        e.tables.set_datasheet_layout(a.object, a.layout)
        return StepResult(step_id=0, message=f"Datenblattansicht von „{a.object}“ eingerichtet")

    # ------------------------------------------------------------------ Daten
    def _do_insert_records(self, e: AccessEngine, a: m.InsertRecords, o: ExecOptions) -> StepResult:
        before = e.tables.count_records(a.table)
        inserted = e.tables.insert_records(a.table, a.records)
        return StepResult(step_id=0, message=f"{inserted} Datensätze in „{a.table}“ eingefügt",
                          details={"count_before": before, "inserted": inserted})

    def _do_import_data(self, e: AccessEngine, a: m.ImportData, o: ExecOptions) -> StepResult:
        from app.access import importer

        table = o.rename_to or a.table
        source = importer.resolve_source(a.file, e.path)
        rows = importer.read_rows(source, delimiter=a.delimiter, widths=a.column_widths or None, sheet=a.sheet,
                                  has_header=a.has_header)
        before = e.tables.count_records(table) if e.tables.table_exists(table) else 0
        inserted, created = e.tables.import_rows(table, rows, has_header=a.has_header, fields=a.fields or None,
                                                 primary_key=a.primary_key or None, add_id=a.add_id)
        where = "in die neue Tabelle" if created else "in die vorhandene Tabelle"
        return StepResult(step_id=0, message=f"{inserted} Datensätze aus „{source.name}“ {where} „{table}“ importiert",
                          final_name=table, details={"count_before": before, "inserted": inserted, "created": created,
                                                       "source": str(source),
                                                       "source_rows": len(rows) - (1 if a.has_header and rows else 0)})

    def _do_update_records(self, e: AccessEngine, a: m.UpdateRecords, o: ExecOptions) -> StepResult:
        details: dict[str, Any] = {}
        key = e.tables.primary_key(a.table)
        if len(key) == 1 and key[0].lower() not in {k.lower() for k in a.values}:  # genau DIESE Datensätze merken
            sql = f"SELECT {quote_ident(key[0])} FROM {quote_ident(a.table)}" + (f" WHERE {a.where}" if a.where else "")
            _, rows = e.tables.read_sql(sql, limit=_MAX_TRACKED_KEYS + 1)
            if len(rows) <= _MAX_TRACKED_KEYS:
                details |= {"key_field": key[0], "target_keys": [row[0] for row in rows]}
        affected = e.tables.update_records(a.table, a.values, a.where, max_affected=o.max_affected)
        return StepResult(step_id=0, message=f"{affected} Datensätze in „{a.table}“ geändert",
                          details={"affected": affected, **details})

    def _do_delete_records(self, e: AccessEngine, a: m.DeleteRecords, o: ExecOptions) -> StepResult:
        before = e.tables.count_records(a.table)
        affected = e.tables.delete_records(a.table, a.where, max_affected=o.max_affected)
        return StepResult(step_id=0, message=f"{affected} Datensätze aus „{a.table}“ gelöscht",
                          details={"affected": affected, "count_before": before})

    # ------------------------------------------------------------------ Beziehungen
    def _do_create_relationship(self, e: AccessEngine, a: m.CreateRelationship, o: ExecOptions) -> StepResult:
        if o.replace:
            existing = e.relations.find_relation(a.table, a.foreign_table, a.fields)
            if existing is not None:
                e.relations.delete_relationship(existing.name)
        name = e.relations.create_relationship(
            a.table, a.foreign_table, a.fields, name=a.name, enforce_integrity=a.enforce_integrity,
            cascade_update=a.cascade_update, cascade_delete=a.cascade_delete, join_type=a.join_type,
        )
        return StepResult(step_id=0, message=f"Beziehung {a.table} → {a.foreign_table} erstellt", final_name=name)

    def _do_delete_relationship(self, e: AccessEngine, a: m.DeleteRelationship, o: ExecOptions) -> StepResult:
        name = a.name
        if not name:
            assert a.table and a.foreign_table
            found = e.relations.find_relation(a.table, a.foreign_table)
            if found is None:
                raise ObjectNotFoundError("Beziehung", a.target())
            name = found.name
        e.relations.delete_relationship(name)
        return StepResult(step_id=0, message=f"Beziehung „{name}“ gelöscht", final_name=name)

    # ------------------------------------------------------------------ Abfragen
    def _do_create_query(self, e: AccessEngine, a: m.CreateQuery, o: ExecOptions) -> StepResult:
        name = o.rename_to or a.name
        sql = o.sql_override or self.resolve_sql(a, e.schema())
        info = e.queries.create_query(name, sql, replace=o.replace or a.modify_existing, description=a.description)
        return StepResult(step_id=0, message=f"{info.type_name} „{name}“ gespeichert", sql=info.sql, final_name=name,
                          details={"type": info.type_name, "fields": info.fields,
                                   "parameters": [p.name for p in info.parameters]})

    def _do_delete_query(self, e: AccessEngine, a: m.DeleteQuery, o: ExecOptions) -> StepResult:
        e.queries.delete_query(a.name)
        return StepResult(step_id=0, message=f"Abfrage „{a.name}“ gelöscht")

    def _do_rename_query(self, e: AccessEngine, a: m.RenameQuery, o: ExecOptions) -> StepResult:
        new = o.rename_to or a.new_name
        e.queries.rename_query(a.old_name, new)
        return StepResult(step_id=0, message=f"Abfrage „{a.old_name}“ in „{new}“ umbenannt", final_name=new)

    def _do_run_action_query(self, e: AccessEngine, a: m.RunActionQuery, o: ExecOptions) -> StepResult:
        sql = o.sql_override or a.sql
        if a.name is not None:
            affected = e.queries.run_action(name=a.name, parameters=a.parameters, max_affected=o.max_affected)
            return StepResult(step_id=0, message=f"Aktionsabfrage „{a.name}“ ausgeführt: {affected} Datensätze betroffen",
                              details={"affected": affected})
        assert sql is not None
        affected = e.queries.run_action(sql=sql, parameters=a.parameters, max_affected=o.max_affected)
        return StepResult(step_id=0, message=f"Aktionsabfrage ausgeführt: {affected} Datensätze betroffen", sql=sql,
                          details={"affected": affected, "kind": classify_sql(sql)})

    # ------------------------------------------------------------------ Formulare / Berichte
    def _do_create_form(self, e: AccessEngine, a: m.CreateForm, o: ExecOptions) -> StepResult:
        spec = a.form.model_copy(update={"name": o.rename_to}) if o.rename_to else a.form
        name = e.objects.create_form(spec, replace=o.replace)
        return StepResult(step_id=0, message=f"Formular „{name}“ erstellt", final_name=name)

    def _do_create_report(self, e: AccessEngine, a: m.CreateReport, o: ExecOptions) -> StepResult:
        spec = a.report.model_copy(update={"name": o.rename_to}) if o.rename_to else a.report
        name = e.objects.create_report(spec, replace=o.replace)
        return StepResult(step_id=0, message=f"Bericht „{name}“ erstellt", final_name=name)

    def _do_set_conditional_format(self, e: AccessEngine, a: m.SetConditionalFormat, o: ExecOptions) -> StepResult:
        count = e.objects.set_conditional_format(a.object_type, a.name, a.control, a.rules, replace=a.replace)
        return StepResult(step_id=0, message=f"Bedingte Formatierung für „{a.name}.{a.control}“ gesetzt ({count} Regeln)",
                          details={"rules": count})

    def _do_modify_report(self, e: AccessEngine, a: m.ModifyReport, o: ExecOptions) -> StepResult:
        e.objects.modify_report(a.name, a.changes)
        return StepResult(step_id=0, message=f"Bericht „{a.name}“ geändert")

    def _do_set_field_from_file(self, e: AccessEngine, a: m.SetFieldFromFile, o: ExecOptions) -> StepResult:
        from app.access import importer

        source = importer.resolve_source(a.file, e.path)
        text = importer.read_text_file(source)
        affected = e.tables.update_records(a.table, {a.field: text}, a.where, max_affected=o.max_affected)
        return StepResult(step_id=0, message=f"Inhalt von „{source.name}“ ({len(text)} Zeichen) in {affected} "
                                             f"Datensatz/-sätze von „{a.table}.{a.field}“ übernommen",
                          details={"affected": affected, "file": str(source), "chars": len(text)})

    def _do_map_keys(self, e: AccessEngine, a: m.MapKeys, o: ExecOptions) -> StepResult:
        from app.access.matching import match_where, plan_key_mapping
        from app.core.errors import ApprovalRequiredError

        columns = ", ".join(quote_ident(f) for f in a.source_fields)
        _, sources = e.tables.read_sql(f"SELECT DISTINCT {columns} FROM {quote_ident(a.table)}", limit=20000)
        lookup = ", ".join(quote_ident(f) for f in [a.lookup_key, *a.lookup_fields])
        _, rows = e.tables.read_sql(f"SELECT {lookup} FROM {quote_ident(a.lookup_table)}", limit=100000)
        mapping = plan_key_mapping([tuple(r) for r in sources if any(v is not None for v in r)],
                                   [(r[0], tuple(r[1:])) for r in rows])
        empty = f"{quote_ident(a.target_field)} Is Null"  # nur leere Felder befüllen – nie Vorhandenes überschreiben
        planned = {s: f"({match_where(a.source_fields, s)}) AND {empty}" for s in mapping.keys}
        total = sum(e.tables.count_records(a.table, where) for where in planned.values())
        if o.max_affected is not None and total > o.max_affected:
            error = ApprovalRequiredError(
                f"Zuordnung in „{a.table}“: {total} Datensätze wären betroffen (Grenze {o.max_affected}). "
                "Das ist mehr als die Grenze für automatische Änderungen (Einstellungen) – nichts geändert.")
            error.affected = total  # type: ignore[attr-defined]
            raise error
        affected = sum(e.tables.update_records(a.table, {a.target_field: mapping.keys[s]}, where)
                       for s, where in planned.items())
        unresolved = [[list(s), why] for s, why in mapping.unresolved.items()]
        message = (f"{len(mapping.keys)} von {len(mapping.keys) + len(unresolved)} Werten eindeutig zugeordnet, "
                   f"{affected} Datensätze in „{a.table}.{a.target_field}“ befüllt")
        if unresolved:
            message += "; nicht eindeutig: " + "; ".join(f"{' '.join(map(str, s))} ({why})" for s, why in unresolved)
        return StepResult(step_id=0, message=message, details={
            "affected": affected, "mapping": [[list(s), k] for s, k in mapping.keys.items()],
            "similar": [[list(s), k, ratio] for s, (k, ratio) in mapping.similar.items()], "unresolved": unresolved})

    def _do_attach_files(self, e: AccessEngine, a: m.AttachFiles, o: ExecOptions) -> StepResult:
        files = self.attach_plan(e, a)
        count = e.tables.attach_files(a.table, a.field, a.key_field, files, replace=a.replace)
        return StepResult(step_id=0, message=f"{count} Datei(en) in „{a.table}.{a.field}“ gespeichert",
                          details={"files": {k: p.name for k, p in files.items()}})

    @staticmethod
    def attach_plan(e: AccessEngine, a: m.AttachFiles) -> dict[str, Path]:
        """Schlüsselwert → Datei aus dem Übungsordner."""
        from app.access import importer

        images = {p.name.lower(): p for p in importer.image_files(e.path)}
        by_stem = {p.stem.lower(): p for p in images.values()}
        if a.match_by_name:
            keys = sorted({str(r[0]).strip() for r in e.queries.run_select_sql(
                f"SELECT [{a.key_field}] FROM [{a.table}]", limit=100000)[1] if r[0] is not None},
                key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k.lower()))
            files = {k: by_stem[k.lower()] for k in keys if k.lower() in by_stem}
            if a.limit:
                files = dict(list(files.items())[:a.limit])
            if not files:
                raise ObjectNotFoundError("Bilddatei passend zu", f"{a.table}.{a.key_field}")
            return files
        files = {}
        for key, name in a.files.items():
            found = images.get(name.lower()) or by_stem.get(Path(name).stem.lower())
            if found is None:
                raise ObjectNotFoundError("Bilddatei im Übungsordner", name)
            files[key] = found
        return files

    def _do_transfer_objects(self, e: AccessEngine, a: m.TransferObjects, o: ExecOptions) -> StepResult:
        path = e.transfer.transfer(a.direction, a.database, a.objects, structure_only=a.structure_only,
                                   create_new=a.create_new)
        verb = {"export": "nach", "import": "aus", "link": "verknüpft mit"}[a.direction]
        names = ", ".join(i.target for i in a.objects)
        return StepResult(step_id=0, message=f"{len(a.objects)} Objekt(e) {verb} „{path.name}“: {names}",
                          details={"path": str(path)})

    def _do_answer_object_property(self, e: AccessEngine, a: m.AnswerObjectProperty, o: ExecOptions) -> StepResult:
        answer = object_property_answer(e, a)
        return StepResult(step_id=0, message=f"{a.question} → {answer}", details={"answer": answer})

    def _do_answer_query(self, e: AccessEngine, a: m.AnswerQuery, o: ExecOptions) -> StepResult:
        columns, rows, isolated = e.queries.run_select_readonly(a.sql, limit=200)
        answer = m.answer_summary(columns, rows)
        return StepResult(step_id=0, message=f"{a.question} → {answer}",
                          details={"answer": answer, "columns": columns, "rows": rows[:20], "row_count": len(rows),
                                   "isolated": isolated})

    def _do_calculate_value(self, e: AccessEngine, a: m.CalculateValue, o: ExecOptions) -> StepResult:
        from app.agent import calc

        try:
            unknown = calc.unknown_numbers(a.expression, a.given)
            if unknown:
                raise calc.CalcError("Zahl(en) nicht in den Angaben der Aufgabe: " + ", ".join(unknown))
            answer = calc.format_value(calc.evaluate(a.expression), a.decimals)
        except calc.CalcError as exc:
            raise WidsError(f"Rechnung nicht ausführbar: {exc}") from exc
        return StepResult(step_id=0, message=f"{a.question} → {answer}",
                          details={"answer": answer, "expression": a.expression})

    def _do_modify_form(self, e: AccessEngine, a: m.ModifyForm, o: ExecOptions) -> StepResult:
        e.objects.modify_form(a.name, a.changes)
        return StepResult(step_id=0, message=f"Formular „{a.name}“ geändert")

    def _do_create_navigation_form(self, e: AccessEngine, a: m.CreateNavigationForm, o: ExecOptions) -> StepResult:
        name = e.objects.create_navigation_form(o.rename_to or a.name, a.tabs, title=a.title, replace=o.replace)
        return StepResult(step_id=0, message=f"Navigationsformular „{name}“ mit {len(a.tabs)} Registerkarten erstellt",
                          final_name=name)

    def _do_export_data(self, e: AccessEngine, a: m.ExportData, o: ExecOptions) -> StepResult:
        from pathlib import Path

        file = a.file
        if o.rename_to:
            file = Path(o.rename_to).stem + Path(a.file).suffix
        path = Path(e.path).parent / file
        count = e.tables.export_data(a.source, path, delimiter=a.delimiter, header=a.header, replace=o.replace)
        message = f"{count} Datensätze aus „{a.source}“ nach „{path.name}“ exportiert"
        if a.excel_no_wrap or a.excel_autofit:
            from app.access import excel

            excel.format_workbook(path, wrap_text=False if a.excel_no_wrap else None, autofit=a.excel_autofit)
            message += " und in Excel formatiert"
        return StepResult(step_id=0, message=message, final_name=path.name, details={"path": str(path), "count": count})

    def _do_import_odbc(self, e: AccessEngine, a: m.ImportOdbc, o: ExecOptions) -> StepResult:
        table = o.rename_to or a.table
        count = e.tables.import_odbc(a.connect, a.source_table, table, link=a.link)
        how = "verknüpft" if a.link else "importiert"
        return StepResult(step_id=0, message=f"ODBC-Tabelle „{a.source_table}“ als „{table}“ {how} ({count} Datensätze)",
                          final_name=table, details={"count": count})

    def _do_rename_object(self, e: AccessEngine, a: m.RenameObject, o: ExecOptions) -> StepResult:
        new = o.rename_to or a.new_name
        e.objects.rename(a.object_type, a.old_name, new)
        return StepResult(step_id=0, message=f"„{a.old_name}“ in „{new}“ umbenannt", final_name=new)

    def _do_delete_object(self, e: AccessEngine, a: m.DeleteObject, o: ExecOptions) -> StepResult:
        e.objects.delete(a.object_type, a.name)
        return StepResult(step_id=0, message=f"„{a.name}“ gelöscht")


def object_property_answer(e: AccessEngine, a: m.AnswerObjectProperty) -> str:
    """Antwort auf eine Objektfrage aus dem GESPEICHERTEN Zustand (Datenquelle samt Art, Felder, SQL, Schlüssel …)."""
    from app.access.sqlbuilder import classify_sql

    schema = e.schema()

    def source_kind(source: str) -> str:
        if not source:
            return "keine Datenquelle"
        if schema.table(source) is not None:
            return f"Tabelle „{schema.table(source).name}“"  # type: ignore[union-attr]
        if schema.query(source) is not None:
            return f"Abfrage „{schema.query(source).name}“"  # type: ignore[union-attr]
        if source.strip().upper().startswith("SELECT"):
            return "SQL-Anweisung (keine gespeicherte Tabelle/Abfrage)"
        return f"„{source}“ (weder Tabelle noch Abfrage in dieser Datenbank)"

    if a.object_type in ("form", "report"):
        info = e.objects.inspect_form(a.name) if a.object_type == "form" else e.objects.inspect_report(a.name)
        if a.property == "record_source":
            return info.record_source or "keine Datenquelle"
        if a.property == "record_source_type":
            return source_kind(info.record_source)
        if a.property == "fields":
            return ", ".join(sorted({x.control_source for x in info.controls
                                     if x.control_source and not x.control_source.startswith("=")})) or "keine"
        if a.property == "group_by" and a.object_type == "report":
            return ", ".join(g.expression for g in info.group_levels) or "keine Gruppierung"  # type: ignore[union-attr]
        if a.property == "sort":
            return str(info.properties.get("OrderBy") or "") or "keine gespeicherte Sortierung"
    elif a.object_type == "query":
        query = schema.query(a.name)
        if query is None:
            raise ObjectNotFoundError("Abfrage", a.name)
        if a.property == "sql":
            return query.sql.strip()
        if a.property == "query_type":
            return query.type_name or classify_sql(query.sql)
        if a.property == "fields":
            return ", ".join(query.fields) or "keine"
        if a.property == "record_count":
            return str(e.queries.run_select_readonly(f"SELECT Count(*) FROM {quote_ident(query.name)}", limit=1)[1][0][0])
    else:
        table = schema.table(a.name)
        if table is None:
            raise ObjectNotFoundError("Tabelle", a.name)
        if a.property == "primary_key":
            return ", ".join(table.primary_key) or "kein Primärschlüssel"
        if a.property == "fields":
            return ", ".join(f.name for f in table.fields)
        if a.property == "field_type":
            found = table.field(a.field or "")
            if found is None:
                raise ObjectNotFoundError("Feld", f"{a.name}.{a.field}")
            from app.access.constants import TYPE_LABELS_DE

            return TYPE_LABELS_DE.get(found.type, found.type.value)  # wie Access ihn anzeigt
        if a.property == "record_count":
            return str(e.tables.count_records(table.name))
    raise WidsError(f"Die Eigenschaft „{a.property}“ gibt es für {a.object_type} nicht.")

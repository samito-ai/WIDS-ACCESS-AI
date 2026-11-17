"""Tabellen-, Feld-, Index- und Datensatzoperationen über DAO."""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from app.access import constants as c
from app.access.comerrors import com_guard, dao_error_number, is_com_error
from app.access.constants import AccessType, access_type_from_dao
from app.access.naming import quote_ident, validate_name
from app.access.session import AccessSession
from app.access.specs import DatasheetSpec, FieldPropertyChanges, FieldSpec
from app.access.sqlbuilder import ensure_safe_fragment
from app.access.values import default_expression, from_dao, to_dao
from app.core.errors import (
    AccessError,
    ApprovalRequiredError,
    DataIntegrityError,
    ObjectExistsError,
    ObjectNotFoundError,
    UnsupportedOperationError,
)
from app.core.logging_setup import get_logger

log = get_logger(__name__)

_CUSTOM_PROPS: dict[str, int] = {
    "Description": c.DB_TEXT,
    "Caption": c.DB_TEXT,
    "Format": c.DB_TEXT,
    "InputMask": c.DB_TEXT,
    "DecimalPlaces": c.DB_BYTE,
}
#: Nachschlagefeld-Eigenschaften (DisplayControl zuerst – die übrigen gehören dazu)
_LOOKUP_PROPS: dict[str, int] = {
    "DisplayControl": c.DB_INTEGER,
    "RowSourceType": c.DB_TEXT,
    "RowSource": c.DB_MEMO,
    "BoundColumn": c.DB_INTEGER,
    "ColumnCount": c.DB_INTEGER,
    "ColumnHeads": c.DB_BOOLEAN,
    "ColumnWidths": c.DB_TEXT,
    "ListRows": c.DB_INTEGER,
    "ListWidth": c.DB_TEXT,
    "LimitToList": c.DB_BOOLEAN,
    "AllowValueListEdits": c.DB_BOOLEAN,
}


# --------------------------------------------------------------------------- Hilfsfunktionen
def collection_has(collection: Any, name: str) -> bool:
    try:
        _ = collection(name).Name
        return True
    except Exception as exc:
        if is_com_error(exc):
            return False
        raise


def is_system_table(tabledef: Any) -> bool:
    name = str(tabledef.Name)
    if name[:4] in ("MSys", "USys") or name.startswith("~"):
        return True
    try:
        return bool(int(tabledef.Attributes) & 0x80000002)
    except Exception:
        return False


def get_property(obj: Any, name: str, default: Any = "") -> Any:
    try:
        value = obj.Properties(name).Value
    except Exception:
        return default
    if isinstance(value, str):
        value = value.rstrip("\x00")  # DAO hängt an manche Texte ein NUL-Zeichen an
    return default if value is None else value


def set_property(obj: Any, name: str, value: Any, dao_type: int = c.DB_TEXT) -> None:
    """Setzt eine (ggf. benutzerdefinierte) DAO-Eigenschaft; leerer Wert entfernt sie."""
    if value is None or value == "":
        with contextlib.suppress(Exception):
            obj.Properties.Delete(name)
        return
    try:
        obj.Properties(name).Value = value
    except Exception as exc:
        if not is_com_error(exc) or dao_error_number(exc) not in (3270, 3265):
            raise
        prop = obj.CreateProperty(name, dao_type, value)
        obj.Properties.Append(prop)

def _column_order(obj: Any) -> list[tuple[int, int, str]]:
    """Spalten in der Reihenfolge des Datenblatts (ColumnOrder 0 = ungesetzt, steht hinten in Feldreihenfolge)."""
    items = [(int(get_property(f, "ColumnOrder", 0) or 0), i, str(f.Name)) for i, f in enumerate(obj.Fields)]
    return sorted(items, key=lambda item: (item[0] == 0, item[0], item[1]))


def datasheet_summary(obj: Any, *, with_formats: bool) -> str:
    """Gespeicherte Datenblatteinstellungen einer TableDef/QueryDef als Text für Prüfung und KI.

    ``with_formats``: Format/Beschriftung mit ausgeben (bei Tabellen stehen sie schon in den Feldeigenschaften).
    """
    try:
        order = _column_order(obj)
        fields = {str(f.Name): f for f in obj.Fields}
    except Exception:
        return ""
    parts = []
    if any(position for position, _, _ in order):
        parts.append("Spaltenreihenfolge " + ", ".join(name for _, _, name in order))
    frozen = int(get_property(obj, "FrozenColumns", 1) or 1) - 1
    if frozen > 0:
        parts.append("fixiert: " + ", ".join(name for _, _, name in order[:frozen]))
    hidden = [n for n, f in fields.items() if get_property(f, "ColumnHidden", False)]
    if hidden:
        parts.append("ausgeblendet: " + ", ".join(hidden))
    widths = [f"{n} {int(w) / c.TWIPS_PER_CM:.1f} cm" for n, f in fields.items()
              if int(w := get_property(f, "ColumnWidth", -1) or -1) > 0]
    if widths:
        parts.append("Spaltenbreiten: " + ", ".join(widths))
    if with_formats:
        formats = [f"{n} „{fmt}“" for n, f in fields.items() if (fmt := str(get_property(f, "Format", "")))]
        captions = [f"{n} „{cap}“" for n, f in fields.items() if (cap := str(get_property(f, "Caption", "")))]
        if formats:
            parts.append("Formate: " + ", ".join(formats))
        if captions:
            parts.append("Beschriftungen: " + ", ".join(captions))
    color = get_property(obj, "DatasheetAlternateBackColor", None)
    if color is not None and int(color) >= 0:
        bgr = int(color)
        parts.append(f"alternative Zeilenfarbe #{bgr & 0xFF:02X}{(bgr >> 8) & 0xFF:02X}{(bgr >> 16) & 0xFF:02X}")
    return "; ".join(parts)



def check_odbc_connection(connect: str, timeout_s: int = 8) -> None:
    """Prüft eine ODBC-Verbindung ohne jede Rückfrage (ADO, Prompt = Never, kurzer Timeout).

    DAO würde bei einer fehlgeschlagenen Anmeldung den Anmeldedialog des Treibers öffnen und die Automatisierung
    blockieren. Deshalb wird vorher nicht interaktiv getestet; scheitert das, wird DAO gar nicht erst aufgerufen.
    """
    import pythoncom
    import win32com.client

    from app.core.logging_setup import redact

    pythoncom.CoInitialize()
    conn = win32com.client.Dispatch("ADODB.Connection")
    try:
        conn.ConnectionTimeout = timeout_s
        conn.Provider = "MSDASQL"
        conn.Properties("Prompt").Value = 4  # adPromptNever
        conn.Open(connect)
    except Exception as exc:
        message = redact(str((getattr(exc, "excepinfo", None) and exc.excepinfo[2]) or exc))  # type: ignore[attr-defined]
        raise AccessError(f"Die ODBC-Verbindung ist nicht möglich (Server, Datenbank oder Anmeldung prüfen): {message}",
                          detail=message) from None
    finally:
        with contextlib.suppress(Exception):
            if int(conn.State):
                conn.Close()


class TableOps:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    @property
    def _db(self) -> Any:
        return self._s.db

    # ------------------------------------------------------------------ Existenzprüfungen
    def table_exists(self, name: str) -> bool:
        self._db.TableDefs.Refresh()
        return collection_has(self._db.TableDefs, name)

    def query_exists(self, name: str) -> bool:
        self._db.QueryDefs.Refresh()
        return collection_has(self._db.QueryDefs, name)

    def _require_table(self, name: str) -> Any:
        self._db.TableDefs.Refresh()
        if not collection_has(self._db.TableDefs, name):
            raise ObjectNotFoundError("Tabelle", name)
        return self._db.TableDefs(name)

    def _require_field(self, tabledef: Any, field: str) -> Any:
        if not collection_has(tabledef.Fields, field):
            raise ObjectNotFoundError("Feld", f"{tabledef.Name}.{field}")
        return tabledef.Fields(field)

    def _ensure_name_free(self, name: str) -> None:
        if self.table_exists(name):
            raise ObjectExistsError("Tabelle", name)
        if self.query_exists(name):
            raise ObjectExistsError("Abfrage", name)

    def field_type(self, table: str, field: str) -> AccessType:
        fld = self._require_field(self._require_table(table), field)
        return access_type_from_dao(int(fld.Type), int(fld.Attributes))

    # ------------------------------------------------------------------ Tabellen
    def _new_field(self, tabledef: Any, spec: FieldSpec) -> Any:
        dao_type, attributes = c.TYPE_TO_DAO[spec.type]
        if spec.type == AccessType.SHORT_TEXT:
            fld = tabledef.CreateField(spec.name, dao_type, spec.size or 255)
        else:
            fld = tabledef.CreateField(spec.name, dao_type)
        if attributes:
            fld.Attributes = int(fld.Attributes) | attributes
        if spec.type != AccessType.AUTONUMBER:
            if spec.required:
                fld.Required = True
            if spec.default_value:
                fld.DefaultValue = default_expression(spec.default_value, spec.type)
        if spec.allow_zero_length is not None and spec.type in (AccessType.SHORT_TEXT, AccessType.LONG_TEXT):
            fld.AllowZeroLength = spec.allow_zero_length
        if spec.validation_rule:
            fld.ValidationRule = spec.validation_rule
            if spec.validation_text:
                fld.ValidationText = spec.validation_text
        return fld

    def _apply_custom_props(self, fld: Any, spec: FieldSpec | FieldPropertyChanges) -> None:
        if spec.description is not None:
            set_property(fld, "Description", spec.description)
        if spec.caption is not None:
            set_property(fld, "Caption", spec.caption)
        if spec.format is not None:
            set_property(fld, "Format", spec.format)
        if spec.input_mask is not None:
            set_property(fld, "InputMask", spec.input_mask)
        if spec.decimal_places is not None:
            set_property(fld, "DecimalPlaces", spec.decimal_places, c.DB_BYTE)
        if spec.lookup is not None:
            lookup = spec.lookup
            set_property(fld, "DisplayControl", c.AC_COMBO_BOX, c.DB_INTEGER)  # zuerst – die übrigen gehören dazu
            set_property(fld, "RowSourceType", "Value List" if lookup.values else "Table/Query")
            set_property(fld, "RowSource", lookup.row_source_text())
            set_property(fld, "BoundColumn", lookup.bound_column, c.DB_INTEGER)
            set_property(fld, "ColumnCount", lookup.column_count, c.DB_INTEGER)
            set_property(fld, "ColumnWidths", lookup.column_widths or "")
            set_property(fld, "LimitToList", lookup.limit_to_list, c.DB_BOOLEAN)
            set_property(fld, "AllowValueListEdits", lookup.allow_value_list_edits, c.DB_BOOLEAN)

    def create_table(
        self,
        name: str,
        fields: list[FieldSpec],
        primary_key: list[str] | None = None,
        description: str | None = None,
    ) -> None:
        validate_name(name, "Tabelle")
        if not fields:
            raise AccessError("Eine Tabelle benötigt mindestens ein Feld.")
        lowered = [f.name.lower() for f in fields]
        duplicates = sorted({n for n in lowered if lowered.count(n) > 1})
        if duplicates:
            raise AccessError(f"Doppelte Feldnamen: {', '.join(duplicates)}")
        pk = list(primary_key) if primary_key else [f.name for f in fields if f.primary_key]
        for key in pk:
            if key.lower() not in lowered:
                raise ObjectNotFoundError("Primärschlüsselfeld", key)
        if sum(1 for f in fields if f.type == AccessType.AUTONUMBER) > 1:
            raise AccessError("Eine Tabelle darf nur ein AutoWert-Feld enthalten.")
        self._ensure_name_free(name)
        with com_guard(f"Tabelle „{name}“ erstellen"):
            tabledef = self._db.CreateTableDef(name)
            for spec in fields:
                tabledef.Fields.Append(self._new_field(tabledef, spec))
            if pk:
                index = tabledef.CreateIndex("PrimaryKey")
                index.Primary = True
                index.Unique = True
                for key in pk:
                    index.Fields.Append(index.CreateField(key))
                tabledef.Indexes.Append(index)
            pk_lower = {k.lower() for k in pk}
            for spec in fields:
                if spec.indexed in ("duplicates", "unique") and not (
                    len(pk) == 1 and spec.name.lower() in pk_lower
                ):
                    index = tabledef.CreateIndex(spec.name)
                    index.Unique = spec.indexed == "unique"
                    index.Fields.Append(index.CreateField(spec.name))
                    tabledef.Indexes.Append(index)
            self._db.TableDefs.Append(tabledef)
            self._db.TableDefs.Refresh()
            created = self._db.TableDefs(name)
            for spec in fields:
                self._apply_custom_props(created.Fields(spec.name), spec)
            if description:
                set_property(created, "Description", description)
        self._s.refresh_navigation()
        log.info("Tabelle erstellt: %s (%d Felder)", name, len(fields))

    def rename_table(self, old: str, new: str) -> None:
        validate_name(new, "Tabelle")
        tabledef = self._require_table(old)
        if old.lower() != new.lower():
            self._ensure_name_free(new)
        with com_guard(f"Tabelle „{old}“ umbenennen"):
            tabledef.Name = new
            self._db.TableDefs.Refresh()
        self._s.refresh_navigation()

    def delete_table(self, name: str) -> None:
        self._require_table(name)
        with com_guard(f"Tabelle „{name}“ löschen"):
            self._db.TableDefs.Delete(name)
            self._db.TableDefs.Refresh()
        self._s.refresh_navigation()

    def copy_table(self, source: str, new_name: str, *, with_data: bool = True) -> int:
        """Kopiert Struktur (Felder, Indizes, Eigenschaften) und optional die Daten."""
        validate_name(new_name, "Tabelle")
        src = self._require_table(source)
        self._ensure_name_free(new_name)
        copied = 0
        with com_guard(f"Tabelle „{source}“ kopieren"):
            target = self._db.CreateTableDef(new_name)
            if str(src.ValidationRule or ""):  # Tabellen-Gültigkeitsregel (z. B. [Ende]>=[Start])
                target.ValidationRule = src.ValidationRule
                target.ValidationText = src.ValidationText
            for fld in src.Fields:
                dao_type = int(fld.Type)
                if dao_type >= c.DB_ATTACHMENT:
                    raise UnsupportedOperationError(
                        f"Das Feld „{fld.Name}“ (Anlage/mehrwertig) kann nicht automatisch kopiert werden."
                    )
                new = (
                    target.CreateField(fld.Name, dao_type, int(fld.Size))
                    if dao_type == c.DB_TEXT
                    else target.CreateField(fld.Name, dao_type)
                )
                keep = int(fld.Attributes) & (c.DB_AUTOINCR_FIELD | c.DB_HYPERLINK_FIELD)
                if keep:
                    new.Attributes = int(new.Attributes) | keep
                new.Required = bool(fld.Required)
                if dao_type in (c.DB_TEXT, c.DB_MEMO):
                    new.AllowZeroLength = bool(fld.AllowZeroLength)
                if fld.DefaultValue:
                    new.DefaultValue = fld.DefaultValue
                if fld.ValidationRule:
                    new.ValidationRule = fld.ValidationRule
                    new.ValidationText = fld.ValidationText
                target.Fields.Append(new)
            for idx in src.Indexes:
                if bool(idx.Foreign):
                    continue
                new_idx = target.CreateIndex(idx.Name)
                new_idx.Primary = bool(idx.Primary)
                new_idx.Unique = bool(idx.Unique)
                new_idx.Required = bool(idx.Required)
                new_idx.IgnoreNulls = bool(idx.IgnoreNulls)
                for idx_field in idx.Fields:
                    part = new_idx.CreateField(idx_field.Name)
                    if int(idx_field.Attributes) & 1:
                        part.Attributes = 1
                    new_idx.Fields.Append(part)
                target.Indexes.Append(new_idx)
            self._db.TableDefs.Append(target)
            self._db.TableDefs.Refresh()
            created = self._db.TableDefs(new_name)
            for fld in src.Fields:
                for prop, dao_type in _CUSTOM_PROPS.items():
                    value = get_property(fld, prop, None)
                    if value not in (None, ""):
                        set_property(created.Fields(fld.Name), prop, value, dao_type)
                if get_property(fld, "DisplayControl", None) not in (None, c.AC_TEXT_BOX):
                    for prop, dao_type in _LOOKUP_PROPS.items():  # Nachschlagefeld vollständig übernehmen
                        value = get_property(fld, prop, None)
                        if value is not None:
                            set_property(created.Fields(fld.Name), prop, value, dao_type)
            if with_data:
                self._db.Execute(
                    f"INSERT INTO {quote_ident(new_name)} SELECT * FROM {quote_ident(source)}", c.DB_FAIL_ON_ERROR
                )
                copied = int(self._db.RecordsAffected)
        self._s.refresh_navigation()
        return copied

    # ------------------------------------------------------------------ Felder
    def add_field(self, table: str, spec: FieldSpec) -> None:
        tabledef = self._require_table(table)
        if collection_has(tabledef.Fields, spec.name):
            raise ObjectExistsError("Feld", f"{table}.{spec.name}")
        with com_guard(f"Feld „{spec.name}“ zu „{table}“ hinzufügen"):
            tabledef.Fields.Append(self._new_field(tabledef, spec))
            tabledef.Fields.Refresh()
            fld = tabledef.Fields(spec.name)
            self._apply_custom_props(fld, spec)
            if spec.indexed in ("duplicates", "unique"):
                self._create_index(tabledef, spec.name, [spec.name], unique=spec.indexed == "unique")
        if spec.primary_key:
            self.set_primary_key(table, [spec.name])

    def remove_field(self, table: str, field: str) -> None:
        tabledef = self._require_table(table)
        self._require_field(tabledef, field)
        with com_guard(f"Feld „{field}“ aus „{table}“ entfernen"):
            for idx in list(tabledef.Indexes):
                names = [f.Name for f in idx.Fields]
                if [n.lower() for n in names] == [field.lower()] and not bool(idx.Foreign):
                    if bool(idx.Primary):
                        raise DataIntegrityError(
                            f"„{field}“ ist der Primärschlüssel von „{table}“. Bitte zuerst den Primärschlüssel ändern."
                        )
                    tabledef.Indexes.Delete(idx.Name)
            tabledef.Fields.Delete(field)

    def rename_field(self, table: str, old: str, new: str) -> None:
        validate_name(new, "Feld")
        tabledef = self._require_table(table)
        fld = self._require_field(tabledef, old)
        if old.lower() != new.lower() and collection_has(tabledef.Fields, new):
            raise ObjectExistsError("Feld", f"{table}.{new}")
        with com_guard(f"Feld „{old}“ umbenennen"):
            fld.Name = new

    def change_field_type(self, table: str, field: str, new_type: AccessType, size: int | None = None) -> None:
        tabledef = self._require_table(table)
        fld = self._require_field(tabledef, field)
        if new_type not in c.TYPE_TO_DDL:
            raise UnsupportedOperationError(
                f"Der Datentyp „{c.TYPE_LABELS_DE.get(new_type, new_type)}“ kann nicht per Umwandlung gesetzt werden."
            )
        current = access_type_from_dao(int(fld.Type), int(fld.Attributes))
        current_size = int(fld.Size) if current == AccessType.SHORT_TEXT else None
        if current == new_type and (new_type != AccessType.SHORT_TEXT or size in (None, current_size)):
            return
        # Eigenschaften sichern – ALTER COLUMN setzt einige davon zurück
        saved = {
            "Required": bool(fld.Required),
            "DefaultValue": str(fld.DefaultValue or ""),
            "ValidationRule": str(fld.ValidationRule or "").rstrip("\x00"),
            "ValidationText": str(fld.ValidationText or "").rstrip("\x00"),
        }
        custom = {prop: get_property(fld, prop, None) for prop in ("Description", "Caption")}
        ddl = c.TYPE_TO_DDL[new_type]
        if new_type == AccessType.SHORT_TEXT:
            ddl += f"({size or current_size or 255})"
        sql = f"ALTER TABLE {quote_ident(table)} ALTER COLUMN {quote_ident(field)} {ddl}"
        with com_guard(f"Datentyp von „{table}.{field}“ ändern"):
            self._db.Execute(sql, c.DB_FAIL_ON_ERROR)
            self._db.TableDefs.Refresh()
            fld = self._db.TableDefs(table).Fields(field)
            if new_type != AccessType.AUTONUMBER:
                with contextlib.suppress(Exception):
                    fld.Required = saved["Required"]
                if saved["DefaultValue"]:
                    with contextlib.suppress(Exception):
                        fld.DefaultValue = saved["DefaultValue"]
            if saved["ValidationRule"]:
                with contextlib.suppress(Exception):
                    fld.ValidationRule = saved["ValidationRule"]
                    fld.ValidationText = saved["ValidationText"]
            for prop, value in custom.items():
                if value not in (None, ""):
                    with contextlib.suppress(Exception):
                        set_property(fld, prop, value)

    def set_field_properties(self, table: str, field: str, changes: FieldPropertyChanges) -> None:
        tabledef = self._require_table(table)
        fld = self._require_field(tabledef, field)
        access_type = access_type_from_dao(int(fld.Type), int(fld.Attributes))
        if changes.size is not None:
            if access_type != AccessType.SHORT_TEXT:
                raise UnsupportedOperationError(
                    f"Die Feldgröße kann nur bei Textfeldern in Zeichen angegeben werden („{field}“ ist "
                    f"{c.TYPE_LABELS_DE.get(access_type, access_type)}). Für Zahlen bitte den Datentyp ändern."
                )
            if int(fld.Size) != changes.size:
                self.change_field_type(table, field, AccessType.SHORT_TEXT, changes.size)
                fld = self._db.TableDefs(table).Fields(field)
        with com_guard(f"Eigenschaften von „{table}.{field}“ setzen"):
            if changes.required is not None:
                fld.Required = changes.required
            if changes.allow_zero_length is not None and access_type in (
                AccessType.SHORT_TEXT,
                AccessType.LONG_TEXT,
            ):
                fld.AllowZeroLength = changes.allow_zero_length
            if changes.default_value is not None:
                fld.DefaultValue = default_expression(changes.default_value, access_type)
            if changes.validation_rule is not None:
                fld.ValidationRule = changes.validation_rule
            if changes.validation_text is not None:
                fld.ValidationText = changes.validation_text
            self._apply_custom_props(fld, changes)
            if changes.indexed is not None:
                self._set_field_index(self._db.TableDefs(table), field, changes.indexed)

    # ------------------------------------------------------------------ Schlüssel / Indizes
    def primary_key(self, table: str) -> list[str]:
        tabledef = self._require_table(table)
        for idx in tabledef.Indexes:
            if bool(idx.Primary):
                return [f.Name for f in idx.Fields]
        return []

    def set_primary_key(self, table: str, fields: list[str]) -> None:
        tabledef = self._require_table(table)
        for name in fields:
            self._require_field(tabledef, name)
        current = self.primary_key(table)
        if [n.lower() for n in current] == [n.lower() for n in fields]:
            return
        with com_guard(f"Primärschlüssel von „{table}“ setzen"):
            for idx in list(tabledef.Indexes):
                if bool(idx.Primary):
                    tabledef.Indexes.Delete(idx.Name)
            if not fields:
                return
            # Ein gleichnamiger Nicht-Primärindex würde das Anlegen verhindern
            if collection_has(tabledef.Indexes, "PrimaryKey"):
                tabledef.Indexes.Delete("PrimaryKey")
            index = tabledef.CreateIndex("PrimaryKey")
            index.Primary = True
            index.Unique = True
            for name in fields:
                index.Fields.Append(index.CreateField(name))
            tabledef.Indexes.Append(index)

    def _create_index(
        self,
        tabledef: Any,
        name: str,
        fields: list[str],
        *,
        unique: bool = False,
        ignore_nulls: bool = False,
        descending: list[str] | None = None,
    ) -> None:
        index = tabledef.CreateIndex(name)
        index.Unique = unique
        index.IgnoreNulls = ignore_nulls
        desc = {d.lower() for d in descending or []}
        for field in fields:
            part = index.CreateField(field)
            if field.lower() in desc:
                part.Attributes = 1  # dbDescending
            index.Fields.Append(part)
        tabledef.Indexes.Append(index)

    def create_index(
        self,
        table: str,
        name: str,
        fields: list[str],
        *,
        unique: bool = False,
        ignore_nulls: bool = False,
        descending: list[str] | None = None,
    ) -> None:
        validate_name(name, "Index")
        tabledef = self._require_table(table)
        for field in fields:
            self._require_field(tabledef, field)
        if collection_has(tabledef.Indexes, name):
            raise ObjectExistsError("Index", f"{table}.{name}")
        with com_guard(f"Index „{name}“ erstellen"):
            self._create_index(
                tabledef, name, fields, unique=unique, ignore_nulls=ignore_nulls, descending=descending
            )

    def delete_index(self, table: str, name: str) -> None:
        tabledef = self._require_table(table)
        if not collection_has(tabledef.Indexes, name):
            raise ObjectNotFoundError("Index", f"{table}.{name}")
        with com_guard(f"Index „{name}“ löschen"):
            tabledef.Indexes.Delete(name)

    def _set_field_index(self, tabledef: Any, field: str, mode: str) -> None:
        single = [
            idx
            for idx in tabledef.Indexes
            if [f.Name.lower() for f in idx.Fields] == [field.lower()] and not bool(idx.Foreign)
        ]
        primary = any(bool(idx.Primary) for idx in single)
        for idx in single:
            if bool(idx.Primary):
                continue
            tabledef.Indexes.Delete(idx.Name)
        if mode == "no" or primary:
            return
        name = field if not collection_has(tabledef.Indexes, field) else f"{field}_idx"
        self._create_index(tabledef, name, [field], unique=mode == "unique")

    def set_table_sort_filter(
        self, table: str, *, order_by: str | None = None, filter_expr: str | None = None
    ) -> None:
        """Speichert Sortierung/Filter als Tabelleneigenschaft (wie in der Datenblattansicht)."""
        tabledef = self._require_table(table)
        with com_guard(f"Sortierung/Filter von „{table}“ setzen"):
            if order_by is not None:
                set_property(tabledef, "OrderBy", order_by, c.DB_TEXT)
                set_property(tabledef, "OrderByOn", bool(order_by) or None, c.DB_BOOLEAN)
            if filter_expr is not None:
                set_property(tabledef, "Filter", filter_expr, c.DB_TEXT)
                set_property(tabledef, "FilterOnLoad", bool(filter_expr) or None, c.DB_BOOLEAN)

    def set_datasheet_layout(self, name: str, spec: DatasheetSpec) -> None:
        """Datenblattansicht einer Tabelle oder gespeicherten Abfrage einrichten (gespeichert wie beim Schließen mit „Ja“)."""
        self._db.TableDefs.Refresh()
        self._db.QueryDefs.Refresh()
        if collection_has(self._db.TableDefs, name):
            obj = self._db.TableDefs(name)
        elif collection_has(self._db.QueryDefs, name):
            obj = self._db.QueryDefs(name)
        else:
            raise ObjectNotFoundError("Tabelle/Abfrage", name)
        with com_guard(f"Datenblattansicht von „{name}“ einrichten"):
            fields = {str(f.Name).lower(): f for f in obj.Fields}
            missing = sorted(n for n in spec.columns() if n.lower() not in fields)
            if missing:
                raise ObjectNotFoundError("Feld", ", ".join(missing))
            for column, fmt in spec.formats.items():
                set_property(fields[column.lower()], "Format", fmt, c.DB_TEXT)
            for column, caption in spec.captions.items():
                set_property(fields[column.lower()], "Caption", caption, c.DB_TEXT)
            for column in spec.hidden:
                set_property(fields[column.lower()], "ColumnHidden", True, c.DB_BOOLEAN)
            for column in spec.unhide:
                set_property(fields[column.lower()], "ColumnHidden", False, c.DB_BOOLEAN)
            for column, width in spec.widths_cm.items():
                set_property(fields[column.lower()], "ColumnWidth", round(width * c.TWIPS_PER_CM), c.DB_INTEGER)
            if spec.best_fit:
                for column, width in self._best_fit_widths(name, spec.best_fit).items():
                    set_property(fields[column.lower()], "ColumnWidth", width, c.DB_INTEGER)
            if spec.column_order or spec.frozen:
                current = [n for _, _, n in _column_order(obj)]
                wanted = [*spec.frozen, *(n for n in spec.column_order if n.lower() not in {f.lower() for f in spec.frozen})]
                order = [str(fields[n.lower()].Name) for n in wanted]
                order += [n for n in current if n.lower() not in {o.lower() for o in order}]
                for position, column in enumerate(order, start=1):
                    set_property(fields[column.lower()], "ColumnOrder", position, c.DB_INTEGER)
            if spec.frozen or spec.unfreeze:
                # Access zählt die Datensatzmarkierer-Spalte mit (gemessen: 2 fixierte Spalten → 3)
                set_property(obj, "FrozenColumns", len(spec.frozen) + 1, c.DB_INTEGER)
            if spec.alternate_back_color:
                rgb = spec.alternate_back_color.lstrip("#")
                set_property(obj, "DatasheetAlternateBackColor", int(rgb[4:6] + rgb[2:4] + rgb[0:2], 16), c.DB_LONG)

    def _best_fit_widths(self, name: str, columns: list[str]) -> dict[str, int]:
        """Breite (Twips) wie „optimale Breite“: längster Inhalt bzw. Spaltenkopf.

        shortcut: schätzt aus der Zeichenanzahl (Calibri 11 ≈ 0,2 cm/Zeichen) statt die Schrift zu messen; reicht für
        das Ziel „alles lesbar“, bei sehr breiten Schriften die Breite stattdessen in cm angeben.
        """
        longest = {col: len(col) for col in columns}
        rs = self._db.OpenRecordset(f"SELECT TOP 2000 {', '.join(quote_ident(col) for col in columns)} FROM {quote_ident(name)}",
                                    c.DB_OPEN_SNAPSHOT)
        try:
            while not rs.EOF:
                for col in columns:
                    value = rs.Fields(col).Value
                    if value is not None:
                        longest[col] = max(longest[col], len(str(value)))
                rs.MoveNext()
        finally:
            rs.Close()
        return {col: round(min(15.0, 0.5 + 0.2 * n) * c.TWIPS_PER_CM) for col, n in longest.items()}

    # ------------------------------------------------------------------ Datensätze
    def count_records(self, table: str, where: str | None = None) -> int:
        self._require_source(table)
        sql = f"SELECT Count(*) AS n FROM {quote_ident(table)}"
        if where:
            sql += f" WHERE {ensure_safe_fragment(where)}"
        with com_guard(f"Datensätze in „{table}“ zählen"):
            rs = self._db.OpenRecordset(sql, c.DB_OPEN_SNAPSHOT)
            try:
                return int(rs.Fields(0).Value or 0)
            finally:
                rs.Close()

    def _require_source(self, name: str) -> None:
        if not (self.table_exists(name) or self.query_exists(name)):
            raise ObjectNotFoundError("Tabelle/Abfrage", name)

    def insert_records(self, table: str, rows: list[dict[str, Any]]) -> int:
        """Fügt Datensätze ein (alle oder keiner – in einer Transaktion)."""
        tabledef = self._require_table(table)
        if not rows:
            return 0
        types: dict[str, tuple[str, AccessType]] = {}
        for fld in tabledef.Fields:
            types[str(fld.Name).lower()] = (
                str(fld.Name),
                access_type_from_dao(int(fld.Type), int(fld.Attributes)),
            )
        prepared: list[list[tuple[str, Any]]] = []
        for number, row in enumerate(rows, start=1):
            values: list[tuple[str, Any]] = []
            for key, raw in row.items():
                if key.lower() not in types:
                    raise ObjectNotFoundError("Feld", f"{table}.{key} (Datensatz {number})")
                real_name, access_type = types[key.lower()]
                if access_type == AccessType.AUTONUMBER and raw is None:
                    continue
                values.append((real_name, to_dao(raw, access_type, real_name)))
            prepared.append(values)
        with self._s.transaction(), com_guard(f"Datensätze in „{table}“ einfügen"):
            rs = self._db.OpenRecordset(table, c.DB_OPEN_DYNASET)
            try:
                for values in prepared:
                    rs.AddNew()
                    for name, value in values:
                        rs.Fields(name).Value = value
                    rs.Update()
            finally:
                rs.Close()
        return len(prepared)

    def attach_files(self, table: str, field: str, key_field: str, files: dict[str, Path], *,
                     replace: bool = False) -> int:
        """Lädt Dateien in ein Anlagefeld: ``files`` = Schlüsselwert → Datei. Alle oder keine (eine Transaktion).
        ``replace`` entfernt vorher die bisherigen Anlagen der betroffenen Datensätze (z. B. falsches Foto)."""
        tabledef = self._require_table(table)
        fields = {str(f.Name).lower(): f for f in tabledef.Fields}
        for name in (field, key_field):
            if name.lower() not in fields:
                raise ObjectNotFoundError("Feld", f"{table}.{name}")
        if int(fields[field.lower()].Type) != c.DB_ATTACHMENT:
            raise AccessError(f"„{table}.{field}“ ist kein Anlagefeld (vorher mit add_field Typ „attachment“ anlegen).")
        wanted = {str(k).strip().lower(): p for k, p in files.items()}
        done: set[str] = set()
        with self._s.transaction(), com_guard(f"Dateien in „{table}.{field}“ speichern"):
            rs = self._db.OpenRecordset(table, c.DB_OPEN_DYNASET)
            try:
                while not rs.EOF:
                    key = str(rs.Fields(key_field).Value).strip().lower()
                    if key in wanted:
                        rs.Edit()
                        child = rs.Fields(field).Value
                        if replace:
                            while not child.EOF:
                                child.Delete()
                                child.MoveNext()
                        child.AddNew()
                        child.Fields("FileData").LoadFromFile(str(wanted[key]))
                        child.Update()
                        child.Close()
                        rs.Update()
                        done.add(key)
                    rs.MoveNext()
            finally:
                rs.Close()
            missing = sorted(set(wanted) - done)
            if missing:  # Ausnahme in der Transaktion → nichts wird gespeichert
                raise ObjectNotFoundError(f"Datensatz in „{table}“ mit {key_field}", ", ".join(missing))
        return len(done)

    def attachment_names(self, table: str, field: str, key_field: str) -> dict[str, list[str]]:
        """Schlüsselwert → Dateinamen im Anlagefeld (für die Prüfung)."""
        result: dict[str, list[str]] = {}
        with com_guard(f"Anlagen in „{table}.{field}“ lesen"):
            rs = self._db.OpenRecordset(table, c.DB_OPEN_SNAPSHOT)
            try:
                while not rs.EOF:
                    child = rs.Fields(field).Value
                    names = []
                    while not child.EOF:
                        names.append(str(child.Fields("FileName").Value))
                        child.MoveNext()
                    child.Close()
                    if names:
                        result[str(rs.Fields(key_field).Value).strip()] = names
                    rs.MoveNext()
            finally:
                rs.Close()
        return result

    def import_rows(
        self,
        table: str,
        rows: list[list[Any]],
        *,
        has_header: bool = True,
        fields: list[FieldSpec] | None = None,
        primary_key: list[str] | None = None,
        add_id: str | None = None,
    ) -> tuple[int, bool]:
        """Importiert Dateizeilen in eine neue oder vorhandene Tabelle. Liefert (Anzahl, Tabelle neu angelegt).

        Neue Tabelle: Felder aus ``fields`` (in Spaltenreihenfolge) oder aus Kopfzeile und Inhalt abgeleitet;
        ``add_id`` legt zusätzlich ein AutoWert-Feld als Primärschlüssel an.
        Vorhandene Tabelle: Spalten über ``fields``-Namen, sonst über die Kopfzeile, sonst nach Position.
        """
        from app.access import importer

        header = rows[0] if has_header and rows else None
        data = rows[1:] if header is not None else rows
        if not data:
            raise AccessError("Die Datei enthält keine Datensätze zum Importieren.")
        width = max(len(r) for r in data)
        created = not self.table_exists(table)
        if created:
            specs = list(fields) if fields else importer.infer_fields(header, data)
            if len(specs) != width:
                raise AccessError(f"Die Datei hat {width} Spalten, angegeben sind {len(specs)} Felder.")
            keys = list(primary_key or [])
            all_specs = specs
            if add_id:
                all_specs = [FieldSpec(name=add_id, type=AccessType.AUTONUMBER), *specs]
                keys = keys or [add_id]
            self.create_table(table, all_specs, keys or None)
            columns = [s.name for s in specs]
        else:
            tabledef = self._require_table(table)
            existing = [str(f.Name) for f in tabledef.Fields]
            lower = {n.lower(): n for n in existing}
            if fields:
                columns = [f.name for f in fields]
            elif header is not None and all(importer.clean_field_name(h, i).lower() in lower for i, h in enumerate(header)):
                columns = [lower[importer.clean_field_name(h, i).lower()] for i, h in enumerate(header)]
            elif header is not None:
                # Überschriften vorhanden, aber nicht alle passen: nach Position zu raten würde Daten in falsche Felder
                # schreiben (z. B. Familienname → Vorname) – die Zielfelder müssen ausdrücklich angegeben werden
                unknown = [str(h) for i, h in enumerate(header) if importer.clean_field_name(h, i).lower() not in lower]
                raise AccessError(f"Die Spalten {', '.join(unknown)} der Datei gibt es in „{table}“ nicht "
                                  f"({', '.join(existing)}). Bitte die Zielfelder in Dateireihenfolge angeben (fields).")
            else:
                editable = [str(f.Name) for f in tabledef.Fields
                            if access_type_from_dao(int(f.Type), int(f.Attributes)) != AccessType.AUTONUMBER]
                columns = existing if len(existing) == width else editable
            if len(columns) != width:
                raise AccessError(f"Die Datei hat {width} Spalten, „{table}“ passt nicht dazu ({', '.join(existing)}). "
                                  "Bitte die Zielfelder in Dateireihenfolge angeben.")
            for name in columns:
                if name.lower() not in lower:
                    raise ObjectNotFoundError("Feld", f"{table}.{name}")
            if primary_key:
                self.set_primary_key(table, primary_key)
        types = {n.lower(): self.field_type(table, n) for n in columns}
        records = []
        for row in data:
            record: dict[str, Any] = {}
            for name, value in zip(columns, row, strict=False):
                if value in ("", None):
                    continue
                if types[name.lower()] == AccessType.DATETIME:
                    value = importer.excel_serial_to_datetime(value)
                record[name] = value
            records.append(record)
        try:
            return self.insert_records(table, records), created
        except Exception:
            if created:  # alles oder nichts: keine leere Tabelle zurücklassen
                with contextlib.suppress(Exception):
                    self.delete_table(table)
            raise

    # ------------------------------------------------------------------ Export
    def export_data(self, source: str, path: Path, *, delimiter: str = ";", header: bool = True,
                    replace: bool = False) -> int:
        """Exportiert Tabelle/Abfrage nach .xlsx (über Access), .csv oder .txt. Vorhandene Dateien werden nur
        mit ``replace`` überschrieben. Liefert die Anzahl exportierter Datensätze."""
        self._require_source(source)
        suffix = path.suffix.lower()
        if suffix not in (".xlsx", ".csv", ".txt"):
            raise AccessError("Export ist nach .xlsx, .csv oder .txt möglich.")
        if path.exists() and not replace:
            raise ObjectExistsError("Datei", path.name)
        if path.exists():  # „Überschreiben“ gewählt: die bisherige Datei vorher sichern
            from app.access.transfer import TransferOps

            log.info("Bisherige Exportdatei gesichert: %s", TransferOps._backup(path))
        count = self.count_records(source)
        if suffix == ".xlsx":
            app = self._s.application()
            with com_guard(f"„{source}“ nach Excel exportieren"):
                if path.exists():
                    path.unlink()
                app.DoCmd.TransferSpreadsheet(1, 10, source, str(path), header)  # acExport, acSpreadsheetTypeExcel12Xml
            return count
        with com_guard(f"„{source}“ exportieren"):
            rs = self._db.OpenRecordset(source, c.DB_OPEN_SNAPSHOT)
            try:
                names = [str(f.Name) for f in rs.Fields]
                rows = []
                while not rs.EOF:
                    rows.append(["" if (v := from_dao(rs.Fields(n).Value)) is None else v for n in names])
                    rs.MoveNext()
            finally:
                rs.Close()
        import csv

        temp = path.with_suffix(path.suffix + ".tmp")  # erst vollständig schreiben, dann ersetzen
        with temp.open("w", newline="", encoding="cp1252", errors="replace") as handle:
            writer = csv.writer(handle, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
            if header:
                writer.writerow(names)
            writer.writerows(rows)
        temp.replace(path)
        return len(rows)

    # ------------------------------------------------------------------ ODBC (z. B. SQL Server)
    def import_odbc(self, connect: str, source_table: str, target: str, *, link: bool = False) -> int:
        """Importiert (``SELECT … INTO``) oder verknüpft eine Tabelle einer ODBC-Datenquelle über eine vorhandene
        Verbindung (DSN oder Verbindungszeichenfolge). Ein Kennwort wird nie gespeichert oder protokolliert."""
        from app.core.logging_setup import register_secret

        validate_name(target, "Tabelle")
        self._ensure_name_free(target)
        connect = connect.strip()
        if not connect.upper().startswith("ODBC;"):
            connect = "ODBC;" + connect
        for match in re.finditer(r"(?i)\b(?:PWD|Password)=([^;]*)", connect):
            register_secret(match.group(1))
        check_odbc_connection(connect[5:])  # nie einen Anmeldedialog öffnen: erst ohne Rückfrage testen
        if link:
            with com_guard(f"ODBC-Tabelle „{source_table}“ verknüpfen"):
                tabledef = self._db.CreateTableDef(target)
                tabledef.Connect = connect  # ohne dbAttachSavePWD: Kennwort wird nicht in der Datenbank gespeichert
                tabledef.SourceTableName = source_table
                self._db.TableDefs.Append(tabledef)
                self._db.TableDefs.Refresh()
        else:
            source = "[" + connect.replace("]", "]]") + "].[" + source_table.replace("]", "]]") + "]"
            with self._s.transaction(), com_guard(f"ODBC-Tabelle „{source_table}“ importieren"):
                self._db.Execute(f"SELECT * INTO {quote_ident(target)} FROM {source}", c.DB_FAIL_ON_ERROR)
        return self.count_records(target)

    def _guarded_execute(self, sql: str, context: str, *, max_affected: int | None) -> int:
        """Führt eine ändernde Anweisung in einer Transaktion aus.

        Überschreitet die Zahl betroffener Datensätze ``max_affected``, wird zurückgerollt und
        ``ApprovalRequiredError`` ausgelöst – so ist die Anzahl exakt bekannt, ohne dass etwas
        dauerhaft verändert wurde.
        """
        ws = self._s.workspace()
        with com_guard(context):
            ws.BeginTrans()
            try:
                self._db.Execute(sql, c.DB_FAIL_ON_ERROR)
                affected = int(self._db.RecordsAffected)
            except Exception:
                ws.Rollback()
                raise
            if max_affected is not None and affected > max_affected:
                ws.Rollback()
                error = ApprovalRequiredError(
                    f"{context}: {affected} Datensätze wären betroffen (Grenze {max_affected}). "
                    "Das ist mehr als die Grenze für automatische Änderungen (Einstellungen) – nichts geändert."
                )
                error.affected = affected  # type: ignore[attr-defined]
                raise error
            ws.CommitTrans()
        return affected

    def update_records(
        self, table: str, values: dict[str, Any], where: str | None = None, *, max_affected: int | None = None
    ) -> int:
        tabledef = self._require_table(table)
        if not values:
            raise AccessError("Es wurden keine zu ändernden Werte angegeben.")
        from app.access.naming import sql_literal

        assignments: list[str] = []
        for key, raw in values.items():
            fld = self._require_field(tabledef, key)
            access_type = access_type_from_dao(int(fld.Type), int(fld.Attributes))
            if isinstance(raw, str) and raw.startswith("="):
                assignments.append(f"{quote_ident(str(fld.Name))} = {ensure_safe_fragment(raw[1:], 'Zuweisung')}")
            else:
                assignments.append(
                    f"{quote_ident(str(fld.Name))} = {sql_literal(to_dao(raw, access_type, str(fld.Name)))}"
                )
        sql = f"UPDATE {quote_ident(table)} SET {', '.join(assignments)}"
        if where:
            sql += f" WHERE {ensure_safe_fragment(where)}"
        return self._guarded_execute(sql, f"Datensätze in „{table}“ ändern", max_affected=max_affected)

    def delete_records(self, table: str, where: str | None = None, *, max_affected: int | None = None) -> int:
        self._require_table(table)
        sql = f"DELETE FROM {quote_ident(table)}"
        if where:
            sql += f" WHERE {ensure_safe_fragment(where)}"
        return self._guarded_execute(sql, f"Datensätze in „{table}“ löschen", max_affected=max_affected)

    def read_rows(self, source: str, *, limit: int = 200, order_by: str | None = None) -> tuple[list[str], list[list[Any]]]:
        """Liest Zeilen einer Tabelle oder Abfrage (für Vorschau und Verifikation)."""
        self._require_source(source)
        sql = f"SELECT * FROM {quote_ident(source)}"
        if order_by:
            sql += f" ORDER BY {ensure_safe_fragment(order_by, 'Sortierung')}"
        return self.read_sql(sql, limit=limit)

    def read_sql(self, sql: str, *, limit: int = 200) -> tuple[list[str], list[list[Any]]]:
        with com_guard("Daten lesen"):
            rs = self._db.OpenRecordset(sql, c.DB_OPEN_SNAPSHOT)
            try:
                return recordset_to_rows(rs, limit)
            finally:
                rs.Close()


def recordset_to_rows(rs: Any, limit: int) -> tuple[list[str], list[list[Any]]]:
    columns = [str(f.Name) for f in rs.Fields]
    rows: list[list[Any]] = []
    if not (bool(rs.EOF) and bool(rs.BOF)):
        data = rs.GetRows(limit)
        if data:
            for row_index in range(len(data[0])):
                rows.append([from_dao(column[row_index]) for column in data])
    return columns, rows

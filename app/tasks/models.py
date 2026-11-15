"""Strukturiertes Aufgaben- und Planmodell.

Die KI liefert ausschließlich Daten in diesem Schema. Jede Aktion ist ein streng
validiertes Pydantic-Modell mit festem ``action``-Diskriminator – es gibt keine Aktion, die
freien Programmcode oder Shell-Befehle ausführt. Was nicht in dieser Liste steht, kann der
Agent nicht tun.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.access.constants import AccessType, normalize_type
from app.access.naming import validate_name
from app.access.specs import (
    DatasheetSpec,
    FieldPropertyChanges,
    FieldSpec,
    FormatRuleSpec,
    FormChangeSpec,
    FormSpec,
    JoinType,
    NavigationTabSpec,
    QuerySpec,
    RelationFieldPair,
    ReportChangeSpec,
    ReportSpec,
    StrictModel,
)
from app.access.transfer import TransferItem


class TaskType(StrEnum):
    TABLE = "table"
    DATATYPE = "datatype"
    FIELD_DEFINITION = "field_definition"
    PRIMARY_KEY = "primary_key"
    RELATIONSHIP = "relationship"
    SELECT_QUERY = "select_query"
    PARAMETER_QUERY = "parameter_query"
    AGGREGATE_QUERY = "aggregate_query"
    CALCULATED_FIELD = "calculated_field"
    CRITERIA = "criteria"
    SORTING = "sorting"
    ACTION_QUERY = "action_query"
    CROSSTAB_QUERY = "crosstab_query"
    FORM = "form"
    REPORT = "report"
    DATA_EDITING = "data_editing"
    THEORY = "theory"
    MULTIPLE_CHOICE = "multiple_choice"
    MIXED = "mixed"
    UNKNOWN = "unknown"


TASK_TYPE_LABELS: dict[TaskType, str] = {
    TaskType.TABLE: "Tabelle",
    TaskType.DATATYPE: "Datentypen",
    TaskType.FIELD_DEFINITION: "Felddefinition",
    TaskType.PRIMARY_KEY: "Primärschlüssel",
    TaskType.RELATIONSHIP: "Beziehungen",
    TaskType.SELECT_QUERY: "Auswahlabfrage",
    TaskType.PARAMETER_QUERY: "Parameterabfrage",
    TaskType.AGGREGATE_QUERY: "Aggregatabfrage",
    TaskType.CALCULATED_FIELD: "Berechnetes Feld",
    TaskType.CRITERIA: "Kriterien",
    TaskType.SORTING: "Sortierung",
    TaskType.ACTION_QUERY: "Aktionsabfrage",
    TaskType.CROSSTAB_QUERY: "Kreuztabellenabfrage",
    TaskType.FORM: "Formular",
    TaskType.REPORT: "Bericht",
    TaskType.DATA_EDITING: "Datenbearbeitung",
    TaskType.THEORY: "Theoriefrage",
    TaskType.MULTIPLE_CHOICE: "Multiple Choice",
    TaskType.MIXED: "Gemischte Aufgabe",
    TaskType.UNKNOWN: "Unbekannt",
}

Risk = Literal["safe", "modify", "destructive"]


class _Action(StrictModel):
    """Basisklasse aller ausführbaren Aktionen."""

    #: ``safe`` = legt nur Neues an, ``modify`` = ändert Bestehendes, ``destructive`` = kann Daten/Objekte
    #: vernichten. Bewusst eine Klassenkonstante und kein Datenfeld: Die Risikostufe darf niemals aus der
    #: KI-Antwort stammen.
    RISK: ClassVar[Risk] = "safe"

    @property
    def risk(self) -> Risk:
        return self.RISK

    def target(self) -> str:
        """Kurzbezeichnung des betroffenen Objekts (für Anzeige und Protokoll)."""
        return ""


def _name(value: str, kind: str) -> str:
    return validate_name(value, kind)


# --------------------------------------------------------------------------- Tabellen
class CreateTable(_Action):
    action: Literal["create_table"] = "create_table"
    name: str
    fields: list[FieldSpec] = Field(min_length=1)
    primary_key: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Tabelle")

    def target(self) -> str:
        return self.name


class RenameTable(_Action):
    action: Literal["rename_table"] = "rename_table"
    RISK: ClassVar[Risk] = "modify"
    old_name: str
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Tabelle")

    def target(self) -> str:
        return self.old_name


class CopyTable(_Action):
    action: Literal["copy_table"] = "copy_table"
    source: str
    new_name: str
    with_data: bool = True

    @field_validator("new_name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Tabelle")

    def target(self) -> str:
        return self.new_name


class DeleteTable(_Action):
    action: Literal["delete_table"] = "delete_table"
    RISK: ClassVar[Risk] = "destructive"
    name: str

    def target(self) -> str:
        return self.name


class AddField(_Action):
    action: Literal["add_field"] = "add_field"
    RISK: ClassVar[Risk] = "modify"
    table: str
    field: FieldSpec

    def target(self) -> str:
        return f"{self.table}.{self.field.name}"


class RemoveField(_Action):
    action: Literal["remove_field"] = "remove_field"
    RISK: ClassVar[Risk] = "destructive"
    table: str
    field: str

    def target(self) -> str:
        return f"{self.table}.{self.field}"


class RenameField(_Action):
    action: Literal["rename_field"] = "rename_field"
    RISK: ClassVar[Risk] = "modify"
    table: str
    old_name: str
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Feld")

    def target(self) -> str:
        return f"{self.table}.{self.old_name}"


class ChangeFieldType(_Action):
    action: Literal["change_field_type"] = "change_field_type"
    #: Typumwandlungen können Daten abschneiden oder verwerfen
    RISK: ClassVar[Risk] = "destructive"
    table: str
    field: str
    new_type: AccessType
    size: int | None = Field(default=None, ge=1, le=255)

    @field_validator("new_type", mode="before")
    @classmethod
    def _t(cls, v: Any) -> Any:
        return normalize_type(v) if isinstance(v, str) else v

    def target(self) -> str:
        return f"{self.table}.{self.field}"


class SetFieldProperties(_Action):
    action: Literal["set_field_properties"] = "set_field_properties"
    RISK: ClassVar[Risk] = "modify"
    table: str
    field: str
    properties: FieldPropertyChanges

    def target(self) -> str:
        return f"{self.table}.{self.field}"


class SetPrimaryKey(_Action):
    action: Literal["set_primary_key"] = "set_primary_key"
    RISK: ClassVar[Risk] = "modify"
    table: str
    fields: list[str] = Field(min_length=1)

    def target(self) -> str:
        return self.table


class CreateIndex(_Action):
    action: Literal["create_index"] = "create_index"
    RISK: ClassVar[Risk] = "modify"
    table: str
    name: str
    fields: list[str] = Field(min_length=1)
    unique: bool = False

    def target(self) -> str:
        return f"{self.table}.{self.name}"


class DeleteIndex(_Action):
    action: Literal["delete_index"] = "delete_index"
    RISK: ClassVar[Risk] = "destructive"
    table: str
    name: str

    def target(self) -> str:
        return f"{self.table}.{self.name}"


class SetTableSortFilter(_Action):
    action: Literal["set_table_sort_filter"] = "set_table_sort_filter"
    RISK: ClassVar[Risk] = "modify"
    table: str
    #: z. B. ``[Nachname] DESC`` – leer entfernt die Sortierung
    order_by: str | None = None
    #: z. B. ``[Ort]="Imst"`` – leer entfernt den Filter
    filter: str | None = None

    def target(self) -> str:
        return self.table


class SetDatasheetLayout(_Action):
    """Datenblattansicht einer Tabelle/Abfrage: Spalten anordnen, ausblenden, fixieren, Breite, Format, Zeilenfarbe."""

    action: Literal["set_datasheet_layout"] = "set_datasheet_layout"
    RISK: ClassVar[Risk] = "modify"
    object: str
    layout: DatasheetSpec

    def target(self) -> str:
        return self.object


# --------------------------------------------------------------------------- Daten
class InsertRecords(_Action):
    action: Literal["insert_records"] = "insert_records"
    RISK: ClassVar[Risk] = "modify"
    table: str
    records: list[dict[str, Any]] = Field(min_length=1)

    def target(self) -> str:
        return self.table


class ImportData(_Action):
    """Import aus einer Excel- oder Textdatei der Übung in eine neue oder vorhandene Tabelle."""

    action: Literal["import_data"] = "import_data"
    #: Fügt nur hinzu (neue Tabelle oder zusätzliche Datensätze) – nichts wird überschrieben
    RISK: ClassVar[Risk] = "modify"
    #: Dateiname wie in der Aufgabe („Filmcrew1.txt“ oder „Filmcrew1“); gesucht wird im te.comp-Übungsordner
    file: str
    table: str
    has_header: bool = True
    #: Trennzeichen bei Textdateien (Standard: automatisch erkannt); "\\t" = Tabulator
    delimiter: str | None = Field(default=None, max_length=1)
    #: Spaltenbreiten in Zeichen bei Textdateien mit fester Breite
    column_widths: list[int] = Field(default_factory=list)
    sheet: str | None = None
    #: Zielfelder in Spaltenreihenfolge (neue Tabelle: Name und Typ; vorhandene: nur Name)
    fields: list[FieldSpec] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    #: Zusätzliches AutoWert-Feld als Primärschlüssel („Access-Primärschlüssel hinzufügen“)
    add_id: str | None = None

    @field_validator("table")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Tabelle")

    @field_validator("delimiter", mode="before")
    @classmethod
    def _tab(cls, v: Any) -> Any:
        return {"\\t": "\t", "tab": "\t", "tabulator": "\t"}.get(str(v).lower(), v) if isinstance(v, str) else v

    @field_validator("column_widths")
    @classmethod
    def _widths(cls, v: list[int]) -> list[int]:
        if any(w < 1 or w > 1000 for w in v):
            raise ValueError("Spaltenbreiten müssen zwischen 1 und 1000 Zeichen liegen.")
        return v

    def target(self) -> str:
        return self.table


class ExportData(_Action):
    """Export einer Tabelle/Abfrage als Datei in den Ordner der Datenbank (vorhandene Datei = Konflikt)."""

    action: Literal["export_data"] = "export_data"
    source: str
    #: Nur Dateiname mit Endung .xlsx, .csv oder .txt – kein Pfad
    file: str = Field(pattern=r"(?i)^[^\\/:*?\"<>|]+\.(xlsx|csv|txt)$")
    delimiter: str = Field(default=";", max_length=1)
    header: bool = True
    #: nur .xlsx, danach in Excel: Textumbruch aller Daten ausschalten / Spaltenbreiten optimal anpassen
    excel_no_wrap: bool = False
    excel_autofit: bool = False

    @model_validator(mode="after")
    def _excel_only(self) -> ExportData:
        if (self.excel_no_wrap or self.excel_autofit) and not self.file.lower().endswith(".xlsx"):
            raise ValueError("excel_no_wrap/excel_autofit gibt es nur beim Export nach .xlsx.")
        return self

    @field_validator("delimiter", mode="before")
    @classmethod
    def _tab(cls, v: Any) -> Any:
        return {"\\t": "\t", "tab": "\t", "tabulator": "\t"}.get(str(v).lower(), v) if isinstance(v, str) else v

    def target(self) -> str:
        return self.file


class ImportOdbc(_Action):
    """Tabelle einer ODBC-Datenquelle (z. B. SQL Server) importieren oder verknüpfen – über eine vorhandene
    Verbindung (DSN oder Verbindungszeichenfolge). Kennwörter werden weder gespeichert noch protokolliert."""

    action: Literal["import_odbc"] = "import_odbc"
    RISK: ClassVar[Risk] = "modify"
    connect: str = Field(min_length=4)
    source_table: str
    table: str
    link: bool = False

    @field_validator("table")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Tabelle")

    @field_validator("connect")
    @classmethod
    def _secret(cls, v: str) -> str:
        import re

        from app.core.logging_setup import register_secret

        for match in re.finditer(r"(?i)\b(?:PWD|Password)=([^;]*)", v):
            register_secret(match.group(1))  # ab jetzt in jedem Log geschwärzt
        return v

    def target(self) -> str:
        return self.table


class UpdateRecords(_Action):
    action: Literal["update_records"] = "update_records"
    RISK: ClassVar[Risk] = "modify"
    table: str
    #: Feld -> neuer Wert; ein Wert mit führendem ``=`` ist ein Ausdruck, z. B. ``=[Preis]*1.1``
    values: dict[str, Any] = Field(min_length=1)
    where: str | None = None

    @property
    def risk(self) -> Risk:
        # ohne Bedingung werden alle Datensätze überschrieben
        return "modify" if self.where and self.where.strip() else "destructive"

    def target(self) -> str:
        return self.table


class DeleteRecords(_Action):
    action: Literal["delete_records"] = "delete_records"
    RISK: ClassVar[Risk] = "destructive"
    table: str
    where: str | None = None

    def target(self) -> str:
        return self.table


# --------------------------------------------------------------------------- Beziehungen
class CreateRelationship(_Action):
    action: Literal["create_relationship"] = "create_relationship"
    RISK: ClassVar[Risk] = "modify"
    #: Primärtabelle (1-Seite)
    table: str
    #: Detailtabelle (n-Seite)
    foreign_table: str
    fields: list[RelationFieldPair] = Field(min_length=1)
    enforce_integrity: bool = True
    cascade_update: bool = False
    cascade_delete: bool = False
    join_type: JoinType = "inner"
    name: str | None = None

    def target(self) -> str:
        return f"{self.table} → {self.foreign_table}"


class DeleteRelationship(_Action):
    action: Literal["delete_relationship"] = "delete_relationship"
    RISK: ClassVar[Risk] = "destructive"
    name: str | None = None
    table: str | None = None
    foreign_table: str | None = None

    @model_validator(mode="after")
    def _check(self) -> DeleteRelationship:
        if not self.name and not (self.table and self.foreign_table):
            raise ValueError("Entweder 'name' oder 'table' und 'foreign_table' angeben.")
        return self

    def target(self) -> str:
        return self.name or f"{self.table} → {self.foreign_table}"


# --------------------------------------------------------------------------- Abfragen
class CreateQuery(_Action):
    action: Literal["create_query"] = "create_query"
    name: str
    #: Strukturierte Definition (bevorzugt) …
    spec: QuerySpec | None = None
    #: … oder fertiges Access-SQL
    sql: str | None = None
    description: str | None = None
    #: die Aufgabe verlangt ausdrücklich, eine VORHANDENE Abfrage zu ändern/erweitern → an Ort und Stelle ersetzen
    modify_existing: bool = False

    @property
    def risk(self) -> Risk:
        return "modify" if self.modify_existing else self.RISK

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Abfrage")

    @model_validator(mode="after")
    def _check(self) -> CreateQuery:
        if (self.spec is None) == (self.sql is None):
            raise ValueError("Genau eines von 'spec' oder 'sql' muss angegeben werden.")
        return self

    def target(self) -> str:
        return self.name


class DeleteQuery(_Action):
    action: Literal["delete_query"] = "delete_query"
    RISK: ClassVar[Risk] = "destructive"
    name: str

    def target(self) -> str:
        return self.name


class RenameQuery(_Action):
    action: Literal["rename_query"] = "rename_query"
    RISK: ClassVar[Risk] = "modify"
    old_name: str
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Abfrage")

    def target(self) -> str:
        return self.old_name


class RunActionQuery(_Action):
    action: Literal["run_action_query"] = "run_action_query"
    RISK: ClassVar[Risk] = "destructive"
    #: Name einer gespeicherten Aktionsabfrage …
    name: str | None = None
    #: … oder eine einzelne SQL-Anweisung
    sql: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> RunActionQuery:
        if (self.name is None) == (self.sql is None):
            raise ValueError("Genau eines von 'name' oder 'sql' muss angegeben werden.")
        return self

    def target(self) -> str:
        return self.name or "SQL-Anweisung"


# --------------------------------------------------------------------------- Formulare / Berichte
class CreateForm(_Action):
    action: Literal["create_form"] = "create_form"
    form: FormSpec

    def target(self) -> str:
        return self.form.name


class CreateReport(_Action):
    action: Literal["create_report"] = "create_report"
    report: ReportSpec

    def target(self) -> str:
        return self.report.name


class AttachFiles(_Action):
    """Bilder/Dateien des Übungsordners in ein Anlagefeld speichern (te.comp 0207: Fotos je Club/Mitglied)."""

    action: Literal["attach_files"] = "attach_files"
    RISK: ClassVar[Risk] = "modify"
    table: str
    #: Anlagefeld (Typ „attachment“; fehlt es, vorher add_field)
    field: str
    #: Feld, dessen Wert die Datei bestimmt (z. B. Golfplatz oder Mitgliedsnummer)
    key_field: str
    #: Schlüsselwert → Dateiname im Übungsordner …
    files: dict[str, str] = Field(default_factory=dict)
    #: … oder automatisch: Dateiname ohne Endung = Schlüsselwert („Fotos sind mit den Club-Namen benannt“)
    match_by_name: bool = False
    #: höchstens so viele Datensätze (z. B. „die ersten zehn Mitglieder“), in Reihenfolge des Schlüssels
    limit: int | None = Field(default=None, ge=1)
    #: bisherige Anlagen dieser Datensätze ersetzen (z. B. „falsches Foto korrigieren“)
    replace: bool = False

    @model_validator(mode="after")
    def _check(self) -> AttachFiles:
        if bool(self.files) == self.match_by_name:
            raise ValueError("Entweder 'files' angeben oder 'match_by_name': true.")
        return self

    def target(self) -> str:
        return f"{self.table}.{self.field}"


class TransferObjects(_Action):
    """Objekte mit einer anderen Access-Datenbank der Übung austauschen: exportieren (auch in eine NEUE Datenbank,
    z. B. Sicherungsdatenbank), importieren oder Tabellen verknüpfen."""

    action: Literal["transfer_objects"] = "transfer_objects"
    RISK: ClassVar[Risk] = "modify"
    direction: Literal["export", "import", "link"]
    #: Dateiname der anderen Datenbank im Übungsordner („Sicherung Filmdaten“, „Filmdaten.accdb“)
    database: str = Field(min_length=1)
    objects: list[TransferItem] = Field(min_length=1, max_length=60)
    #: nur Tabellenstruktur ohne Daten
    structure_only: bool = False
    #: Exportziel als neue, leere Datenbank anlegen
    create_new: bool = False

    def target(self) -> str:
        return self.database


class AnswerQuery(_Action):
    """Datenfrage („Wie viele …?“, „Welche ID hat …?“) mit einer NUR LESENDEN Abfrage beantworten – ändert nichts."""

    action: Literal["answer_query"] = "answer_query"
    RISK: ClassVar[Risk] = "safe"
    question: str = Field(min_length=1, max_length=500)
    #: genau eine SELECT-Anweisung in Access-SQL
    sql: str = Field(min_length=1)

    def target(self) -> str:
        return "Frage"


def answer_summary(columns: list[str], rows: list[list[Any]], *, limit: int = 8) -> str:
    """Kurzfassung eines Abfrageergebnisses als Antwort (ein Wert, sonst die ersten Zeilen)."""
    if not rows:
        return "keine Datensätze"
    if len(rows) == 1 and len(columns) == 1:
        return str(rows[0][0])
    shown = "; ".join(", ".join("" if v is None else str(v) for v in row) for row in rows[:limit])
    return f"{len(rows)} Datensatz/-sätze: {shown}" + (" …" if len(rows) > limit else "")


class ModifyReport(_Action):
    """Vorhandenen Bericht bearbeiten: Filter/Sortierung, Gruppen (neue Seite), Detailbereich, Felder ergänzen/ändern."""

    action: Literal["modify_report"] = "modify_report"
    RISK: ClassVar[Risk] = "modify"
    name: str
    changes: ReportChangeSpec

    def target(self) -> str:
        return self.name


class ModifyForm(_Action):
    """Vorhandenes Formular bearbeiten: Eigenschaften (PopUp, geteiltes Formular), Steuerelemente ändern/ergänzen/entfernen."""

    action: Literal["modify_form"] = "modify_form"
    RISK: ClassVar[Risk] = "modify"
    name: str
    changes: FormChangeSpec

    def target(self) -> str:
        return self.name


class SetConditionalFormat(_Action):
    action: Literal["set_conditional_format"] = "set_conditional_format"
    RISK: ClassVar[Risk] = "modify"
    object_type: Literal["form", "report"]
    name: str
    #: Name des Text-/Kombinationsfelds (bei automatisch erstellten Objekten = Feldname)
    control: str
    rules: list[FormatRuleSpec] = Field(min_length=1, max_length=50)
    #: vorhandene Regeln dieses Steuerelements ersetzen statt ergänzen
    replace: bool = False

    def target(self) -> str:
        return f"{self.name}.{self.control}"


class CreateNavigationForm(_Action):
    action: Literal["create_navigation_form"] = "create_navigation_form"
    name: str
    tabs: list[NavigationTabSpec] = Field(min_length=1, max_length=20)
    title: str | None = None

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Formular")

    def target(self) -> str:
        return self.name


class RenameObject(_Action):
    action: Literal["rename_object"] = "rename_object"
    RISK: ClassVar[Risk] = "modify"
    object_type: Literal["form", "report"]
    old_name: str
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _name(v, "Objekt")

    def target(self) -> str:
        return self.old_name


class DeleteObject(_Action):
    action: Literal["delete_object"] = "delete_object"
    RISK: ClassVar[Risk] = "destructive"
    object_type: Literal["form", "report"]
    name: str

    def target(self) -> str:
        return self.name


Action = Annotated[
    CreateTable
    | RenameTable
    | CopyTable
    | DeleteTable
    | AddField
    | RemoveField
    | RenameField
    | ChangeFieldType
    | SetFieldProperties
    | SetPrimaryKey
    | CreateIndex
    | DeleteIndex
    | SetTableSortFilter
    | SetDatasheetLayout
    | InsertRecords
    | ImportData
    | ExportData
    | ImportOdbc
    | UpdateRecords
    | DeleteRecords
    | CreateRelationship
    | DeleteRelationship
    | CreateQuery
    | DeleteQuery
    | RenameQuery
    | RunActionQuery
    | CreateForm
    | CreateReport
    | SetConditionalFormat
    | ModifyReport
    | ModifyForm
    | AnswerQuery
    | TransferObjects
    | AttachFiles
    | CreateNavigationForm
    | RenameObject
    | DeleteObject,
    Field(discriminator="action"),
]

ACTION_LABELS: dict[str, str] = {
    "create_table": "Tabelle erstellen",
    "rename_table": "Tabelle umbenennen",
    "copy_table": "Tabelle kopieren",
    "delete_table": "Tabelle löschen",
    "add_field": "Feld hinzufügen",
    "remove_field": "Feld entfernen",
    "rename_field": "Feld umbenennen",
    "change_field_type": "Datentyp ändern",
    "set_field_properties": "Feldeigenschaften setzen",
    "set_primary_key": "Primärschlüssel setzen",
    "create_index": "Index erstellen",
    "delete_index": "Index löschen",
    "set_table_sort_filter": "Sortierung/Filter setzen",
    "set_datasheet_layout": "Datenblattansicht einrichten",
    "insert_records": "Datensätze einfügen",
    "import_data": "Daten importieren",
    "export_data": "Daten exportieren",
    "import_odbc": "ODBC-/SQL-Server-Daten übernehmen",
    "set_conditional_format": "Bedingte Formatierung",
    "modify_report": "Bericht bearbeiten",
    "modify_form": "Formular bearbeiten",
    "answer_query": "Datenfrage beantworten",
    "transfer_objects": "Objekte mit anderer Datenbank austauschen",
    "attach_files": "Dateien in Anlagefeld speichern",
    "create_navigation_form": "Navigationsformular erstellen",
    "update_records": "Datensätze ändern",
    "delete_records": "Datensätze löschen",
    "create_relationship": "Beziehung erstellen",
    "delete_relationship": "Beziehung löschen",
    "create_query": "Abfrage erstellen",
    "delete_query": "Abfrage löschen",
    "rename_query": "Abfrage umbenennen",
    "run_action_query": "Aktionsabfrage ausführen",
    "create_form": "Formular erstellen",
    "create_report": "Bericht erstellen",
    "rename_object": "Objekt umbenennen",
    "delete_object": "Objekt löschen",
}


# --------------------------------------------------------------------------- Analyse und Plan
class MCOption(BaseModel):
    label: str = ""
    text: str
    correct: bool = False
    reason: str = ""


class MultipleChoiceAnswer(BaseModel):
    question: str = ""
    options: list[MCOption] = Field(default_factory=list)
    explanation: str = ""
    #: Hinweis, falls die Antwort unsicher ist (leer = sicher)
    uncertainty: str = ""

    @property
    def correct_labels(self) -> list[str]:
        return [o.label or o.text for o in self.options if o.correct]


class TaskAnalysis(BaseModel):
    task_type: TaskType = TaskType.UNKNOWN
    additional_types: list[TaskType] = Field(default_factory=list)
    title: str = ""
    summary: str = ""
    requirements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    #: Nummern (1-basiert) der Anforderungen, die keine Änderung an der Datenbank brauchen
    manual_requirements: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Ausformulierte Antwort bei Theorie-/Verständnisfragen
    answer: str = ""
    multiple_choice: MultipleChoiceAnswer | None = None
    #: Verständliche Erklärung des Lösungswegs
    explanation: str = ""

    @field_validator("task_type", mode="before")
    @classmethod
    def _tt(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return TaskType(v.strip().lower())
            except ValueError:
                return TaskType.UNKNOWN
        return v

    @field_validator("additional_types", mode="before")
    @classmethod
    def _at(cls, v: Any) -> Any:
        if isinstance(v, list):
            result = []
            for item in v:
                try:
                    result.append(TaskType(str(item).strip().lower()))
                except ValueError:
                    continue
            return result
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> Any:
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 0.5
        if number > 1:
            number /= 100
        return min(max(number, 0.0), 1.0)


class PlanStep(BaseModel):
    id: int = 0
    title: str = ""
    action: Action
    rationale: str = ""
    #: Nummern (1-basiert) der Anforderungen aus ``analysis.requirements``, die dieser Schritt erfüllt
    covers: list[int] = Field(default_factory=list)

    @property
    def risk(self) -> Risk:
        return self.action.risk

    @property
    def label(self) -> str:
        return ACTION_LABELS.get(self.action.action, self.action.action)


class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _number(self) -> ExecutionPlan:
        for index, step in enumerate(self.steps, start=1):
            step.id = index
            if not step.title:
                target = step.action.target()
                step.title = f"{step.label}: {target}" if target else step.label
        return self


    @property
    def is_empty(self) -> bool:
        return not self.steps


class AgentSolution(BaseModel):
    """Vollständige, validierte Antwort der KI auf eine Aufgabenstellung."""

    analysis: TaskAnalysis
    plan: ExecutionPlan = Field(default_factory=ExecutionPlan)

    def uncovered_requirements(self) -> list[int]:
        """Anforderungen, die weder ein Schritt abdeckt noch als „ohne Datenbankänderung“ markiert sind."""
        if self.plan.is_empty or self.analysis.task_type in (TaskType.THEORY, TaskType.MULTIPLE_CHOICE):
            return []
        done = {n for step in self.plan.steps for n in step.covers} | set(self.analysis.manual_requirements)
        return [n for n in range(1, len(self.analysis.requirements) + 1) if n not in done]


RequirementStatus = Literal["erfüllt", "nicht erfüllt", "unklar"]


class RequirementResult(BaseModel):
    number: int = 0
    requirement: str = ""
    status: RequirementStatus = "unklar"
    evidence: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> Any:
        text = str(v).strip().lower()
        if text in ("met", "ok", "yes", "true", "erfuellt"):
            return "erfüllt"
        if text in ("not met", "no", "false", "nicht erfuellt", "nicht_erfüllt"):
            return "nicht erfüllt"
        return text if text in ("erfüllt", "nicht erfüllt") else "unklar"


class RequirementReport(BaseModel):
    """Prüfung jeder Anforderung gegen den tatsächlichen Zustand der Datenbank."""

    results: list[RequirementResult] = Field(default_factory=list)

    @property
    def unmet(self) -> list[RequirementResult]:
        return [r for r in self.results if r.status == "nicht erfüllt"]

    @property
    def unclear(self) -> list[RequirementResult]:
        return [r for r in self.results if r.status == "unklar"]


class CapturedTask(BaseModel):
    """Eine erfasste Aufgabenstellung samt Herkunft."""

    text: str = ""
    #: ``manual`` | ``uia`` | ``ocr`` | ``vision`` | ``image`` | ``clipboard``
    method: str = "manual"
    source: str = ""
    window_title: str = ""
    #: Datenbank, zu der die Aufgabe gehört (te.comp-Übung), sonst leer
    db_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: Abbildungen der te.comp-Aufgabenseite (PNG) – Übungsmaterial, nie Bildschirmfotos; nicht gespeichert
    images: list[bytes] = Field(default_factory=list, exclude=True, repr=False)

"""Eingabemodelle (Spezifikationen) für Engine-Operationen.

Diese Modelle werden sowohl von der Engine als auch vom validierten Aktionsschema der
KI-Planung verwendet. Unbekannte Felder werden abgelehnt (``extra="forbid"``), damit
fehlerhafte KI-Ausgaben früh auffallen.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.access.constants import AccessType, normalize_type
from app.access.naming import validate_name


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Indexed = Literal["no", "duplicates", "unique"]


class LookupSpec(StrictModel):
    """Nachschlagefeld: in Datenblatt und Formularen ein Kombinationsfeld mit fester Liste oder Datenherkunft."""

    #: feste Werteliste („Werte selbst eingeben“) …
    values: list[str] = Field(default_factory=list)
    #: … oder Datensatzherkunft: Tabelle, Abfrage oder Access-SQL
    row_source: str | None = None
    bound_column: int = Field(default=1, ge=1, le=20)
    column_count: int = Field(default=1, ge=1, le=20)
    column_widths: str | None = None
    #: „Nur Listeneinträge“
    limit_to_list: bool = False
    #: „Bearbeitung von Wertlisten zulassen“
    allow_value_list_edits: bool = True

    @model_validator(mode="after")
    def _one_source(self) -> LookupSpec:
        if bool(self.values) == bool(self.row_source):
            raise ValueError("Ein Nachschlagefeld braucht entweder 'values' (Werteliste) oder 'row_source' (Tabelle/Abfrage/SQL).")
        return self

    def row_source_text(self) -> str:
        """Wert der Eigenschaft „Datensatzherkunft“ – eine Werteliste in Access-Schreibweise ``"A";"B"``."""
        if self.row_source:
            return self.row_source
        return ";".join('"' + value.replace('"', '""') + '"' for value in self.values)


class FieldSpec(StrictModel):
    name: str
    type: AccessType = AccessType.SHORT_TEXT
    size: int | None = Field(default=None, ge=1, le=255)
    required: bool = False
    allow_zero_length: bool | None = None
    default_value: str | None = None
    validation_rule: str | None = None
    validation_text: str | None = None
    description: str | None = None
    caption: str | None = None
    format: str | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=15)
    input_mask: str | None = None
    lookup: LookupSpec | None = None
    primary_key: bool = False
    indexed: Indexed | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_type(value)
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_name(value, "Feld")

    @field_validator("default_value", mode="before")
    @classmethod
    def _default_to_str(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)


class FieldPropertyChanges(StrictModel):
    """Zu ändernde Feldeigenschaften; ``None`` bedeutet „unverändert“."""

    size: int | None = Field(default=None, ge=1, le=255)
    required: bool | None = None
    allow_zero_length: bool | None = None
    default_value: str | None = None
    validation_rule: str | None = None
    validation_text: str | None = None
    description: str | None = None
    caption: str | None = None
    format: str | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=15)
    input_mask: str | None = None
    lookup: LookupSpec | None = None
    indexed: Indexed | None = None

    @field_validator("default_value", mode="before")
    @classmethod
    def _default_to_str(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)


class RelationFieldPair(StrictModel):
    primary: str
    foreign: str


JoinType = Literal["inner", "left", "right"]


# --------------------------------------------------------------------------- Abfragen
Aggregate = Literal["sum", "count", "avg", "min", "max", "first", "last", "stdev", "var", "group_by", "where"]
SortDirection = Literal["ASC", "DESC"]


class QueryFieldSpec(StrictModel):
    """Eine Spalte des Abfrageentwurfs (entspricht einer Spalte im Access-Entwurfsraster)."""

    #: Feldname; ``*`` für alle Felder
    field: str | None = None
    table: str | None = None
    #: Berechneter Ausdruck in Access-Syntax, z. B. ``[Menge]*[Einzelpreis]``
    expression: str | None = None
    alias: str | None = None
    aggregate: Aggregate | None = None
    sort: SortDirection | None = None
    #: Kriterium in Access-Entwurfssyntax, z. B. ``"Tirol"``, ``>100``, ``Wie "A*"``, ``Zwischen 1 Und 5``
    criteria: str | None = None
    #: Weitere „oder“-Zeilen des Entwurfsrasters
    or_criteria: list[str] = Field(default_factory=list)
    show: bool = True

    @field_validator("sort", mode="before")
    @classmethod
    def _norm_sort(cls, value: Any) -> Any:
        if isinstance(value, str):
            upper = value.strip().upper()
            mapping = {"AUFSTEIGEND": "ASC", "ABSTEIGEND": "DESC", "ASCENDING": "ASC", "DESCENDING": "DESC"}
            return mapping.get(upper, upper) or None
        return value

    @field_validator("aggregate", mode="before")
    @classmethod
    def _norm_agg(cls, value: Any) -> Any:
        if isinstance(value, str):
            lower = value.strip().lower().replace(" ", "_")
            mapping = {
                "summe": "sum",
                "anzahl": "count",
                "mittelwert": "avg",
                "average": "avg",
                "minimum": "min",
                "maximum": "max",
                "gruppierung": "group_by",
                "groupby": "group_by",
                "group": "group_by",
                "bedingung": "where",
                "erster_wert": "first",
                "letzter_wert": "last",
            }
            return mapping.get(lower, lower) or None
        return value

    @model_validator(mode="after")
    def _field_or_expression(self) -> QueryFieldSpec:
        if not self.field and not self.expression:
            raise ValueError("Entweder 'field' oder 'expression' muss angegeben werden.")
        if self.expression and not self.alias and not self.field:
            raise ValueError("Ein berechnetes Feld (expression) benötigt einen Namen (alias).")
        return self


class JoinSpec(StrictModel):
    left_table: str
    right_table: str
    left_field: str
    right_field: str
    type: JoinType = "inner"

    @field_validator("type", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> Any:
        if isinstance(value, str):
            lower = value.strip().lower().replace(" join", "").replace("_join", "")
            return {"outer": "left", "left outer": "left", "right outer": "right"}.get(lower, lower)
        return value


class QueryParameterSpec(StrictModel):
    name: str
    type: AccessType = AccessType.SHORT_TEXT

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> Any:
        return normalize_type(value) if isinstance(value, str) else value


QueryKind = Literal["select", "crosstab", "update", "delete", "append", "make_table"]


class CrosstabSpec(StrictModel):
    row_headings: list[str]
    column_heading: str
    value_field: str
    value_aggregate: Aggregate = "sum"


class QuerySpec(StrictModel):
    """Strukturierte Abfragedefinition, aus der Access-SQL erzeugt wird."""

    kind: QueryKind = "select"
    tables: list[str] = Field(min_length=1)
    joins: list[JoinSpec] = Field(default_factory=list)
    fields: list[QueryFieldSpec] = Field(default_factory=list)
    #: Zusätzliche WHERE-Bedingung in Access-SQL (wird mit UND an die Kriterien angehängt)
    where: str | None = None
    having: str | None = None
    distinct: bool = False
    top: int | None = Field(default=None, ge=1)
    top_percent: bool = False
    parameters: list[QueryParameterSpec] = Field(default_factory=list)
    crosstab: CrosstabSpec | None = None
    #: Aktualisierungsabfrage: Feld -> Ausdruck
    set_values: dict[str, str] = Field(default_factory=dict)
    #: Anfüge-/Tabellenerstellungsabfrage: Zieltabelle
    target_table: str | None = None

    @model_validator(mode="after")
    def _check(self) -> QuerySpec:
        if self.kind == "select" and not self.fields:
            raise ValueError("Eine Auswahlabfrage benötigt mindestens ein Feld.")
        if self.kind == "crosstab" and self.crosstab is None:
            raise ValueError("Eine Kreuztabellenabfrage benötigt die Angabe 'crosstab'.")
        if self.kind == "update" and not self.set_values:
            raise ValueError("Eine Aktualisierungsabfrage benötigt 'set_values'.")
        if self.kind in ("append", "make_table") and not self.target_table:
            raise ValueError("Für diese Abfrage muss 'target_table' angegeben werden.")
        return self


# --------------------------------------------------------------------------- Formulare / Berichte
ControlKind = Literal["text_box", "label", "combo_box", "check_box", "list_box", "button", "subform"]
SectionName = Literal["detail", "header", "footer", "page_header", "page_footer"]


class ControlSpec(StrictModel):
    type: ControlKind = "text_box"
    name: str | None = None
    #: Datenfeld (Steuerelementinhalt) oder Ausdruck mit führendem ``=``
    field: str | None = None
    #: Beschriftung (für Labels/Schaltflächen bzw. das zugeordnete Bezeichnungsfeld)
    caption: str | None = None
    #: Datensatzherkunft für Kombinations-/Listenfelder (Tabelle, Abfrage oder SQL)
    row_source: str | None = None
    section: SectionName = "detail"
    left_cm: float | None = Field(default=None, ge=0, le=55)
    top_cm: float | None = Field(default=None, ge=0, le=55)
    width_cm: float | None = Field(default=None, gt=0, le=55)
    height_cm: float | None = Field(default=None, gt=0, le=55)
    with_label: bool = True
    #: Formatierung des Steuerelements
    bold: bool | None = None
    italic: bool | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    fore_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    #: Gestapeltes Layout: 0 = linke Feldspalte (Standard), 1 = rechte Feldspalte (eigenes Layout daneben)
    layout_column: int | None = Field(default=None, ge=0, le=1)
    #: Unterformular: eingebettetes Formular (oder „Table.X“/„Query.X“) und die Verknüpfungsfelder
    source_object: str | None = None
    link_master_fields: list[str] = Field(default_factory=list)
    link_child_fields: list[str] = Field(default_factory=list)

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            lower = value.strip().lower().replace(" ", "_").replace("-", "_")
            mapping = {
                "textbox": "text_box",
                "textfeld": "text_box",
                "bezeichnungsfeld": "label",
                "bezeichnung": "label",
                "combobox": "combo_box",
                "kombinationsfeld": "combo_box",
                "checkbox": "check_box",
                "kontrollkästchen": "check_box",
                "kontrollkaestchen": "check_box",
                "listbox": "list_box",
                "listenfeld": "list_box",
                "command_button": "button",
                "schaltfläche": "button",
                "unterformular": "subform",
                "sub_form": "subform",
            }
            return mapping.get(lower, lower)
        return value

    @model_validator(mode="after")
    def _subform(self) -> ControlSpec:
        if self.type == "subform" and not self.source_object:
            raise ValueError("Ein Unterformular braucht 'source_object' (Name des eingebetteten Formulars).")
        if len(self.link_master_fields) != len(self.link_child_fields):
            raise ValueError("link_master_fields und link_child_fields müssen gleich viele Felder haben.")
        return self


ConditionOperator = Literal["between", "not_between", "equal", "not_equal", "greater", "less",
                            "greater_equal", "less_equal"]


class FormatRuleSpec(StrictModel):
    """Eine Regel der bedingten Formatierung: Ausdruck (z. B. ``[Umsatz]>1000``) oder Feldwert mit Operator."""

    expression: str | None = None
    operator: ConditionOperator | None = None
    value: str | None = None
    value2: str | None = None
    #: Farben als "#RRGGBB"
    fore_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    #: Datenbalken statt Farbregel (Balkenfarbe = back_color); Grenzen als Zahl, sonst automatisch
    data_bar: bool = False
    shortest_value: float | None = None
    longest_value: float | None = None
    show_bar_only: bool = False

    @model_validator(mode="after")
    def _kind(self) -> FormatRuleSpec:
        if self.data_bar:
            if self.expression or self.operator:
                raise ValueError("Ein Datenbalken hat keinen Ausdruck und keinen Vergleich.")
            return self
        if bool(self.expression) == bool(self.operator):
            raise ValueError("Eine Regel braucht entweder 'expression' oder 'operator' + 'value'.")
        if self.operator and self.value is None:
            raise ValueError("Für einen Vergleich mit dem Feldwert wird 'value' benötigt.")
        if self.operator in ("between", "not_between") and self.value2 is None:
            raise ValueError("„zwischen“ braucht 'value' und 'value2'.")
        if not any(v is not None for v in (self.fore_color, self.back_color, self.bold, self.italic, self.underline)):
            raise ValueError("Die Regel ändert keine Formatierung (Farbe, fett, kursiv, unterstrichen).")
        return self


class NavigationTabSpec(StrictModel):
    caption: str
    #: Formular oder Bericht, das/der beim Klick angezeigt wird
    target: str
    target_type: Literal["form", "report"] = "form"


FormView = Literal["single", "continuous", "datasheet", "split"]


class FormSpec(StrictModel):
    name: str
    record_source: str | None = None
    caption: str | None = None
    #: Kurzform: Felder, die als Textfelder mit Beschriftung untereinander angeordnet werden
    fields: list[str] = Field(default_factory=list)
    controls: list[ControlSpec] = Field(default_factory=list)
    default_view: FormView = "single"
    title: str | None = None
    properties: dict[str, bool | int | str] = Field(default_factory=dict)
    #: „stacked“ = Steuerelementlayout wie „Erstellen > Formular“ (Beschriftung links, Feld rechts, je Zeile)
    layout: Literal["none", "stacked"] = "none"
    #: Breite je Layoutspalte von links (cm): [Beschriftungen, Felder, (rechte Beschriftungen, rechte Felder)]
    column_widths_cm: list[float] = Field(default_factory=list, max_length=6)
    #: Zellen verbinden: Feldname → Anzahl zusätzlicher Spalten rechts daneben, die die Zelle überspannt
    span_right: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _layout_options(self) -> FormSpec:
        if (self.column_widths_cm or self.span_right) and self.layout != "stacked":
            raise ValueError("column_widths_cm und span_right gibt es nur mit layout „stacked“.")
        if any(w <= 0 or w > 30 for w in self.column_widths_cm) or any(n < 1 or n > 4 for n in self.span_right.values()):
            raise ValueError("Spaltenbreiten 0–30 cm, span_right 1–4 Spalten.")
        return self

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_name(value, "Formular")


class GroupSpec(StrictModel):
    field: str
    sort: SortDirection = "ASC"
    header: bool = True
    footer: bool = True
    #: jede Gruppe beginnt auf einer neuen Seite („Neue Seite: Vor Bereich“)
    new_page: bool = False
    #: Gruppe zusammenhalten („Ganze Gruppe auf einer Seite“)
    keep_together: bool = False


ReportSection = Literal["report_header", "page_header", "group_header", "detail", "group_footer", "page_footer",
                        "report_footer"]


class ReportControlSpec(StrictModel):
    """Zusätzliches Berichtssteuerelement, z. B. berechnetes Feld im Gruppenfuß, Titel, Trennlinie, Datum."""

    type: Literal["text_box", "label", "line"] = "text_box"
    section: ReportSection = "detail"
    #: Index in ``group_by`` für group_header/group_footer
    group: int = Field(default=0, ge=0, le=9)
    #: Feldname oder Ausdruck mit führendem ``=``, z. B. ``=Anzahl(*) & " Check-Ins"``
    expression: str | None = None
    #: Text des Bezeichnungsfelds (bei text_box links davon)
    caption: str | None = None
    name: str | None = None
    left_cm: float | None = Field(default=None, ge=0, le=55)
    top_cm: float | None = Field(default=None, ge=0, le=55)
    width_cm: float | None = Field(default=None, gt=0, le=55)
    height_cm: float | None = Field(default=None, gt=0, le=55)
    bold: bool | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    format: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ReportControlSpec:
        if self.type == "text_box" and not self.expression:
            raise ValueError("Ein Textfeld braucht expression (Feld oder =Ausdruck).")
        if self.type == "label" and not self.caption:
            raise ValueError("Ein Bezeichnungsfeld braucht caption.")
        return self


class SortSpec(StrictModel):
    field: str
    direction: SortDirection = "ASC"


ReportAggregate = Literal["sum", "count", "avg", "min", "max"]


class ReportTotalSpec(StrictModel):
    field: str
    function: ReportAggregate = "sum"
    #: Wo die Berechnung erscheint: je Gruppe, am Berichtsende oder beides
    scope: Literal["group", "report", "both"] = "both"
    label: str | None = None
    format: str | None = None


class PageSetupSpec(StrictModel):
    """„Seite einrichten“ eines Berichts. Wird im Bericht gespeichert – der Standarddrucker bleibt unverändert."""

    orientation: Literal["portrait", "landscape"] | None = None
    paper: Literal["A4", "A3", "A5", "Letter"] | None = None
    margin_top_cm: float | None = Field(default=None, ge=0, le=5)
    margin_bottom_cm: float | None = Field(default=None, ge=0, le=5)
    margin_left_cm: float | None = Field(default=None, ge=0, le=5)
    margin_right_cm: float | None = Field(default=None, ge=0, le=5)
    #: Ziel z. B. „alles auf einer Seite“ = 1: höchstens so viele Seiten in der Seitenansicht (wird nachgezählt)
    max_pages: int | None = Field(default=None, ge=1, le=500)
    #: „Seite einrichten → Spalten“: mehrspaltiger Bericht (Spaltenbreite wie Detailbereich)
    columns: int | None = Field(default=None, ge=1, le=6)
    column_spacing_cm: float | None = Field(default=None, ge=0, le=5)
    #: „across“ = erst quer, dann nach unten; „down“ = erst nach unten, dann quer
    column_layout: Literal["across", "down"] | None = None

    @model_validator(mode="after")
    def _check(self) -> PageSetupSpec:
        if all(getattr(self, k) is None for k in type(self).model_fields):
            raise ValueError("Keine Seiteneinstellung angegeben.")
        return self


#: Etikettenformate (mm): Breite, Höhe, nebeneinander, untereinander, Rand oben, Rand links, Abstand Spalten/Zeilen
LABEL_FORMATS: dict[str, tuple[float, float, int, int, float, float, float, float]] = {
    "Avery J8163": (99.1, 38.1, 2, 7, 15.15, 4.65, 2.5, 0.0),
    "Avery L7163": (99.1, 38.1, 2, 7, 15.15, 4.65, 2.5, 0.0),
    "Avery L7160": (63.5, 38.1, 3, 7, 15.15, 7.25, 2.54, 0.0),
    "Avery L7161": (63.5, 46.6, 3, 6, 8.8, 7.25, 2.54, 0.0),
    "Avery L7162": (99.1, 33.9, 2, 8, 12.9, 4.65, 2.5, 0.0),
    "Avery L7165": (99.1, 67.7, 2, 4, 13.1, 4.65, 2.5, 0.0),
}


class LabelSpec(StrictModel):
    """Etikettenbericht (wie der Etikettenassistent, A4 hoch): ein bekanntes Format ODER eigene Maße.
    Die Zeilen des Etiketts sind die ``controls`` des Berichts (Bereich „detail“, untereinander)."""

    type: str | None = None
    width_cm: float | None = Field(default=None, gt=1, le=21)
    height_cm: float | None = Field(default=None, gt=0.5, le=29)
    across: int | None = Field(default=None, ge=1, le=6)
    down: int | None = Field(default=None, ge=1, le=30)
    margin_top_cm: float = Field(default=1.0, ge=0, le=5)
    margin_left_cm: float = Field(default=0.5, ge=0, le=5)
    column_gap_cm: float = Field(default=0.0, ge=0, le=5)
    row_gap_cm: float = Field(default=0.0, ge=0, le=5)

    @model_validator(mode="after")
    def _check(self) -> LabelSpec:
        if self.type is not None:
            known = {k.lower().replace(" ", ""): k for k in LABEL_FORMATS}
            name = known.get(self.type.lower().replace(" ", ""))
            if name is None:
                raise ValueError(f"Etikettenformat „{self.type}“ unbekannt – bekannt: {', '.join(LABEL_FORMATS)}; "
                                 "sonst width_cm, height_cm, across, down angeben.")
            self.type = name
        elif None in (self.width_cm, self.height_cm, self.across, self.down):
            raise ValueError("Etiketten: type oder width_cm, height_cm, across und down angeben.")
        w, _h, across, _down, _top, left, gap, _row = self.mm()
        if left * 2 + across * w + (across - 1) * gap > 210.5:
            raise ValueError("Die Etiketten passen nebeneinander nicht auf A4.")
        return self

    def mm(self) -> tuple[float, float, int, int, float, float, float, float]:
        if self.type:
            return LABEL_FORMATS[self.type]
        return (float(self.width_cm or 0) * 10, float(self.height_cm or 0) * 10, int(self.across or 1),
                int(self.down or 1), self.margin_top_cm * 10, self.margin_left_cm * 10, self.column_gap_cm * 10,
                self.row_gap_cm * 10)


class ReportSpec(StrictModel):
    name: str
    record_source: str
    title: str | None = None
    fields: list[str] = Field(min_length=1)
    group_by: list[GroupSpec] = Field(default_factory=list)
    sort_by: list[SortSpec] = Field(default_factory=list)
    totals: list[ReportTotalSpec] = Field(default_factory=list)
    orientation: Literal["portrait", "landscape"] = "portrait"
    #: "tabular" = Spalten nebeneinander (Überschriften im Seitenkopf), "columnar" = Spaltenlayout/„Einspaltig“
    #: (je Datensatz Beschriftung links, Feld rechts untereinander)
    layout: Literal["tabular", "columnar"] = "tabular"
    show_page_numbers: bool = True
    show_date: bool = False
    caption: str | None = None
    #: Filter/Sortierung des BERICHTS (nicht der Datenquelle), z. B. ``[Status]="M"`` bzw. ``[Familienname], [Vorname]``
    filter: str | None = None
    order_by: str | None = None
    #: Detailbereich ausblenden (nur Gruppensummen anzeigen)
    hide_detail: bool = False
    controls: list[ReportControlSpec] = Field(default_factory=list)
    page_setup: PageSetupSpec | None = None
    #: Etikettenbericht: Zeilen = ``controls`` im Detailbereich; ``fields`` = verwendete Felder (zur Prüfung)
    labels: LabelSpec | None = None

    @model_validator(mode="after")
    def _check_controls(self) -> ReportSpec:
        if self.labels and (self.group_by or self.totals or self.title or self.layout != "tabular" or self.hide_detail
                            or self.page_setup or self.orientation != "portrait" or any(ctl.section != "detail" for ctl in self.controls) or not self.controls):
            raise ValueError("Etikettenbericht: nur Zeilen (controls im Bereich „detail“) und Sortierung (sort_by) – "
                             "keine Gruppen, Summen, Titel oder Spaltenlayout.")
        for ctl in self.controls:
            if ctl.section in ("group_header", "group_footer"):
                if ctl.group >= len(self.group_by):
                    raise ValueError(f"Steuerelement im Gruppenbereich: Gruppe {ctl.group} gibt es nicht (group_by).")
                group = self.group_by[ctl.group]
                if not (group.header if ctl.section == "group_header" else group.footer):
                    raise ValueError(f"Die Gruppe „{group.field}“ hat keinen {'Kopf' if ctl.section == 'group_header' else 'Fuß'}.")
        return self

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_name(value, "Bericht")


class DatasheetSpec(StrictModel):
    """Einstellungen der Datenblattansicht einer Tabelle oder gespeicherten Abfrage (wie im Datenblatt per Maus)."""

    #: gewünschte Reihenfolge (die genannten Spalten zuerst, der Rest behält seine Reihenfolge)
    column_order: list[str] = Field(default_factory=list)
    hidden: list[str] = Field(default_factory=list)
    unhide: list[str] = Field(default_factory=list)
    widths_cm: dict[str, float] = Field(default_factory=dict)
    #: „optimale Breite“ (Doppelklick auf den Spaltenrand)
    best_fit: list[str] = Field(default_factory=list)
    #: „Felder fixieren“ – Access stellt fixierte Spalten ganz nach vorne
    frozen: list[str] = Field(default_factory=list)
    unfreeze: bool = False
    #: Format-Eigenschaft je Spalte, z. B. Abfragefeld Tag → ``ddd", "dd.mm.`` oder Betrag → ``#,##0.00``
    formats: dict[str, str] = Field(default_factory=dict)
    #: Spaltenbeschriftung (Feldname bleibt)
    captions: dict[str, str] = Field(default_factory=dict)
    #: „Alternative Zeilenfarbe“
    alternate_back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def _check(self) -> DatasheetSpec:
        if not (self.column_order or self.hidden or self.unhide or self.widths_cm or self.best_fit or self.frozen
                or self.unfreeze or self.formats or self.captions or self.alternate_back_color):
            raise ValueError("Keine Datenblatteinstellung angegeben.")
        if any(not 0.2 <= w <= 50 for w in self.widths_cm.values()):
            raise ValueError("Spaltenbreiten müssen zwischen 0,2 und 50 cm liegen.")
        return self

    def columns(self) -> set[str]:
        """Alle hier genannten Spaltennamen."""
        return {*self.column_order, *self.hidden, *self.unhide, *self.widths_cm, *self.best_fit, *self.frozen,
                *self.formats, *self.captions}


class ControlChange(StrictModel):
    """Vorhandenes Formular-/Berichtssteuerelement verschieben, umbeschriften, formatieren oder ausblenden."""

    name: str
    left_cm: float | None = Field(default=None, ge=0, le=55)
    top_cm: float | None = Field(default=None, ge=0, le=55)
    width_cm: float | None = Field(default=None, gt=0, le=55)
    caption: str | None = None
    bold: bool | None = None
    font_size: int | None = Field(default=None, ge=6, le=72)
    fore_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    format: str | None = None
    visible: bool | None = None
    height_cm: float | None = Field(default=None, gt=0, le=55)
    italic: bool | None = None
    back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    #: Rahmen („Formkontur“): 0 = transparent (Umrandung aus), 1 = durchgezogen, 2–7 gestrichelt/gepunktet
    border_style: int | None = Field(default=None, ge=0, le=7)
    #: Linienstärke 0 = Haarlinie … 6 = 6 pt
    border_width: int | None = Field(default=None, ge=0, le=6)
    border_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    #: Hintergrund „Transparent“ (true) bzw. „Normal“ (false)
    transparent: bool | None = None
    #: „Aktiviert“ / „Gesperrt“ – beides Nein/Ja = Feld lässt sich nicht anklicken/ändern
    enabled: bool | None = None
    locked: bool | None = None
    #: Datenbindung („Steuerelementinhalt“): Feldname oder Ausdruck mit führendem "=", z. B. "=[Preis]*[Menge]"
    control_source: str | None = None
    #: im Steuerelementlayout mit so vielen LEEREN Zellen darunter bzw. rechts daneben verbinden („Zellen verbinden“)
    merge_down: int | None = Field(default=None, ge=1, le=10)
    merge_right: int | None = Field(default=None, ge=1, le=4)


class SectionColorSpec(StrictModel):
    """Farben eines Bereichs (Access: Bereich → Hintergrundfarbe / Alternative Hintergrundfarbe). Die alternative
    Farbe färbt jede zweite Zeile bzw. jeden zweiten Datensatz – „Format > Alternative Zeilenfarbe“ in der Layoutansicht
    eines Berichts oder Endlosformulars. Für Tabellen-/Abfragedatenblätter gilt stattdessen set_datasheet_layout."""

    section: ReportSection = "detail"
    #: Gruppenebene (0 = erste) bei group_header/group_footer
    group: int = Field(default=0, ge=0, le=9)
    back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    alternate_back_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def _check(self) -> SectionColorSpec:
        if not (self.back_color or self.alternate_back_color):
            raise ValueError("Für den Bereich ist keine Farbe angegeben.")
        if self.alternate_back_color and self.section in ("page_header", "page_footer"):
            # Access: Seitenkopf/-fuß haben keine „Alternative Hintergrundfarbe“ (Laufzeitfehler 2455, gemessen)
            raise ValueError("Seitenkopf und Seitenfuß haben keine alternative Hintergrundfarbe – jede zweite Zeile "
                             "färbt der Detailbereich (section \"detail\").")
        return self


class ReportChangeSpec(StrictModel):
    """Änderungen an einem VORHANDENEN Bericht (te.comp: „Bearbeiten Sie den Bericht …“)."""

    #: Filter/Sortierung des Berichts; "" entfernt sie
    filter: str | None = None
    order_by: str | None = None
    hide_detail: bool | None = None
    #: Gruppen: vorhandene Ebene desselben Felds wird angepasst (Sortierung, neue Seite, zusammenhalten), sonst neu angelegt
    groups: list[GroupSpec] = Field(default_factory=list)
    #: neue Steuerelemente; ``group`` = Index der Gruppenebene im Bericht (nach dem Anlegen neuer Gruppen)
    controls: list[ReportControlSpec] = Field(default_factory=list)
    control_changes: list[ControlChange] = Field(default_factory=list)
    #: Steuerelemente entfernen (z. B. „nicht benötigte Felder löschen“) – Daten bleiben unberührt
    delete_controls: list[str] = Field(default_factory=list)
    page_setup: PageSetupSpec | None = None
    #: neue Datenquelle (Tabelle, Abfrage oder SELECT) – „Datensatzquelle“ des Berichts
    record_source: str | None = None
    #: Berichtsbreite (z. B. für mehrspaltigen Druck schmäler); Steuerelemente vorher per control_changes einpassen
    width_cm: float | None = Field(default=None, gt=1, le=55)
    #: Hintergrund-/alternative Hintergrundfarbe von Bereichen (z. B. jede zweite Detailzeile farbig)
    section_colors: list[SectionColorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ReportChangeSpec:
        if not (self.filter is not None or self.order_by is not None or self.hide_detail is not None or self.groups
                or self.controls or self.control_changes or self.delete_controls or self.page_setup
                or self.record_source or self.width_cm or self.section_colors):
            raise ValueError("Keine Berichtsänderung angegeben.")
        return self


class TabPageSpec(StrictModel):
    caption: str = Field(min_length=1)
    #: Felder, die als Textfeld mit Beschriftung auf diese Seite kommen
    fields: list[str] = Field(default_factory=list)
    #: vorhandenes Formular als Unterformular auf dieser Seite
    subform: str | None = None
    link_master_fields: list[str] = Field(default_factory=list)
    link_child_fields: list[str] = Field(default_factory=list)


class TabControlSpec(StrictModel):
    """Registersteuerelement („Daten in Registern organisieren“)."""

    pages: list[TabPageSpec] = Field(min_length=1, max_length=10)
    #: die genannten Felder wandern auf die Seiten (die bisherigen Steuerelemente dieser Felder werden entfernt)
    move_fields: bool = True
    left_cm: float = Field(default=0.3, ge=0, le=50)
    top_cm: float | None = Field(default=None, ge=0, le=50)
    width_cm: float = Field(default=16.0, gt=2, le=50)
    height_cm: float = Field(default=8.0, gt=2, le=50)


class FormChangeSpec(StrictModel):
    """Änderungen an einem VORHANDENEN Formular (te.comp: „Öffnen Sie das Formular … und ändern Sie …“)."""

    caption: str | None = None
    #: "split" = geteiltes Formular
    default_view: FormView | None = None
    #: erlaubte Formulareigenschaften, z. B. {"PopUp": true, "SplitFormSplitterBar": false}
    properties: dict[str, Any] = Field(default_factory=dict)
    #: neue Steuerelemente (section "detail", "report_header" = Formularkopf, "report_footer" = Formularfuß)
    controls: list[ReportControlSpec] = Field(default_factory=list)
    control_changes: list[ControlChange] = Field(default_factory=list)
    delete_controls: list[str] = Field(default_factory=list)
    tab_control: TabControlSpec | None = None
    #: neue Datenquelle (Tabelle, Abfrage oder SELECT) – „Datensatzquelle“ des Formulars
    record_source: str | None = None
    #: Hintergrund-/alternative Hintergrundfarbe von Bereichen (detail, report_header = Formularkopf, report_footer)
    section_colors: list[SectionColorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> FormChangeSpec:
        if not (self.caption is not None or self.default_view or self.properties or self.controls
                or self.control_changes or self.delete_controls or self.tab_control or self.record_source
                or self.section_colors):
            raise ValueError("Keine Formularänderung angegeben.")
        if any(s.section in ("group_header", "group_footer") for s in self.section_colors):
            raise ValueError("Formulare haben keine Gruppenbereiche.")
        bad = [c.section for c in self.controls if c.section not in ("detail", "report_header", "report_footer")]
        if bad:
            raise ValueError(f"Formulare haben nur Detailbereich, Formularkopf und -fuß (nicht {bad[0]}).")
        return self

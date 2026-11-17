"""Strukturmodelle einer Access-Datenbank (Ergebnis des Database Inspectors)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.access.constants import TYPE_LABELS_DE, AccessType


class FieldInfo(BaseModel):
    name: str
    type: AccessType
    dao_type: int = 0
    size: int = 0
    required: bool = False
    allow_zero_length: bool = True
    default_value: str = ""
    validation_rule: str = ""
    validation_text: str = ""
    description: str = ""
    caption: str = ""
    format: str = ""
    decimal_places: int | None = None
    input_mask: str = ""
    #: Nachschlagefeld: Datensatzherkunft (leer = kein Nachschlagefeld) und „Nur Listeneinträge“/„Liste bearbeitbar“
    lookup_source: str = ""
    lookup_limit_to_list: bool = False
    lookup_allow_edits: bool = False
    primary_key: bool = False
    indexed: Literal["no", "duplicates", "unique"] = "no"
    ordinal: int = 0

    def property_summary(self) -> str:
        """Gesetzte Feldeigenschaften in Kurzform (für die KI – damit sie solche Anforderungen prüfen kann)."""
        parts = [f"{label}: {value}" for label, value in (
            ("Standardwert", self.default_value), ("Gültigkeitsregel", self.validation_rule),
            ("Gültigkeitsmeldung", self.validation_text), ("Format", self.format), ("Eingabeformat", self.input_mask),
            ("Beschriftung", self.caption)) if value]
        if self.decimal_places is not None:
            parts.append(f"Dezimalstellen: {self.decimal_places}")
        if self.lookup_source:
            flags = ", nur Listeneinträge" if self.lookup_limit_to_list else ""
            flags += ", Liste bearbeitbar" if self.lookup_allow_edits else ""
            parts.append(f"Nachschlagefeld: {self.lookup_source}{flags}")
        return "; ".join(parts)

    @property
    def type_label(self) -> str:
        label = TYPE_LABELS_DE.get(self.type, self.type.value)
        if self.type == AccessType.SHORT_TEXT and self.size:
            return f"{label} ({self.size})"
        return label


class IndexInfo(BaseModel):
    name: str
    fields: list[str] = Field(default_factory=list)
    descending: list[bool] = Field(default_factory=list)
    primary: bool = False
    unique: bool = False
    required: bool = False
    ignore_nulls: bool = False
    foreign: bool = False


class TableInfo(BaseModel):
    name: str
    fields: list[FieldInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    record_count: int = -1
    linked: bool = False
    description: str = ""
    validation_rule: str = ""
    #: Gespeicherte Sortierung/Filter der Datenblattansicht (leer = keine)
    order_by: str = ""
    filter: str = ""
    #: Datenblatteinstellungen (Reihenfolge, ausgeblendet, fixiert, Breiten, Zeilenfarbe)
    datasheet: str = ""

    def field(self, name: str) -> FieldInfo | None:
        lowered = name.lower()
        return next((f for f in self.fields if f.name.lower() == lowered), None)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


class RelationInfo(BaseModel):
    name: str
    table: str  # Primärtabelle (1-Seite)
    foreign_table: str  # Detailtabelle (n-Seite)
    fields: list[tuple[str, str]] = Field(default_factory=list)  # (Primärfeld, Fremdfeld)
    enforce_integrity: bool = True
    cascade_update: bool = False
    cascade_delete: bool = False
    join_type: Literal["inner", "left", "right"] = "inner"
    relation_type: Literal["1:1", "1:n", "unbestimmt"] = "1:n"
    attributes: int = 0


class QueryParameter(BaseModel):
    name: str
    type: int = 0


class QueryInfo(BaseModel):
    name: str
    sql: str = ""
    type_code: int = 0
    type_name: str = "Auswahlabfrage"
    is_action: bool = False
    fields: list[str] = Field(default_factory=list)
    parameters: list[QueryParameter] = Field(default_factory=list)
    description: str = ""
    #: Datenblatteinstellungen inkl. Formate/Beschriftungen der Abfragefelder
    datasheet: str = ""
    #: nicht leer = Access kann die Abfrage nicht ausführen (z. B. Eingabetabelle fehlt) – keine brauchbare Datenquelle
    error: str = ""


class ControlInfo(BaseModel):
    name: str
    control_type: int
    #: Rahmenart: 0 transparent, 1 durchgezogen, 2 Striche, 3 kurze Striche, 4 Punkte … (-1 = nicht gelesen)
    border_style: int = -1
    type_name: str = ""
    control_source: str = ""
    caption: str = ""
    section: int = 0
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    row_source: str = ""
    #: Unterformular / Navigation
    source_object: str = ""
    link_master_fields: str = ""
    link_child_fields: str = ""
    navigation_target: str = ""
    #: Zieltyp der Navigationsschaltfläche: "form" | "report" | "" (keine)
    navigation_kind: str = ""
    #: Regeln der bedingten Formatierung (Kurzbeschreibung)
    conditions: list[str] = Field(default_factory=list)
    #: sichtbare Formatierung („fett“, „Hintergrund #FFFF00“ …), leer = Standard
    formatting: str = ""
    #: Steuerelementlayout: 0 = keines, 1 = tabellarisch, 2 = gestapelt; Kennung des Layouts
    layout: int = 0
    layout_id: int = 0
    #: übergeordnetes Steuerelement (z. B. Registerseite), sonst das Formular/der Bericht selbst
    parent: str = ""


def layout_summary(controls: list[ControlInfo]) -> list[str]:
    """Steuerelementlayouts eines Formulars: Spalten mit Breite und Zellen, die mehrere Spalten überspannen.

    Access gibt Spalte/Zeile einer Zelle nicht preis; die Spalten ergeben sich aus den linken Kanten der Zellen
    (auch leerer Zellen), eine verbundene Zelle reicht über den Beginn der nächsten Spalte hinaus."""
    lines = []
    for layout_id in sorted({c.layout_id for c in controls if c.layout and c.layout_id}):
        cells = [c for c in controls if c.layout_id == layout_id]
        kind = {1: "tabellarisch", 2: "gestapelt"}.get(cells[0].layout, str(cells[0].layout))
        lefts = sorted({c.left for c in cells})
        widths = []
        for index, left in enumerate(lefts):
            nxt = lefts[index + 1] if index + 1 < len(lefts) else None
            single = [c.width for c in cells if c.left == left and (nxt is None or c.left + c.width <= nxt + 30)]
            widths.append(min(single) if single else min(c.width for c in cells if c.left == left))
        columns = ", ".join(f"{i + 1}: {w / 567:.1f} cm" for i, w in enumerate(widths))
        spans = []
        for cell in cells:
            covered = sum(1 for left in lefts if cell.left <= left < cell.left + cell.width - 30)
            if covered > 1 and cell.control_type != 127:
                spans.append(f"{cell.name} über {covered} Spalten")
        members = ", ".join(c.name for c in cells if c.control_type != 127)
        lines.append(f"Steuerelementlayout {layout_id} ({kind}): Spaltenbreiten {columns}; verbundene Zellen: "
                     f"{', '.join(spans) or 'keine'}; Elemente: {members}")
    return lines


def _section_name(section: int) -> str:
    names = {0: "Detailbereich", 1: "Berichts-/Formularkopf", 2: "Berichts-/Formularfuß", 3: "Seitenkopf", 4: "Seitenfuß"}
    if section in names:
        return names[section]
    level, footer = divmod(section - 5, 2)
    return f"{'Gruppenfuß' if footer else 'Gruppenkopf'} Ebene {level + 1}"


class GroupLevelInfo(BaseModel):
    expression: str
    sort_descending: bool = False
    has_header: bool = False
    has_footer: bool = False
    new_page: bool = False
    keep_together: bool = False


class FormInfo(BaseModel):
    name: str
    record_source: str = ""
    caption: str = ""
    default_view: int = 0
    controls: list[ControlInfo] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    detailed: bool = False
    #: Bereichsnummer → (Hintergrund, alternativer Hintergrund), z. B. {0: ("#FFFFFF", "#F2F2F2")}
    section_colors: dict[int, tuple[str, str]] = Field(default_factory=dict)


class ReportInfo(BaseModel):
    name: str
    record_source: str = ""
    caption: str = ""
    controls: list[ControlInfo] = Field(default_factory=list)
    group_levels: list[GroupLevelInfo] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    detailed: bool = False
    #: Bereichsnummer → (Hintergrund, alternativer Hintergrund), z. B. {0: ("#FFFFFF", "#F2F2F2")}
    section_colors: dict[int, tuple[str, str]] = Field(default_factory=dict)


class DatabaseSchema(BaseModel):
    path: str
    name: str
    file_format: str = ""
    read_only: bool = False
    attached_to_running_instance: bool = False
    tables: list[TableInfo] = Field(default_factory=list)
    relations: list[RelationInfo] = Field(default_factory=list)
    queries: list[QueryInfo] = Field(default_factory=list)
    forms: list[FormInfo] = Field(default_factory=list)
    reports: list[ReportInfo] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)

    def table(self, name: str) -> TableInfo | None:
        lowered = name.lower()
        return next((t for t in self.tables if t.name.lower() == lowered), None)

    def query(self, name: str) -> QueryInfo | None:
        lowered = name.lower()
        return next((q for q in self.queries if q.name.lower() == lowered), None)

    def has_object(self, name: str) -> str | None:
        """Gibt den Objekttyp zurück, falls ein Objekt mit diesem Namen existiert."""
        lowered = name.lower()
        if any(t.name.lower() == lowered for t in self.tables):
            return "Tabelle"
        if any(q.name.lower() == lowered for q in self.queries):
            return "Abfrage"
        return None

    def source_fields(self, source: str) -> list[str]:
        """Feldnamen einer Tabelle oder Abfrage."""
        table = self.table(source)
        if table:
            return table.field_names
        query = self.query(source)
        return list(query.fields) if query else []

    def to_prompt_text(self, *, max_queries: int = 40) -> str:
        """Kompakte Textdarstellung für KI-Prompts."""
        lines: list[str] = [f"Datenbank: {self.name}"]
        lines.append("TABELLEN:")
        if not self.tables:
            lines.append("  (keine)")
        for table in self.tables:
            count = f", {table.record_count} Datensätze" if table.record_count >= 0 else ""
            lines.append(f"  - {table.name}{count}")
            if table.order_by:
                lines.append(f"      (Sortierung der Datenblattansicht: {table.order_by})")
            if table.filter:
                lines.append(f"      (Filter der Datenblattansicht: {table.filter})")
            if table.datasheet:
                lines.append(f"      (Datenblatt: {table.datasheet})")
            for fld in table.fields:
                flags = []
                if fld.primary_key:
                    flags.append("PK")
                if fld.required:
                    flags.append("Pflicht")
                if fld.indexed == "unique" and not fld.primary_key:
                    flags.append("eindeutig")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                props = fld.property_summary()
                lines.append(f"      {fld.name}: {fld.type_label}{suffix}" + (f" ({props})" if props else ""))
        lines.append("BEZIEHUNGEN:")
        if not self.relations:
            lines.append("  (keine)")
        for rel in self.relations:
            pairs = ", ".join(f"{rel.table}.{a} -> {rel.foreign_table}.{b}" for a, b in rel.fields)
            ri = "mit ref. Integrität" if rel.enforce_integrity else "ohne ref. Integrität"
            lines.append(f"  - {rel.name}: {pairs} ({rel.relation_type}, {ri})")
        lines.append("ABFRAGEN:")
        if not self.queries:
            lines.append("  (keine)")
        for query in self.queries[:max_queries]:
            # vollständig genug, dass Kriterien (WHERE/HAVING) prüfbar bleiben – Abfragen mit Joins sind lang
            sql = " ".join(query.sql.split())
            shown = sql if len(sql) <= 2000 else sql[:2000] + " … (gekürzt)"
            lines.append(f"  - {query.name} [{query.type_name}]: {shown}")
            if query.error:
                lines.append(f"      ⚠ NICHT AUSFÜHRBAR ({query.error}) – nicht als Datenquelle verwenden, außer sie wird "
                             "zuerst repariert (create_query mit modify_existing)")
            if query.datasheet:
                lines.append(f"      (Datenblatt: {query.datasheet})")
        for title, items in (("FORMULARE", self.forms), ("BERICHTE", self.reports)):
            if not any(i.detailed for i in items):
                lines.append(f"{title}: " + (", ".join(i.name for i in items) or "(keine)"))
                continue
            lines.append(f"{title}:")
            for item in items:
                sources = [f"{c.control_source} ({_section_name(c.section)})" if c.control_source.startswith("=")
                           else c.control_source for c in item.controls if c.control_source]
                groups = [g.expression + (" (neue Seite je Gruppe)" if g.new_page else "")
                          + (" (zusammenhalten)" if g.keep_together else "")
                          for g in getattr(item, "group_levels", []) if g.has_header or g.has_footer]
                detail = f"Datenquelle {item.record_source or '(keine)'}; Steuerelementinhalte: {', '.join(sources) or '(keine)'}"
                extra = [f"Gruppierung: {', '.join(groups)}"] if groups else []
                if isinstance(item, ReportInfo):
                    extra += [f"Bericht-{key}: {value}" + (" (beim Laden aktiv)" if key in ("Filter", "OrderBy") else "")
                              for key, value in item.properties.items()]
                if isinstance(item, FormInfo):
                    extra.append("Standardansicht " + {0: "Einzelnes Formular", 1: "Endlosformular", 2: "Datenblatt",
                                                       5: "Geteiltes Formular"}.get(item.default_view, str(item.default_view)))
                    fprops = item.properties
                    notable = [k for k in ("PopUp", "Modal") if fprops.get(k)]
                    notable += [k + " aus" for k in ("NavigationButtons", "RecordSelectors", "AllowEdits", "AllowAdditions",
                                                     "AllowDeletions") if fprops.get(k) is False]
                    if item.default_view == 5 and fprops.get("SplitFormSplitterBar") is False:
                        notable.append("Trennleiste aus")
                    notable += [f"{k} {fprops[k]}" for k in ("Filter", "OrderBy") if fprops.get(k)]
                    if fprops.get("Bereichsfarben"):
                        notable.append(f"Bereichsfarben: {fprops['Bereichsfarben']}")
                    if notable:
                        extra.append("Formulareigenschaften: " + ", ".join(notable))
                for ctl in item.controls:
                    if ctl.source_object:
                        link = f", verknüpft {ctl.link_master_fields} → {ctl.link_child_fields}" if ctl.link_master_fields else ""
                        extra.append(f"Unterformular {ctl.name} zeigt {ctl.source_object}{link}")
                    if ctl.navigation_target:
                        extra.append(f"Navigationsschaltfläche „{ctl.caption}“ → {ctl.navigation_target}")
                    if ctl.conditions:
                        extra.append(f"bedingte Formatierung {ctl.name}: {' | '.join(ctl.conditions)}")
                    if ctl.formatting:
                        extra.append(f"{ctl.name}: {ctl.formatting}")
                labels = [f"{c.name}=„{c.caption}“" for c in item.controls if c.control_type == 100 and c.caption]
                if labels:
                    extra.append("Beschriftungen: " + ", ".join(labels[:40]))
                extra += layout_summary(item.controls)
                pages = [ctl for ctl in item.controls if ctl.control_type == 124]  # Registerseite
                if pages:
                    extra.append("Register: " + "; ".join(
                        f"Seite „{p.caption or p.name}“: " + ", ".join(
                            x.control_source or x.source_object or x.name for x in item.controls
                            if x.parent == p.name and x.control_type != 100) for p in pages))
                if isinstance(item, FormInfo):  # Lage im Detailbereich (cm): links,oben Breite – für Layoutanforderungen
                    geometry = [f"{c.name}@{c.left / 567:.1f},{c.top / 567:.1f} B{c.width / 567:.1f}"
                                for c in item.controls if c.section == 0 and c.width]
                    if geometry:
                        extra.append("Lage (cm): " + ", ".join(geometry[:60]))
                lines.append(f"  - {item.name}: {detail}" + "".join(f"; {e}" for e in extra))
        return "\n".join(lines)

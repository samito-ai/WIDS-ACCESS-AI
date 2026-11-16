"""Formulare und Berichte über die Access-Objektbibliothek (``Access.Application``).

DAO kennt Formulare und Berichte nur als Dokumente; zum Erstellen und Gestalten wird die
Access-Anwendung benötigt. Alle Schritte laufen über dokumentierte Automationsmethoden
(``CreateForm``, ``CreateControl``, ``CreateReport``, ``CreateReportControl``,
``CreateGroupLevel``) – ohne Bildschirmkoordinaten.
"""
from __future__ import annotations

import contextlib
import re
from typing import Any

from app.access import constants as c
from app.access.comerrors import com_guard
from app.access.comutil import group_level, has_section, section
from app.access.models import ControlInfo, FormInfo, GroupLevelInfo, ReportInfo
from app.access.naming import validate_name
from app.access.session import AccessSession
from app.access.specs import (
    ControlChange,
    ControlSpec,
    FormatRuleSpec,
    FormChangeSpec,
    FormSpec,
    LabelSpec,
    NavigationTabSpec,
    PageSetupSpec,
    ReportChangeSpec,
    ReportControlSpec,
    ReportSpec,
    ReportTotalSpec,
    TabControlSpec,
)
from app.access.tables import collection_has
from app.core.errors import AccessError, ObjectExistsError, ObjectNotFoundError
from app.core.logging_setup import get_logger

log = get_logger(__name__)

TW = c.TWIPS_PER_CM

_CONTROL_TYPES = {
    "text_box": c.AC_TEXT_BOX,
    "label": c.AC_LABEL,
    "combo_box": c.AC_COMBO_BOX,
    "check_box": c.AC_CHECK_BOX,
    "list_box": c.AC_LIST_BOX,
    "button": c.AC_COMMAND_BUTTON,
    "subform": c.AC_SUBFORM,
}
_SECTIONS = {
    "detail": c.AC_DETAIL,
    "header": c.AC_HEADER,
    "footer": c.AC_FOOTER,
    "page_header": c.AC_PAGE_HEADER,
    "page_footer": c.AC_PAGE_FOOTER,
}
_FORM_VIEWS = {"single": 0, "continuous": 1, "datasheet": 2, "split": 5}
#: FilterOn/OrderByOn gelten nur im geöffneten Formular – im Entwurf gespeichert wird …OnLoad (gemessen)
_PERSISTENT_PROPS = {"FilterOn": "FilterOnLoad", "OrderByOn": "OrderByOnLoad"}
_AGG_FUNCS = {"sum": "Sum", "count": "Count", "avg": "Avg", "min": "Min", "max": "Max"}
_AGG_LABELS = {"sum": "Summe", "count": "Anzahl", "avg": "Mittelwert", "min": "Minimum", "max": "Maximum"}
#: Formulareigenschaften, die über FormSpec.properties gesetzt werden dürfen
_ALLOWED_FORM_PROPS = {
    "NavigationButtons",
    "RecordSelectors",
    "DividingLines",
    "ScrollBars",
    "AllowAdditions",
    "AllowDeletions",
    "AllowEdits",
    "DataEntry",
    "Modal",
    "PopUp",
    "AutoCenter",
    "OrderBy",
    "OrderByOn",
    "Filter",
    "FilterOn",
    "FilterOnLoad",
    "OrderByOnLoad",
    "Caption",
    "Width",
    "Cycle",
    "SplitFormOrientation",
    "SplitFormDatasheet",
    "SplitFormSplitterBar",
    "SplitFormSize",
    "SplitFormPrinting",
    "BorderStyle",
}


#: FormatCondition-Operatoren (AcFormatConditionOperator)
_OPERATORS = {"between": 0, "not_between": 1, "equal": 3, "not_equal": 4, "greater": 5, "less": 6,
              "greater_equal": 7, "less_equal": 8}
_OPERATOR_NAMES = {v: k for k, v in _OPERATORS.items()}

#: Papierformate (AcPrintPaperSize)
_PAPER = {"A4": 9, "A3": 8, "A5": 11, "Letter": 1}
_PAPER_NAMES = {v: k for k, v in _PAPER.items()}
#: „Seite einrichten“-Werte im Bericht (Report.Printer) – gelesen für Prüfung und Schema
#: Printer.ItemLayout – gemessen über die Druckreihenfolge im PDF-Export (14.11.2026): 1953 = Name02 neben Name01
#: (acPRHorizontalColumnLayout, erst quer), 1954 = Name11 oben in Spalte 2 (acPRVerticalColumnLayout, erst nach unten)
#: Standardbreiten beim Anlegen eines Formulars (Beschriftungs-/Feldspalte) – Bezug für „Spalte verschmälern“
DEFAULT_LABEL_WIDTH_CM, DEFAULT_CONTROL_WIDTH_CM = 3.8, 6.0
ACROSS_THEN_DOWN = 1953
DOWN_THEN_ACROSS = 1954
PRINTER_KEYS = ("Orientation", "PaperSize", "TopMargin", "BottomMargin", "LeftMargin", "RightMargin", "DefaultSize",
                "ItemsAcross", "ItemSizeWidth", "ItemSizeHeight", "ColumnSpacing", "RowSpacing", "ItemLayout")
_REPORT_EVENTS = ("OnOpen", "OnClose", "OnActivate", "OnDeactivate", "OnNoData", "OnPage", "OnError", "OnLoad")
_SECTION_EVENTS = ("OnFormat", "OnPrint", "OnRetreat")
_EXPR_WORDS = {"and", "or", "not", "like", "mod", "is", "null", "between", "in", "true", "false", "xor", "eqv",
               "imp", "und", "oder", "nicht", "wie", "ist", "wahr", "falsch", "zwischen"}
_BUILTIN_NAMES = {"page", "pages", "seite", "seiten"}


def expression_names(expression: str) -> set[str]:
    """Namen, auf die sich ein Access-Ausdruck bezieht (ohne Funktionen, Operatoren und Literale)."""
    text = re.sub(r'"[^"]*"|\'[^\']*\'|#[^#]*#', " ", expression)
    names = {n.lower() for n in re.findall(r"\[([^\]]+)\]", text)}
    rest = re.sub(r"\[[^\]]+\]", " ", text)
    names |= {n.lower() for n in re.findall(r"(?<![\w.!])([A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß]*)(?!\s*\()", rest)}
    return names - _EXPR_WORDS


def _tw_mm(mm: float) -> int:
    return round(mm * TW / 10)


def _describe_page(values: dict[str, int], width: int) -> str:
    """Kurzbeschreibung der Seiteneinstellungen für Schema und Prüfung."""
    margins = "/".join(f"{values[k] / TW:.1f}".replace(".", ",") for k in
                       ("TopMargin", "BottomMargin", "LeftMargin", "RightMargin"))
    text = (f"{_PAPER_NAMES.get(values['PaperSize'], str(values['PaperSize']))} "
            f"{'quer' if values['Orientation'] == 2 else 'hoch'}, Ränder o/u/l/r {margins} cm, "
            f"Berichtsbreite {width / TW:.1f} cm".replace(".", ","))
    if not values["DefaultSize"]:
        text += (f"; Etiketten {values['ItemsAcross']} nebeneinander, "
                 f"{values['ItemSizeWidth'] / TW:.2f} × {values['ItemSizeHeight'] / TW:.2f} cm").replace(".", ",")
    elif values["ItemsAcross"] > 1:
        text += (f"; {values['ItemsAcross']} Spalten "
                 f"({'erst nach unten' if values['ItemLayout'] == DOWN_THEN_ACROSS else 'erst quer'}, "
                 f"Abstand {values['ColumnSpacing'] / TW:.1f} cm)").replace(".", ",")
    return text


_LAYOUT_TYPES = (c.AC_LABEL, c.AC_TEXT_BOX, c.AC_COMBO_BOX, c.AC_CHECK_BOX)
_GAP = 57  # 0,1 cm wie beim Anlegen (Spaltenbreite − 0,1 cm)


def _geometry(obj: Any) -> dict[str, tuple[int, int, int, int, int]]:
    """Name (klein) → (Bereich, links, oben, Breite, Höhe) der Text-/Beschriftungs-/Kombi-/Kontrollfelder."""
    shapes: dict[str, tuple[int, int, int, int, int]] = {}
    for ctl in obj.Controls:
        with contextlib.suppress(Exception):
            if int(ctl.ControlType) in _LAYOUT_TYPES:
                shapes[str(ctl.Name).lower()] = (int(ctl.Section), int(ctl.Left), int(ctl.Top), int(ctl.Width),
                                                 int(ctl.Height))
    return shapes


def _reflow_overlapping_rows(obj: Any, before: dict[str, tuple[int, int, int, int, int]], touched: set[str]) -> None:
    """Zeilen, in denen ein geändertes/neues Steuerelement ein anderes überdeckt, neu anordnen (Live-Befund 0501/U4:
    „Mitglied“ auf 5,0 cm verschoben, „TeeTime“ blieb bei 4,9–7,2 cm). Reihenfolge = Lage von links; Beginn am
    ursprünglichen linken Rand der Zeile; Abstand 0,1 cm; Breiten bleiben. Seitenkopf und Detailbereich werden gleich
    behandelt und bleiben dadurch bündig. Zeilen ohne Überdeckung bleiben unverändert; passt die Zeile nicht in die
    Berichtsbreite, wird der Bericht breiter (die Seitenbreiten-Prüfung meldet, wenn die Seite nicht reicht)."""
    if not touched:
        return
    now = _geometry(obj)

    def same_row(a: str, b: str) -> bool:  # beide senkrecht zu mehr als der Hälfte des kleineren überdeckt
        (sa, _, ta, _, ha), (sb, _, tb, _, hb) = now[a], now[b]
        return sa == sb and min(ta + ha, tb + hb) - max(ta, tb) > min(ha, hb) // 2

    rows: list[list[str]] = []  # Zeile = Felder, die sich ALLE gegenseitig senkrecht überdecken
    for name in sorted(now, key=lambda n: (now[n][0], now[n][2])):
        target = next((row for row in rows if all(same_row(name, m) for m in row)), None)
        if target is None:
            rows.append([name])
        else:
            target.append(name)

    def overlap(a: str, b: str) -> bool:
        return min(now[a][1] + now[a][3], now[b][1] + now[b][3]) - max(now[a][1], now[b][1]) > _GAP // 2

    # Beschriftung im Seitenkopf zu einem Detailfeld (gleiche Lage oder Access-Namensschema): wandert mit dem Feld
    detail = {n for n, g in now.items() if g[0] == c.AC_DETAIL}
    partner: dict[str, str] = {}
    for n, g in now.items():
        if g[0] == c.AC_PAGE_HEADER:
            base = n.removesuffix("_bezeichnungsfeld").removeprefix("lbl")
            box = base if base in detail and base != n else next(
                (d for d in detail if abs(now[d][1] - g[1]) <= 30 and d not in partner.values()), None)
            if box:
                partner[n] = box
    paired = {v: k for k, v in partner.items()}
    right_edge = 0
    for row in sorted(rows, key=lambda r: now[r[0]][0] != c.AC_DETAIL):  # Detailbereich zuerst
        members = [n for n in row if n not in partner]  # Kopf-Beschriftungen mit Partner folgen ihrem Feld
        clash = any(overlap(a, b) for i, a in enumerate(members) for b in members[i + 1:])
        relevant = set(row) | {paired[n] for n in row if n in paired}
        if not clash or not relevant & touched:
            right_edge = max([right_edge] + [now[n][1] + now[n][3] for n in row])
            continue
        left = min([now[n][1] for n in members] + [before[n][1] for n in members if n in before])
        for n in sorted(members, key=lambda n: (now[n][1], n in touched)):
            width = max(now[n][3], now[paired[n]][3]) if n in paired else now[n][3]
            for name in ([n, paired[n]] if n in paired else [n]):
                obj.Controls(name).Left = left
                now[name] = (now[name][0], left, *now[name][2:])
            left += width + _GAP
        right_edge = max(right_edge, left - _GAP)
    with contextlib.suppress(Exception):
        if right_edge > int(obj.Width):
            obj.Width = right_edge


def _access_color(hex_color: str) -> int:
    """"#RRGGBB" → Access-Farbwert (BGR als Zahl)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return r + g * 256 + b * 65536


_SECTION_INDEX = {"detail": c.AC_DETAIL, "report_header": c.AC_HEADER, "report_footer": c.AC_FOOTER,
                  "page_header": c.AC_PAGE_HEADER, "page_footer": c.AC_PAGE_FOOTER}
_SECTION_LABELS = {c.AC_DETAIL: "Detailbereich", c.AC_HEADER: "Kopf", c.AC_FOOTER: "Fuß",
                   c.AC_PAGE_HEADER: "Seitenkopf", c.AC_PAGE_FOOTER: "Seitenfuß"}


def section_index(name: str, group: int = 0) -> int:
    """Access-Bereichsnummer: detail 0, Kopf/Fuß 1/2, Seitenkopf/-fuß 3/4, Gruppenkopf/-fuß 5+2n/6+2n."""
    if name == "group_header":
        return c.AC_GROUP_LEVEL1_HEADER + 2 * group
    if name == "group_footer":
        return c.AC_GROUP_LEVEL1_FOOTER + 2 * group
    return _SECTION_INDEX[name]


def color_hex(value: Any) -> str:
    """Access-Farbwert (BGR) → "#RRGGBB"; negative Werte sind System-/Designfarben."""
    number = int(value)
    if number < 0:
        return "Systemfarbe"
    return f"#{number & 0xFF:02X}{(number >> 8) & 0xFF:02X}{(number >> 16) & 0xFF:02X}"


def _apply_section_colors(obj: Any, colors: list[Any]) -> None:
    for spec in colors:
        try:
            area = section(obj, section_index(spec.section, spec.group))
        except Exception as exc:
            raise AccessError(f"Den Bereich „{spec.section}“ gibt es in „{obj.Name}“ nicht.") from exc
        if spec.back_color:
            area.BackColor = _access_color(spec.back_color)
        if spec.alternate_back_color:
            area.AlternateBackColor = _access_color(spec.alternate_back_color)


def section_colors(obj: Any) -> dict[int, tuple[str, str]]:
    """Bereichsnummer → (Hintergrund, alternativer Hintergrund) aller vorhandenen Bereiche."""
    result: dict[int, tuple[str, str]] = {}
    for index in range(0, 25):
        try:
            area = section(obj, index)
            back = color_hex(area.BackColor)
        except Exception:
            continue
        try:  # Seitenkopf/-fuß haben keine alternative Farbe – der Hintergrund zählt trotzdem
            alternate = color_hex(area.AlternateBackColor)
        except Exception:
            alternate = "(nicht verfügbar)"
        result[index] = (back, alternate)
    return result


def describe_section_colors(obj: Any) -> str:
    """„Detailbereich: Hintergrund #FFFFFF, alternativ #F2F2F2; …“ – für Schema, Prüfung und Bericht."""
    parts = []
    for index, (back, alternate) in section_colors(obj).items():
        label = _SECTION_LABELS.get(index) or (f"Gruppenkopf {(index - 5) // 2 + 1}" if index % 2 else
                                              f"Gruppenfuß {(index - 6) // 2 + 1}")
        parts.append(f"{label}: Hintergrund {back}, alternativ {alternate}")
    return "; ".join(parts)


def _apply_format(control: Any, spec: ControlSpec) -> None:
    """Schrift und Farben eines Steuerelements (fett, kursiv, Größe, Schrift-/Hintergrundfarbe)."""
    if spec.bold is not None:
        control.FontBold = spec.bold
    if spec.italic is not None:
        control.FontItalic = spec.italic
    if spec.font_size is not None:
        control.FontSize = spec.font_size
    if spec.fore_color:
        control.ForeColor = _access_color(spec.fore_color)
    if spec.back_color:
        control.BackStyle = 1  # „Normal“ – sonst ist die Hintergrundfarbe unsichtbar (transparent)
        control.BackColor = _access_color(spec.back_color)


def _describe_condition(fc: Any) -> str:
    kind = int(fc.Type)
    if kind == c.AC_EXPRESSION:
        text = f"Ausdruck {fc.Expression1}"
    elif kind == c.AC_FIELD_VALUE:
        text = f"Feldwert {_OPERATOR_NAMES.get(int(fc.Operator), fc.Operator)} {fc.Expression1}"
        with contextlib.suppress(Exception):
            if fc.Expression2:
                text += f" und {fc.Expression2}"
    elif kind == c.AC_DATA_BAR:
        limits = {0: "automatisch", 1: "Zahl", 2: "Prozent"}
        text = (f"Datenbalken kürzester {limits.get(int(fc.ShortestBarLimit), '?')} {fc.ShortestBarValue}, "
                f"längster {limits.get(int(fc.LongestBarLimit), '?')} {fc.LongestBarValue}"
                + (", nur Balken" if bool(fc.ShowBarOnly) else ""))
    else:
        text = f"Typ {kind}"
    parts = []
    with contextlib.suppress(Exception):
        back = int(fc.BackColor)
        parts.append(f"Hintergrund #{back & 255:02X}{(back >> 8) & 255:02X}{(back >> 16) & 255:02X}")
    with contextlib.suppress(Exception):
        fore = int(fc.ForeColor)
        parts.append(f"Schrift #{fore & 255:02X}{(fore >> 8) & 255:02X}{(fore >> 16) & 255:02X}")
    with contextlib.suppress(Exception):
        if fc.FontBold:
            parts.append("fett")
    return f"{text} → {', '.join(parts)}"


def _cm(value: float) -> int:
    return round(value * TW)


def _safe_control_name(base: str, used: set[str]) -> str:
    cleaned = "".join(ch for ch in base if ch not in ".![]`\"'") or "Steuerelement"
    candidate = cleaned[:60]
    counter = 2
    while candidate.lower() in used:
        candidate = f"{cleaned[:56]}_{counter}"
        counter += 1
    used.add(candidate.lower())
    return candidate


class FormReportOps:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    # ------------------------------------------------------------------ Namen
    def _document_names(self, container: str) -> list[str]:
        db = self._s.db
        try:
            with com_guard("Objektliste lesen"):
                db.Containers.Refresh()
                docs = db.Containers(container).Documents
                docs.Refresh()
                return sorted((str(d.Name) for d in docs if not str(d.Name).startswith("~")), key=str.lower)
        except ObjectNotFoundError:
            # Rein per DAO erzeugte Dateien besitzen den Container erst, nachdem Access sie geöffnet hat
            return []

    def form_names(self) -> list[str]:
        return self._document_names("Forms")

    def report_names(self) -> list[str]:
        return self._document_names("Reports")

    def form_exists(self, name: str) -> bool:
        return name.lower() in (n.lower() for n in self.form_names())

    def report_exists(self, name: str) -> bool:
        return name.lower() in (n.lower() for n in self.report_names())

    def _require_source(self, source: str) -> None:
        db = self._s.db
        db.TableDefs.Refresh()
        db.QueryDefs.Refresh()
        if source.strip().upper().startswith("SELECT "):
            return
        if not (collection_has(db.TableDefs, source) or collection_has(db.QueryDefs, source)):
            raise ObjectNotFoundError("Datenquelle (Tabelle/Abfrage)", source)

    def _source_fields(self, source: str) -> list[str]:
        db = self._s.db
        if source.strip().upper().startswith("SELECT "):
            temp = db.CreateQueryDef("", source)
            try:
                return [str(f.Name) for f in temp.Fields]
            finally:
                with contextlib.suppress(Exception):
                    temp.Close()
        if collection_has(db.TableDefs, source):
            return [str(f.Name) for f in db.TableDefs(source).Fields]
        return [str(f.Name) for f in db.QueryDefs(source).Fields]

    def _check_fields(self, source: str, fields: list[str], what: str) -> None:
        available = {f.lower() for f in self._source_fields(source)}
        missing = [f for f in fields if f and not f.startswith("=") and f.lower() not in available]
        if missing:
            raise ObjectNotFoundError(f"Feld in {what}", f"{source}: {', '.join(missing)}")

    def _field_dao_type(self, source: str, field: str) -> int | None:
        db = self._s.db
        with contextlib.suppress(Exception):
            if collection_has(db.TableDefs, source):
                return int(db.TableDefs(source).Fields(field).Type)
            return int(db.QueryDefs(source).Fields(field).Type)
        return None

    # ------------------------------------------------------------------ Formulare
    def create_form(self, spec: FormSpec, *, replace: bool = False) -> str:
        if spec.record_source:
            self._require_source(spec.record_source)
        # Reihenfolge der Kurzform-Felder; ein Feld, das zusätzlich als ausdrückliches Steuerelement (Beschriftung,
        # Formatierung) kommt, wird nur EINMAL angelegt – an seiner Stelle. Übrige Steuerelemente folgen danach.
        controls: list[ControlSpec] = []
        explicit = {ctl.field.lower(): ctl for ctl in spec.controls if ctl.field and not ctl.field.startswith("=")}
        placed: set[int] = set()
        for field in spec.fields:
            if field.lower() in explicit:
                ctl = explicit[field.lower()]
                if id(ctl) not in placed:
                    controls.append(ctl)
                    placed.add(id(ctl))
                continue
            kind = "text_box"
            if spec.record_source and self._field_dao_type(spec.record_source, field) == c.DB_BOOLEAN:
                kind = "check_box"
            controls.append(ControlSpec(type=kind, field=field))  # type: ignore[arg-type]
        controls.extend(ctl for ctl in spec.controls if id(ctl) not in placed)
        for ctl in controls:
            if ctl.type == "subform" and ctl.source_object and "." not in ctl.source_object \
                    and not self.form_exists(ctl.source_object):
                raise ObjectNotFoundError("Formular für das Unterformular", ctl.source_object)
        if spec.record_source:
            bound = [ctl.field for ctl in controls if ctl.field and not ctl.field.startswith("=")]
            self._check_fields(spec.record_source, bound, "Formular")
        exists = self.form_exists(spec.name)
        if exists and not replace:
            raise ObjectExistsError("Formular", spec.name)
        app = self._s.application()
        temp_name = ""
        with com_guard(f"Formular „{spec.name}“ erstellen"):
            app.Echo(False)
            try:
                form = app.CreateForm()
                temp_name = str(form.Name)
                if spec.record_source:
                    form.RecordSource = spec.record_source
                form.Caption = spec.caption or spec.name
                form.DefaultView = _FORM_VIEWS[spec.default_view]
                for prop, value in spec.properties.items():
                    if prop not in _ALLOWED_FORM_PROPS:
                        raise AccessError(f"Die Formulareigenschaft „{prop}“ darf nicht automatisch gesetzt werden.")
                    setattr(form, _PERSISTENT_PROPS.get(prop, prop), value)
                used: set[str] = set()
                needs_header = bool(spec.title) or any(ctl.section in ("header", "footer") for ctl in controls)
                if needs_header:
                    self._ensure_form_header(app, form)
                if spec.title:
                    label = app.CreateControl(
                        temp_name, c.AC_LABEL, c.AC_HEADER, "", "", _cm(0.5), _cm(0.3), _cm(12), _cm(0.9)
                    )
                    label.Caption = spec.title
                    label.Name = _safe_control_name("lblTitel", used)
                    with contextlib.suppress(Exception):
                        label.FontSize = 18
                        label.FontBold = True
                        section(form, c.AC_HEADER).Height = _cm(1.5)
                created = self._layout_controls(app, temp_name, controls, used, continuous=spec.default_view == "continuous")
                if spec.layout == "stacked":
                    self._apply_stacked_layout(app, form, temp_name, created, spec)
                app.DoCmd.Save(c.AC_FORM, temp_name)
                app.DoCmd.Close(c.AC_FORM, temp_name, c.AC_SAVE_NO)
                if exists:
                    app.DoCmd.DeleteObject(c.AC_FORM, spec.name)
                if temp_name.lower() != spec.name.lower():
                    app.DoCmd.Rename(spec.name, c.AC_FORM, temp_name)
                temp_name = ""
            finally:
                if temp_name:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_FORM, temp_name, c.AC_SAVE_NO)
                with contextlib.suppress(Exception):
                    app.Echo(True)
        self._s.refresh_db()
        self._s.refresh_navigation()
        log.info("Formular erstellt: %s", spec.name)
        return spec.name

    @staticmethod
    def _ensure_form_header(app: Any, form: Any) -> None:
        if has_section(form, c.AC_HEADER):
            return
        with contextlib.suppress(Exception):
            app.DoCmd.SelectObject(c.AC_FORM, form.Name, False)
        app.RunCommand(c.AC_CMD_FORM_HDR_FTR)

    def _layout_controls(
        self, app: Any, form_name: str, controls: list[ControlSpec], used: set[str], *, continuous: bool
    ) -> list[tuple[ControlSpec, list[str]]]:
        """Legt die Steuerelemente an; liefert je Spezifikation die Namen (Feld + Beschriftung)."""
        created: list[tuple[ControlSpec, list[str]]] = []
        base_label_left, base_control_left = _cm(0.5), _cm(4.5)
        label_width, control_width = _cm(DEFAULT_LABEL_WIDTH_CM), _cm(DEFAULT_CONTROL_WIDTH_CM)
        row_height, gap = _cm(0.6), _cm(0.2)
        cursor: dict[tuple[int, int], int] = {}
        for ctl in controls:
            section = _SECTIONS[ctl.section]
            column = ctl.layout_column or 0  # rechte Feldspalte: eigene Beschriftungs-/Feldspalte daneben
            label_left, control_left = base_label_left + column * _cm(11), base_control_left + column * _cm(11)
            names: list[str] = []
            top = _cm(ctl.top_cm) if ctl.top_cm is not None else cursor.get((section, column), _cm(0.3))
            left = _cm(ctl.left_cm) if ctl.left_cm is not None else control_left
            width = _cm(ctl.width_cm) if ctl.width_cm is not None else control_width
            height = _cm(ctl.height_cm) if ctl.height_cm is not None else row_height
            kind = _CONTROL_TYPES[ctl.type]
            bound_field = ctl.field if ctl.field and not ctl.field.startswith("=") else ""
            if ctl.type == "label":
                left = _cm(ctl.left_cm) if ctl.left_cm is not None else label_left
                control = app.CreateControl(form_name, kind, section, "", "", left, top, width, height)
                control.Caption = ctl.caption or ctl.field or "Bezeichnung"
                control.Name = _safe_control_name(ctl.name or f"lbl{ctl.caption or 'Text'}", used)
            elif ctl.type == "subform":
                width = _cm(ctl.width_cm or 14)
                height = _cm(ctl.height_cm or 6)
                control = app.CreateControl(form_name, c.AC_SUBFORM, section, "", "", left if ctl.left_cm is not None
                                            else label_left, top, width, height)
                control.SourceObject = ctl.source_object
                if ctl.link_master_fields:
                    control.LinkMasterFields = ";".join(ctl.link_master_fields)
                    control.LinkChildFields = ";".join(ctl.link_child_fields)
                control.Name = _safe_control_name(ctl.name or f"sub{(ctl.source_object or '').split('.')[-1]}", used)
            elif ctl.type == "button":
                control = app.CreateControl(form_name, kind, section, "", "", left, top, _cm(ctl.width_cm or 3.5), _cm(ctl.height_cm or 0.8))
                control.Caption = ctl.caption or "Schaltfläche"
                control.Name = _safe_control_name(ctl.name or f"btn{ctl.caption or 'Aktion'}", used)
                height = _cm(ctl.height_cm or 0.8)
            else:
                if ctl.type == "check_box":
                    width = _cm(ctl.width_cm) if ctl.width_cm is not None else _cm(0.5)
                control = app.CreateControl(form_name, kind, section, "", bound_field, left, top, width, height)
                if ctl.field and ctl.field.startswith("="):
                    control.ControlSource = ctl.field
                control.Name = _safe_control_name(ctl.name or bound_field or ctl.caption or ctl.type, used)
                if ctl.type in ("combo_box", "list_box") and ctl.row_source:
                    control.RowSourceType = "Table/Query"
                    control.RowSource = ctl.row_source
                if ctl.with_label:
                    # die Beschriftung steht immer links neben IHREM Feld – auch wenn das Feld verschoben ist
                    own_left = label_left if ctl.left_cm is None else max(0, left - label_width - _cm(0.2))
                    label = app.CreateControl(
                        form_name, c.AC_LABEL, section, control.Name, "", own_left, top, label_width, height
                    )
                    label.Caption = ctl.caption or bound_field or control.Name
                    label.Name = _safe_control_name(f"lbl{control.Name}", used)
                    names.append(str(label.Name))
            _apply_format(control, ctl)
            names.insert(0, str(control.Name))
            created.append((ctl, names))
            if ctl.top_cm is None:
                cursor[(section, column)] = top + height + gap
        del continuous
        return created

    @staticmethod
    def _apply_stacked_layout(app: Any, form: Any, form_name: str, created: list[tuple[ControlSpec, list[str]]],
                              spec: FormSpec) -> None:
        """Steuerelementlayout wie „Erstellen > Formular“, je Feldspalte ein gestapeltes Layout; danach Spaltenbreiten
        und verbundene Zellen. Die Layoutbefehle verlangen ein normal geöffnetes Formular – Access selbst bleibt
        unsichtbar, es gibt keinen Fokuswechsel."""
        app.DoCmd.SelectObject(c.AC_FORM, form_name, False)

        def select(names: list[str]) -> None:
            for ctl in form.Controls:
                ctl.InSelection = str(ctl.Name) in names

        groups: dict[int, list[str]] = {}
        for ctl_spec, names in created:
            if ctl_spec.section == "detail" and ctl_spec.type not in ("subform", "button"):
                groups.setdefault(ctl_spec.layout_column or 0, []).extend(names)
        for names in groups.values():
            select(names)
            app.DoCmd.RunCommand(c.AC_CMD_STACKED_LAYOUT)
        # Breite eines Felds = Breite der ganzen Layoutspalte. Je Feldspalte (Layout) zwei Spalten:
        # column_widths_cm[0:2] = linkes Layout (Beschriftung, Feld), [2:4] = rechtes Layout
        for column, group in sorted(groups.items()):
            for index, width in enumerate(spec.column_widths_cm[column * 2:column * 2 + 2]):
                # nach jeder Änderung neu lesen: eine schmalere Spalte verschiebt die Spalten rechts davon
                lefts = sorted({int(form.Controls(n).Left) for n in group})
                if index >= len(lefts):
                    break
                sample = next(n for n in group if int(form.Controls(n).Left) == lefts[index])
                form.Controls(sample).Width = _cm(width)
        names_by_field = {(s.field or "").lower(): names[0] for s, names in created if s.field}
        for field, extra in spec.span_right.items():
            name = names_by_field.get(field.lower())
            if name is None:
                raise ObjectNotFoundError("Feld im Formular", field)
            target = form.Controls(name)
            for _ in range(extra):  # leere Zellen rechts daneben schaffen …
                select([name])
                app.DoCmd.RunCommand(c.AC_CMD_LAYOUT_INSERT_COLUMN_RIGHT)
            row = sorted((x for x in form.Controls if int(x.ControlType) == c.AC_EMPTY_CELL
                          and int(x.Top) == int(target.Top) and int(x.Left) > int(target.Left)), key=lambda x: int(x.Left))
            select([name, *(str(x.Name) for x in row[:extra])])
            app.DoCmd.RunCommand(c.AC_CMD_LAYOUT_MERGE_CELLS)  # … und mit dem Feld verbinden

    # ------------------------------------------------------------------ Berichte
    def create_report(self, spec: ReportSpec, *, replace: bool = False) -> str:
        self._require_source(spec.record_source)
        needed = [*spec.fields, *(g.field for g in spec.group_by), *(s.field for s in spec.sort_by)]
        needed += [t.field for t in spec.totals if t.field != "*"]
        self._check_fields(spec.record_source, needed, "Bericht")
        exists = self.report_exists(spec.name)
        if exists and not replace:
            raise ObjectExistsError("Bericht", spec.name)
        app = self._s.application()
        temp_name = ""
        with com_guard(f"Bericht „{spec.name}“ erstellen"):
            app.Echo(False)
            try:
                report = app.CreateReport()
                temp_name = str(report.Name)
                report.RecordSource = spec.record_source
                report.Caption = spec.caption or spec.title or spec.name
                landscape = spec.orientation == "landscape"
                if landscape:
                    with contextlib.suppress(Exception):
                        report.Printer.Orientation = 2
                page = spec.page_setup
                usable = _cm(25.0 if landscape or (page is not None and page.orientation == "landscape") else 16.5)
                if spec.labels:
                    usable = _tw_mm(spec.labels.mm()[0])
                elif page is not None and page.columns and page.columns > 1:
                    # mehrspaltig: jede Spalte so breit wie der Bericht – alle Spalten samt Abstand müssen auf die Seite
                    gap = _cm(page.column_spacing_cm if page.column_spacing_cm is not None else 0.6)
                    usable = (usable - gap * (page.columns - 1)) // page.columns
                with contextlib.suppress(Exception):
                    report.Width = usable
                used: set[str] = set()
                group_fields = [g.field.lower() for g in spec.group_by]
                detail_fields = [f for f in spec.fields if f.lower() not in group_fields] or list(spec.fields)
                margin = _cm(0.3)
                col_width = min(_cm(4.5), (usable - 2 * margin) // max(len(detail_fields), 1))
                positions = {
                    field.lower(): margin + index * col_width for index, field in enumerate(detail_fields)
                }
                row = _cm(0.55)

                # Gruppierungen und Sortierung (Reihenfolge = Ebenen)
                group_levels: list[tuple[int, Any]] = []
                for group in spec.group_by:
                    level = int(app.CreateGroupLevel(temp_name, f"[{group.field}]", group.header, group.footer))
                    group_level(report, level).SortOrder = group.sort == "DESC"
                    group_levels.append((level, group))
                for sort in spec.sort_by:
                    level = int(app.CreateGroupLevel(temp_name, f"[{sort.field}]", False, False))
                    group_level(report, level).SortOrder = sort.direction == "DESC"

                # Berichtskopf/-fuß
                has_report_totals = any(t.scope in ("report", "both") for t in spec.totals)
                report_header = bool(spec.title) or has_report_totals
                if report_header:
                    self._ensure_report_header(app, report)
                if spec.title:
                    title = app.CreateReportControl(
                        temp_name, c.AC_LABEL, c.AC_HEADER, "", "", margin, _cm(0.2), usable - 2 * margin, _cm(1.0)
                    )
                    title.Caption = spec.title
                    title.Name = _safe_control_name("lblTitel", used)
                    with contextlib.suppress(Exception):
                        title.FontSize = 18
                        title.FontBold = True
                    with contextlib.suppress(Exception):
                        section(report, c.AC_HEADER).Height = _cm(1.4)

                # Seitenkopf: Spaltenüberschriften (im Spaltenlayout stehen die Beschriftungen im Detailbereich)
                for field in detail_fields if spec.layout == "tabular" and not spec.labels else []:
                    label = app.CreateReportControl(
                        temp_name, c.AC_LABEL, c.AC_PAGE_HEADER, "", "",
                        positions[field.lower()], _cm(0.1), col_width - _cm(0.1), row,
                    )
                    label.Caption = field
                    label.Name = _safe_control_name(f"lbl{field}", used)
                    with contextlib.suppress(Exception):
                        label.FontBold = True
                with contextlib.suppress(Exception):
                    section(report, c.AC_PAGE_HEADER).Height = 0 if spec.labels else _cm(0.8)

                # Detailbereich
                if spec.labels:
                    self._apply_labels(report, spec.labels)
                elif spec.layout == "columnar":
                    # Beschriftung + Feld passen in die (bei mehrspaltigem Druck schmälere) Berichtsbreite
                    label_width = min(_cm(3.5), (usable - 2 * margin) * 2 // 5)
                    box_width = max(_cm(1.0), min(_cm(8.0), usable - 2 * margin - label_width - _cm(0.2)))
                    for index, field in enumerate(detail_fields):
                        top = _cm(0.1) + index * (row + _cm(0.1))
                        label = app.CreateReportControl(temp_name, c.AC_LABEL, c.AC_DETAIL, "", "", margin, top,
                                                        label_width, row)
                        label.Caption = field
                        label.Name = _safe_control_name(f"lbl{field}", used)
                        box = app.CreateReportControl(temp_name, c.AC_TEXT_BOX, c.AC_DETAIL, "", field,
                                                      margin + label_width + _cm(0.2), top, box_width, row)
                        box.Name = _safe_control_name(field, used)
                    with contextlib.suppress(Exception):
                        section(report, c.AC_DETAIL).Height = _cm(0.3) + len(detail_fields) * (row + _cm(0.1))
                else:
                    for field in detail_fields:
                        box = app.CreateReportControl(
                            temp_name, c.AC_TEXT_BOX, c.AC_DETAIL, "", field,
                            positions[field.lower()], 0, col_width - _cm(0.1), row,
                        )
                        box.Name = _safe_control_name(field, used)
                    with contextlib.suppress(Exception):
                        section(report, c.AC_DETAIL).Height = row + _cm(0.05)

                # Gruppenkopf/-fuß
                for level, group in group_levels:
                    header_section = c.AC_GROUP_LEVEL1_HEADER + 2 * level
                    footer_section = c.AC_GROUP_LEVEL1_FOOTER + 2 * level
                    if group.header:
                        caption = app.CreateReportControl(
                            temp_name, c.AC_LABEL, header_section, "", "", margin, _cm(0.1), _cm(3.0), row
                        )
                        caption.Caption = f"{group.field}:"
                        caption.Name = _safe_control_name(f"lblGruppe{group.field}", used)
                        with contextlib.suppress(Exception):
                            caption.FontBold = True
                        box = app.CreateReportControl(
                            temp_name, c.AC_TEXT_BOX, header_section, "", group.field,
                            margin + _cm(3.1), _cm(0.1), _cm(8.0), row,
                        )
                        box.Name = _safe_control_name(f"grp{group.field}", used)
                        with contextlib.suppress(Exception):
                            box.FontBold = True
                            section(report, header_section).Height = _cm(0.8)
                    if group.footer:
                        self._add_totals(
                            app, temp_name, footer_section,
                            [t for t in spec.totals if t.scope in ("group", "both")],
                            positions, col_width, row, used, prefix=f"g{level}", margin=margin,
                        )
                if has_report_totals:
                    self._add_totals(
                        app, temp_name, c.AC_FOOTER,
                        [t for t in spec.totals if t.scope in ("report", "both")],
                        positions, col_width, row, used, prefix="gesamt", margin=margin, grand=True,
                    )

                # Seitenfuß
                if spec.show_page_numbers and not spec.labels:
                    page = app.CreateReportControl(
                        temp_name, c.AC_TEXT_BOX, c.AC_PAGE_FOOTER, "", "",
                        usable - _cm(6.0) - margin, _cm(0.1), _cm(6.0), row,
                    )
                    page.ControlSource = '="Seite " & [Page] & " von " & [Pages]'
                    page.Name = _safe_control_name("txtSeite", used)
                    with contextlib.suppress(Exception):
                        page.TextAlign = 3
                if spec.show_date:
                    date_box = app.CreateReportControl(
                        temp_name, c.AC_TEXT_BOX, c.AC_PAGE_FOOTER, "", "", margin, _cm(0.1), _cm(5.0), row
                    )
                    date_box.ControlSource = "=Date()"
                    date_box.Name = _safe_control_name("txtDatum", used)
                with contextlib.suppress(Exception):
                    section(report, c.AC_PAGE_FOOTER).Height = 0 if spec.labels else _cm(0.8)

                # Seitenwechsel/Zusammenhalten je Gruppe, Detailbereich, Filter und Sortierung des Berichts
                for level, group in group_levels:
                    if group.keep_together:
                        group_level(report, level).KeepTogether = 1
                    if group.new_page:
                        if group.header:
                            section(report, c.AC_GROUP_LEVEL1_HEADER + 2 * level).ForceNewPage = 1  # vor Bereich
                        else:
                            section(report, c.AC_GROUP_LEVEL1_FOOTER + 2 * level).ForceNewPage = 2  # nach Bereich
                if spec.hide_detail:
                    section(report, c.AC_DETAIL).Visible = False
                if spec.filter:
                    report.Filter = spec.filter
                    report.FilterOnLoad = True
                if spec.order_by:
                    report.OrderBy = spec.order_by
                    report.OrderByOnLoad = True
                for ctl in spec.controls:
                    if spec.labels:  # Etikettenzeilen: ganze Etikettbreite, Höhe nach Schriftgröße
                        ctl = ctl.model_copy(update={
                            "width_cm": ctl.width_cm or max(1.0, usable / TW - 0.6),
                            "height_cm": ctl.height_cm or max(0.4, (ctl.font_size or 10) * 0.045)})
                    self._add_report_control(app, report, temp_name, ctl, [lvl for lvl, _ in group_levels], used)
                if spec.labels:
                    label_height = _tw_mm(spec.labels.mm()[1])
                    bottom = max((int(x.Top) + int(x.Height) for x in report.Controls), default=0)
                    right = max((int(x.Left) + int(x.Width) for x in report.Controls), default=0)
                    if right > usable:  # breiter als das Etikett → Access verschiebt die Spalten
                        raise AccessError(f"Die Zeilen ({right / TW:.1f} cm) sind breiter als das Etikett "
                                          f"({usable / TW:.1f} cm).")
                    if bottom > label_height:
                        raise AccessError(f"Die Zeilen ({bottom / TW:.1f} cm) passen nicht auf das Etikett "
                                          f"({label_height / TW:.1f} cm) – kleinere Schrift oder weniger Zeilen.")
                    section(report, c.AC_DETAIL).Height = label_height
                if spec.page_setup:
                    self._apply_page_setup(report, spec.page_setup)

                app.DoCmd.Save(c.AC_REPORT, temp_name)
                app.DoCmd.Close(c.AC_REPORT, temp_name, c.AC_SAVE_NO)
                if exists:
                    app.DoCmd.DeleteObject(c.AC_REPORT, spec.name)
                if temp_name.lower() != spec.name.lower():
                    app.DoCmd.Rename(spec.name, c.AC_REPORT, temp_name)
                temp_name = ""
            finally:
                if temp_name:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_REPORT, temp_name, c.AC_SAVE_NO)
                with contextlib.suppress(Exception):
                    app.Echo(True)
        self._s.refresh_db()
        self._s.refresh_navigation()
        log.info("Bericht erstellt: %s", spec.name)
        return spec.name

    def _add_report_control(self, app: Any, report: Any, temp_name: str, ctl: ReportControlSpec,
                            levels: list[int], used: set[str], *, form: bool = False, join_row: bool = False) -> None:
        """Ein zusätzliches Steuerelement in den gewünschten Bereich setzen; der Bereich wächst bei Bedarf mit.
        ``form=True``: dasselbe in einem Formular (report_header/-footer = Formularkopf/-fuß). ``join_row``: beim Ändern
        eines tabellarischen Berichts (alle Felder des Bereichs in EINER Zeile) kommt ein neues Feld mit Links-Angabe in
        diese Zeile statt darunter – Überdeckungen löst danach die Zeilen-Neuordnung."""
        if ctl.name and ctl.name.lower() in used:  # sonst entstünde still „Name_2“ neben dem alten Steuerelement
            raise ObjectExistsError("Steuerelement", f"{ctl.name} – vorhandene Steuerelemente mit control_changes ändern")
        create = app.CreateControl if form else app.CreateReportControl
        fixed = {"report_header": c.AC_HEADER, "report_footer": c.AC_FOOTER, "page_header": c.AC_PAGE_HEADER,
                 "page_footer": c.AC_PAGE_FOOTER, "detail": c.AC_DETAIL}
        if ctl.section in fixed:
            target = fixed[ctl.section]
        else:
            base = c.AC_GROUP_LEVEL1_HEADER if ctl.section == "group_header" else c.AC_GROUP_LEVEL1_FOOTER
            target = base + 2 * levels[ctl.group]
        if ctl.section in ("report_header", "report_footer"):
            (self._ensure_form_header if form else self._ensure_report_header)(app, report)
        left = _cm(ctl.left_cm if ctl.left_cm is not None else 0.3)
        row = [(int(x.Top), int(x.Height)) for x in report.Controls
               if int(x.Section) == target and int(x.ControlType) in _LAYOUT_TYPES]
        single_row = bool(row) and all(min(t + h, row[0][0] + row[0][1]) - max(t, row[0][0]) > _GAP // 2 for t, h in row)
        if ctl.top_cm is not None:
            top = _cm(ctl.top_cm)
        elif join_row and single_row and ctl.left_cm is not None and ctl.type != "line":
            top = row[0][0]  # in die vorhandene Feldzeile (tabellarisch)
        else:  # ohne Angabe unter alles, was schon im Bereich steht – nichts überdeckt sich
            bottoms = [int(x.Top) + int(x.Height) for x in report.Controls if int(x.Section) == target]
            top = max(bottoms, default=0) + _cm(0.1)
        height = 0 if ctl.type == "line" else _cm(ctl.height_cm or (0.6 if not ctl.font_size or ctl.font_size <= 12
                                                                   else ctl.font_size / 20))
        if join_row and single_row and ctl.top_cm is None and ctl.height_cm is None and ctl.type != "line" \
                and ctl.left_cm is not None:
            height = row[0][1]  # gleiche Zeilenhöhe wie die übrigen Felder
        width = _cm(ctl.width_cm or (16.0 if ctl.type == "line" else 4.0))
        if ctl.type == "text_box" and ctl.caption:
            label = create(temp_name, c.AC_LABEL, target, "", "", left, top, _cm(3.0), height)
            label.Caption = ctl.caption
            label.Name = _safe_control_name(f"lbl{ctl.name or ctl.caption}", used)
            left += _cm(3.1)
        kind = {"text_box": c.AC_TEXT_BOX, "label": c.AC_LABEL, "line": c.AC_LINE}[ctl.type]
        obj = create(temp_name, kind, target, "", "", left, top, width, height)
        if ctl.type == "text_box":
            obj.ControlSource = ctl.expression
        elif ctl.type == "label":
            obj.Caption = ctl.caption
        obj.Name = _safe_control_name(ctl.name or {"text_box": "txtFeld", "label": "lblText", "line": "Linie"}[ctl.type], used)
        if ctl.bold is not None and ctl.type != "line":
            obj.FontBold = ctl.bold
        if ctl.font_size and ctl.type != "line":
            obj.FontSize = ctl.font_size
        if ctl.format and ctl.type == "text_box":
            obj.Format = ctl.format
        area = section(report, target)
        if int(area.Height) < top + height + _cm(0.1):
            area.Height = top + height + _cm(0.1)

    @staticmethod
    def _ensure_report_header(app: Any, report: Any) -> None:
        if has_section(report, c.AC_HEADER):
            return
        with contextlib.suppress(Exception):
            app.DoCmd.SelectObject(c.AC_REPORT, report.Name, False)
        app.RunCommand(c.AC_CMD_REPORT_HDR_FTR)

    @staticmethod
    def _add_totals(
        app: Any,
        report_name: str,
        section: int,
        totals: list[ReportTotalSpec],
        positions: dict[str, int],
        col_width: int,
        row: int,
        used: set[str],
        *,
        prefix: str,
        margin: int,
        grand: bool = False,
    ) -> None:
        free_left = margin
        line = 0
        for total in totals:
            func = _AGG_FUNCS[total.function]
            source = f"={func}(*)" if total.field == "*" else f"={func}([{total.field}])"
            left = positions.get(total.field.lower())
            top = _cm(0.1) + line * (row + _cm(0.05))
            label_text = total.label or (
                f"{'Gesamt' if grand else ''}{_AGG_LABELS[total.function].lower() if grand else _AGG_LABELS[total.function]}"
                f" {total.field if total.field != '*' else ''}".strip() + ":"
            )
            if left is None or left < margin + _cm(3.0):
                # kein Platz links neben der Spalte -> Beschriftung und Wert nebeneinander am Zeilenanfang
                label = app.CreateReportControl(
                    report_name, c.AC_LABEL, section, "", "", free_left, top, _cm(4.5), row
                )
                box_left = free_left + _cm(4.6)
                line += 1
            else:
                label_left = max(margin, left - _cm(4.6))
                label = app.CreateReportControl(
                    report_name, c.AC_LABEL, section, "", "", label_left, top, _cm(4.5), row
                )
                box_left = left
                with contextlib.suppress(Exception):
                    label.TextAlign = 3
                line += 1
            label.Caption = label_text
            label.Name = _safe_control_name(f"lbl{prefix}{func}{total.field.strip('*') or 'Alle'}", used)
            box = app.CreateReportControl(
                report_name, c.AC_TEXT_BOX, section, "", "", box_left, top, col_width - _cm(0.1), row
            )
            box.ControlSource = source
            box.Name = _safe_control_name(f"{prefix}{func}{total.field.strip('*') or 'Alle'}", used)
            if total.format:
                with contextlib.suppress(Exception):
                    box.Format = total.format
            with contextlib.suppress(Exception):
                box.FontBold = True

    # ------------------------------------------------------------------ Bedingte Formatierung
    def set_conditional_format(self, kind: str, name: str, control: str, rules: list[FormatRuleSpec],
                               *, replace: bool = False) -> int:
        """Setzt Regeln der bedingten Formatierung eines Text-/Kombinationsfelds. ``replace`` löscht vorhandene
        Regeln dieses Steuerelements, sonst werden die neuen angehängt (Access erlaubt höchstens 50)."""
        object_type, exists = self._kind(kind)
        if not exists(name):
            raise ObjectNotFoundError(kind.capitalize(), name)
        app = self._s.application()
        with com_guard(f"Bedingte Formatierung in „{name}“"):
            obj = self._open_design(app, object_type, name)
            saved = False
            try:
                if not collection_has(obj.Controls, control):
                    names = ", ".join(str(ctl.Name) for ctl in obj.Controls)[:300]
                    raise ObjectNotFoundError("Steuerelement", f"{name}.{control} (vorhanden: {names})")
                target = obj.Controls(control)
                conditions = target.FormatConditions
                if replace:
                    conditions.Delete()
                if int(conditions.Count) + len(rules) > 50:
                    raise AccessError("Access erlaubt höchstens 50 Formatierungsregeln je Steuerelement.")
                for rule in rules:
                    if rule.data_bar:
                        fc = conditions.Add(c.AC_DATA_BAR, 0, "0")  # gemessen: Add braucht einen Ausdruck
                        for side, value in (("Shortest", rule.shortest_value), ("Longest", rule.longest_value)):
                            if value is not None:
                                setattr(fc, f"{side}BarLimit", 1)  # acNumber
                                setattr(fc, f"{side}BarValue", str(value))
                        fc.ShowBarOnly = rule.show_bar_only
                        if rule.back_color:
                            fc.BackColor = _access_color(rule.back_color)
                        continue
                    if rule.expression:
                        fc = conditions.Add(c.AC_EXPRESSION, 0, rule.expression)
                    else:
                        op = _OPERATORS[rule.operator]  # type: ignore[index]
                        fc = conditions.Add(c.AC_FIELD_VALUE, op, rule.value, rule.value2) if rule.value2 is not None \
                            else conditions.Add(c.AC_FIELD_VALUE, op, rule.value)
                    if rule.fore_color:
                        fc.ForeColor = _access_color(rule.fore_color)
                    if rule.back_color:
                        fc.BackColor = _access_color(rule.back_color)
                    for attr, value in (("FontBold", rule.bold), ("FontItalic", rule.italic), ("FontUnderline", rule.underline)):
                        if value is not None:
                            setattr(fc, attr, value)
                count = int(conditions.Count)
                app.DoCmd.Save(object_type, name)
                saved = True
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(object_type, name, c.AC_SAVE_YES if saved else c.AC_SAVE_NO)
        log.info("Bedingte Formatierung gesetzt: %s.%s (%d Regeln)", name, control, count)
        return count

    def modify_report(self, name: str, spec: ReportChangeSpec) -> None:
        """Vorhandenen Bericht im Entwurf ändern und speichern (unsichtbar, ohne Fokuswechsel)."""
        if not self.report_exists(name):
            raise ObjectNotFoundError("Bericht", name)
        app = self._s.application()
        with com_guard(f"Bericht „{name}“ ändern"):
            report = self._open_design(app, c.AC_REPORT, name)
            saved = False
            try:
                before = _geometry(report)  # Zustand vor der Änderung – für die Neuordnung überdeckter Zeilen
                levels: list[Any] = []
                while len(levels) < 10:
                    try:
                        levels.append(group_level(report, len(levels)))
                    except Exception:
                        break
                for group in spec.groups:
                    index = next((i for i, lvl in enumerate(levels)
                                  if str(lvl.ControlSource).strip("[]=").lower() == group.field.lower()), None)
                    if index is None:
                        index = int(app.CreateGroupLevel(name, f"[{group.field}]", group.header, group.footer))
                        levels = [group_level(report, i) for i in range(len(levels) + 1)]
                    level = levels[index]
                    level.SortOrder = group.sort == "DESC"
                    level.KeepTogether = 1 if group.keep_together else 0
                    if not group.new_page:  # „aus“: Seitenumbruch in Kopf UND Fuß entfernen
                        for part in (c.AC_GROUP_LEVEL1_HEADER, c.AC_GROUP_LEVEL1_FOOTER):
                            with contextlib.suppress(Exception):
                                section(report, part + 2 * index).ForceNewPage = 0
                    elif bool(level.GroupHeader):
                        section(report, c.AC_GROUP_LEVEL1_HEADER + 2 * index).ForceNewPage = 1
                    else:
                        section(report, c.AC_GROUP_LEVEL1_FOOTER + 2 * index).ForceNewPage = 2
                if spec.record_source is not None:
                    report.RecordSource = spec.record_source
                if spec.hide_detail is not None:
                    section(report, c.AC_DETAIL).Visible = not spec.hide_detail
                if spec.filter is not None:
                    report.Filter = spec.filter
                    report.FilterOnLoad = bool(spec.filter)
                if spec.order_by is not None:
                    report.OrderBy = spec.order_by
                    report.OrderByOnLoad = bool(spec.order_by)
                if spec.page_setup:
                    self._apply_page_setup(report, spec.page_setup)
                self._change_controls(app, report, name, [], spec.delete_controls, c.AC_REPORT)
                used = {str(x.Name).lower() for x in report.Controls}
                for new in spec.controls:
                    if new.section in ("group_header", "group_footer"):
                        if new.group >= len(levels):
                            raise AccessError(f"Der Bericht „{name}“ hat keine Gruppenebene {new.group + 1}.")
                        # GroupHeader/GroupFooter sind per Automation schreibgeschützt (gemessen, auch im offenen
                        # Entwurf). Aggregate rechnen im Gruppenkopf genauso je Gruppe → dorthin ausweichen.
                        heads = {"group_header": bool(levels[new.group].GroupHeader),
                                 "group_footer": bool(levels[new.group].GroupFooter)}
                        if not heads[new.section]:
                            other = "group_header" if new.section == "group_footer" else "group_footer"
                            if not heads[other]:
                                raise AccessError(f"Die Gruppe {new.group + 1} im Bericht „{name}“ hat weder Kopf noch "
                                                  "Fuß – bitte im Entwurf „Mit Kopfzeilenbereich“ aktivieren.")
                            new = new.model_copy(update={"section": other})
                    self._add_report_control(app, report, name, new, list(range(len(levels))), used, join_row=True)
                self._change_controls(app, report, name, spec.control_changes, [], c.AC_REPORT)
                touched = ({ch.name.lower() for ch in spec.control_changes}
                           | {k for k in _geometry(report) if k not in before})
                _reflow_overlapping_rows(report, before, touched)
                if spec.width_cm is not None:  # nach den Steuerelementen: schmäler geht nur, wenn sie hineinpassen
                    report.Width = _cm(spec.width_cm)
                _apply_section_colors(report, spec.section_colors)
                app.DoCmd.Save(c.AC_REPORT, name)
                saved = True
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(c.AC_REPORT, name, c.AC_SAVE_YES if saved else c.AC_SAVE_NO)
        log.info("Bericht geändert: %s", name)

    @staticmethod
    def _change_controls(app: Any, obj: Any, name: str, changes: list[ControlChange], deletes: list[str],
                         object_type: int) -> None:
        """Steuerelemente eines im Entwurf geöffneten Formulars/Berichts ändern bzw. entfernen."""
        for wanted in [*(ch.name for ch in changes), *deletes]:
            if not collection_has(obj.Controls, wanted):
                names = ", ".join(str(x.Name) for x in obj.Controls)[:300]
                raise ObjectNotFoundError("Steuerelement", f"{name}.{wanted} (vorhanden: {names})")
        for change in changes:
            ctl = obj.Controls(change.name)
            for attr, value in (("Left", change.left_cm), ("Top", change.top_cm), ("Width", change.width_cm),
                                ("Height", change.height_cm)):
                if value is not None:
                    setattr(ctl, attr, _cm(value))
            for attr, raw in (("Caption", change.caption), ("FontBold", change.bold), ("FontItalic", change.italic),
                              ("FontSize", change.font_size), ("Format", change.format), ("Visible", change.visible),
                              ("BorderStyle", change.border_style), ("BorderWidth", change.border_width)):
                if raw is not None:
                    setattr(ctl, attr, raw)
            for attr, color in (("ForeColor", change.fore_color), ("BorderColor", change.border_color)):
                if color:
                    setattr(ctl, attr, _access_color(color))
            if change.back_color:
                ctl.BackStyle = 1
                ctl.BackColor = _access_color(change.back_color)
            if change.transparent is not None:
                ctl.BackStyle = 0 if change.transparent else 1
            for attr, raw in (("Enabled", change.enabled), ("Locked", change.locked)):
                if raw is not None:
                    setattr(ctl, attr, raw)
            if change.control_source is not None:
                ctl.ControlSource = change.control_source
        delete = app.DeleteControl if object_type == c.AC_FORM else app.DeleteReportControl
        for wanted in deletes:
            delete(name, wanted)

    def _add_tab_control(self, app: Any, form: Any, name: str, spec: TabControlSpec) -> None:
        """Registersteuerelement anlegen; Felder und Unterformulare auf die Seiten setzen (gemessen: Steuerelemente
        entstehen per CreateControl mit dem Seitennamen als Parent)."""
        wanted = {f.lower() for page in spec.pages for f in page.fields}
        for page in spec.pages:
            if page.subform and not self.form_exists(page.subform):
                raise ObjectNotFoundError("Formular", page.subform)
        if spec.move_fields:  # die gebundenen Steuerelemente der Felder entfernen (ihre Beschriftung geht mit)
            for ctl in [x for x in form.Controls if str(getattr(x, "ControlSource", "") or "").lower() in wanted]:
                with contextlib.suppress(Exception):
                    app.DeleteControl(name, str(ctl.Name))
        if spec.top_cm is not None:
            top = _cm(spec.top_cm)
        else:
            bottoms = [int(x.Top) + int(x.Height) for x in form.Controls if int(x.Section) == c.AC_DETAIL]
            top = max(bottoms, default=0) + _cm(0.3)
        left, width, height = _cm(spec.left_cm), _cm(spec.width_cm), _cm(spec.height_cm)
        tab = app.CreateControl(name, c.AC_TAB_CONTROL, c.AC_DETAIL, "", "", left, top, width, height)
        while int(tab.Pages.Count) < len(spec.pages):
            tab.Pages.Add()
        while int(tab.Pages.Count) > len(spec.pages):
            tab.Pages.Remove()
        used = {str(x.Name).lower() for x in form.Controls}
        row = _cm(0.6)
        for index, page_spec in enumerate(spec.pages):
            page = tab.Pages(index)
            page.Caption = page_spec.caption
            inner_top = top + _cm(0.9)
            for number, field in enumerate(page_spec.fields):
                y = inner_top + number * (row + _cm(0.1))
                label = app.CreateControl(name, c.AC_LABEL, c.AC_DETAIL, str(page.Name), "", left + _cm(0.4), y,
                                          _cm(3.5), row)
                label.Caption = field
                label.Name = _safe_control_name(f"lbl{field}", used)
                box = app.CreateControl(name, c.AC_TEXT_BOX, c.AC_DETAIL, str(page.Name), field, left + _cm(4.1), y,
                                        min(_cm(8.0), width - _cm(4.5)), row)
                box.Name = _safe_control_name(field, used)
            if page_spec.subform:
                sub = app.CreateControl(name, c.AC_SUBFORM, c.AC_DETAIL, str(page.Name), "", left + _cm(0.4),
                                        inner_top, width - _cm(0.8), height - _cm(1.3))
                sub.SourceObject = page_spec.subform
                sub.Name = _safe_control_name(f"UF{page_spec.subform}", used)
                if page_spec.link_master_fields:
                    sub.LinkMasterFields = ";".join(page_spec.link_master_fields)
                    sub.LinkChildFields = ";".join(page_spec.link_child_fields or page_spec.link_master_fields)
        area = section(form, c.AC_DETAIL)
        if int(area.Height) < top + height + _cm(0.2):
            area.Height = top + height + _cm(0.2)

    def modify_form(self, name: str, spec: FormChangeSpec) -> None:
        """Vorhandenes Formular im Entwurf ändern und speichern (unsichtbar, ohne Fokuswechsel)."""
        if not self.form_exists(name):
            raise ObjectNotFoundError("Formular", name)
        bad = sorted(set(spec.properties) - _ALLOWED_FORM_PROPS)
        if bad:
            raise AccessError(f"Die Formulareigenschaft „{bad[0]}“ darf nicht automatisch gesetzt werden.")
        app = self._s.application()
        with com_guard(f"Formular „{name}“ ändern"):
            form = self._open_design(app, c.AC_FORM, name)
            saved = False
            try:
                if spec.caption is not None:
                    form.Caption = spec.caption
                if spec.record_source is not None:
                    form.RecordSource = spec.record_source
                if spec.default_view:
                    form.DefaultView = _FORM_VIEWS[spec.default_view]
                for prop, value in spec.properties.items():
                    setattr(form, _PERSISTENT_PROPS.get(prop, prop), value)
                self._change_controls(app, form, name, [], spec.delete_controls, c.AC_FORM)
                used = {str(x.Name).lower() for x in form.Controls}
                for new in spec.controls:
                    self._add_report_control(app, form, name, new, [], used, form=True)
                self._change_controls(app, form, name, spec.control_changes, [], c.AC_FORM)
                if spec.tab_control:
                    self._add_tab_control(app, form, name, spec.tab_control)
                _apply_section_colors(form, spec.section_colors)
                app.DoCmd.Save(c.AC_FORM, name)
                saved = True
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(c.AC_FORM, name, c.AC_SAVE_YES if saved else c.AC_SAVE_NO)
        log.info("Formular geändert: %s", name)

    @staticmethod
    def _open_design(app: Any, object_type: int, name: str) -> Any:
        if object_type == c.AC_FORM:
            app.DoCmd.OpenForm(name, c.AC_DESIGN, "", "", -1, c.AC_HIDDEN)
            return app.Forms(name)
        app.DoCmd.OpenReport(name, c.AC_DESIGN, "", "", c.AC_HIDDEN)
        return app.Reports(name)

    def conditional_formats(self, kind: str, name: str) -> dict[str, list[str]]:
        """Steuerelement → Beschreibung jeder Formatierungsregel (für Prüfung und KI)."""
        object_type, exists = self._kind(kind)
        if not exists(name):
            raise ObjectNotFoundError(kind.capitalize(), name)
        app = self._s.application()
        result: dict[str, list[str]] = {}
        with com_guard(f"Bedingte Formatierung von „{name}“ lesen"):
            obj = self._open_design(app, object_type, name)
            try:
                for ctl in obj.Controls:
                    with contextlib.suppress(Exception):
                        fcs = ctl.FormatConditions
                        rules = [_describe_condition(fcs.Item(i)) for i in range(int(fcs.Count))]
                        if rules:
                            result[str(ctl.Name)] = rules
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(object_type, name, c.AC_SAVE_NO)
        return result

    # ------------------------------------------------------------------ Navigationsformular
    def create_navigation_form(self, name: str, tabs: list[NavigationTabSpec], *, title: str | None = None,
                               replace: bool = False) -> str:
        """Navigationsformular wie „Erstellen > Navigation > Horizontale Registerkarten“: Navigationssteuerelement mit
        je einer Schaltfläche pro Ziel und dem Navigationsunterformular, das das Ziel anzeigt."""
        validate_name(name, "Formular")
        if not tabs:
            raise AccessError("Ein Navigationsformular braucht mindestens eine Registerkarte.")
        for tab in tabs:
            if not (self.form_exists(tab.target) if tab.target_type == "form" else self.report_exists(tab.target)):
                raise ObjectNotFoundError("Formular" if tab.target_type == "form" else "Bericht", tab.target)
        exists = self.form_exists(name)
        if exists and not replace:
            raise ObjectExistsError("Formular", name)
        app = self._s.application()
        temp_name = ""
        with com_guard(f"Navigationsformular „{name}“ erstellen"):
            app.Echo(False)
            try:
                form = app.CreateForm()
                temp_name = str(form.Name)
                form.Caption = title or name
                self._ensure_form_header(app, form)
                used: set[str] = set()
                if title:
                    label = app.CreateControl(temp_name, c.AC_LABEL, c.AC_HEADER, "", "", _cm(0.5), _cm(0.2), _cm(12), _cm(0.9))
                    label.Caption = title
                    label.Name = _safe_control_name("lblTitel", used)
                sub = app.CreateControl(temp_name, c.AC_SUBFORM, c.AC_DETAIL, "", "", _cm(0.3), _cm(1.2), _cm(18), _cm(10))
                sub.Name = "NavigationSubform"
                nav = app.CreateControl(temp_name, c.AC_NAVIGATION_CONTROL, c.AC_DETAIL, "", "", _cm(0.3), _cm(0.2),
                                        _cm(18), _cm(0.8))
                nav.Name = "NavigationControl0"
                with contextlib.suppress(Exception):
                    nav.SubForm = "NavigationSubform"
                for index, tab in enumerate(tabs):
                    button = app.CreateControl(temp_name, c.AC_NAVIGATION_BUTTON, c.AC_DETAIL, nav.Name, "",
                                               _cm(0.3 + 3.2 * index), _cm(0.2), _cm(3), _cm(0.8))
                    button.Caption = tab.caption
                    button.Name = _safe_control_name(f"NavigationButton{index + 1}", used)
                    button.NavigationTargetName = tab.target
                    with contextlib.suppress(Exception):
                        button.NavigationTargetType = c.AC_FORM if tab.target_type == "form" else c.AC_REPORT
                first = tabs[0]
                sub.SourceObject = first.target if first.target_type == "form" else f"Report.{first.target}"
                app.DoCmd.Save(c.AC_FORM, temp_name)
                app.DoCmd.Close(c.AC_FORM, temp_name, c.AC_SAVE_NO)
                if exists:
                    app.DoCmd.DeleteObject(c.AC_FORM, name)
                if temp_name.lower() != name.lower():
                    app.DoCmd.Rename(name, c.AC_FORM, temp_name)
                temp_name = ""
            finally:
                if temp_name:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_FORM, temp_name, c.AC_SAVE_NO)
                    with contextlib.suppress(Exception):
                        app.DoCmd.DeleteObject(c.AC_FORM, temp_name)
                with contextlib.suppress(Exception):
                    app.Echo(True)
        self._s.refresh_db()
        self._s.refresh_navigation()
        log.info("Navigationsformular erstellt: %s", name)
        return name

    # ------------------------------------------------------------------ Inspektion
    @staticmethod
    def _controls(obj: Any) -> list[ControlInfo]:
        result: list[ControlInfo] = []
        for control in obj.Controls:
            kind = int(control.ControlType)
            info = ControlInfo(
                name=str(control.Name), control_type=kind, type_name=c.CONTROL_TYPE_NAMES.get(kind, str(kind))
            )
            for attr, target in (
                ("ControlSource", "control_source"),
                ("Caption", "caption"),
                ("RowSource", "row_source"),
                ("SourceObject", "source_object"),
                ("LinkMasterFields", "link_master_fields"),
                ("LinkChildFields", "link_child_fields"),
                ("NavigationTargetName", "navigation_target"),
            ):
                with contextlib.suppress(Exception):
                    setattr(info, target, str(getattr(control, attr) or ""))
            for attr, target in (
                ("Section", "section"),
                ("Left", "left"),
                ("Top", "top"),
                ("Width", "width"),
                ("Height", "height"),
                ("Layout", "layout"),
                ("LayoutID", "layout_id"),
            ):
                with contextlib.suppress(Exception):
                    setattr(info, target, int(getattr(control, attr)))
            with contextlib.suppress(Exception):
                info.parent = str(control.Parent.Name)
            # Access liefert „Formular.frmX“/„Bericht.rptX“ (lokalisiert) – der Präfix ist der Zieltyp (gemessen:
            # NavigationTargetType liefert dafür keinen verlässlichen Wert), der Rest der Objektname
            prefix, _, name = info.navigation_target.rpartition(".")
            info.navigation_target = name
            info.navigation_kind = {"form": "form", "formular": "form", "report": "report", "bericht": "report"}.get(
                prefix.lower(), "") if name else ""
            parts = []
            with contextlib.suppress(Exception):
                if bool(control.FontBold):
                    parts.append("fett")
            with contextlib.suppress(Exception):
                if bool(control.FontItalic):
                    parts.append("kursiv")
            with contextlib.suppress(Exception):
                if not bool(control.Visible):
                    parts.append("unsichtbar")
            with contextlib.suppress(Exception):
                fore = int(control.ForeColor)
                if fore > 0 and fore != 0x404040:  # Schwarz bzw. #404040 (Access-Design) = Standard, negativ = System
                    parts.append(f"Schrift #{fore & 255:02X}{(fore >> 8) & 255:02X}{(fore >> 16) & 255:02X}")
            with contextlib.suppress(Exception):
                if int(control.BackStyle) == 1 and kind in (c.AC_TEXT_BOX, c.AC_LABEL, c.AC_COMBO_BOX):
                    back = int(control.BackColor)
                    if back >= 0 and back != 0xFFFFFF:  # negative Werte = Systemfarben (Standard)
                        parts.append(f"Hintergrund #{back & 255:02X}{(back >> 8) & 255:02X}{(back >> 16) & 255:02X}")
            if kind in (c.AC_TEXT_BOX, c.AC_COMBO_BOX):
                with contextlib.suppress(Exception):
                    if int(control.BackStyle) == 0:
                        parts.append("transparent")
                with contextlib.suppress(Exception):
                    if int(control.BorderStyle) == 0:
                        parts.append("ohne Rahmen")
                with contextlib.suppress(Exception):
                    if not bool(control.Enabled):
                        parts.append("nicht aktiviert")
                with contextlib.suppress(Exception):
                    if bool(control.Locked):
                        parts.append("gesperrt")
            info.formatting = ", ".join(parts)
            if kind in (c.AC_TEXT_BOX, c.AC_COMBO_BOX):
                with contextlib.suppress(Exception):
                    fcs = control.FormatConditions
                    info.conditions = [_describe_condition(fcs.Item(i)) for i in range(int(fcs.Count))]
            result.append(info)
        return result

    def inspect_form(self, name: str) -> FormInfo:
        if not self.form_exists(name):
            raise ObjectNotFoundError("Formular", name)
        app = self._s.application()
        with com_guard(f"Formular „{name}“ untersuchen"):
            already_open = self._is_loaded(app, "AllForms", name)
            if not already_open:
                app.DoCmd.OpenForm(name, c.AC_DESIGN, "", "", -1, c.AC_HIDDEN)
            try:
                form = app.Forms(name)
                info = FormInfo(
                    name=name,
                    record_source=str(form.RecordSource or ""),
                    caption=str(form.Caption or ""),
                    default_view=int(form.DefaultView),
                    controls=self._controls(form),
                    detailed=True,
                )
                for prop in ("NavigationButtons", "RecordSelectors", "AllowAdditions", "AllowDeletions", "AllowEdits",
                             "PopUp", "Modal", "SplitFormSplitterBar"):
                    with contextlib.suppress(Exception):
                        info.properties[prop] = bool(getattr(form, prop))
                for prop in ("Filter", "OrderBy"):  # nur wenn beim Öffnen aktiv
                    with contextlib.suppress(Exception):
                        if getattr(form, prop) and bool(getattr(form, f"{prop}OnLoad")):
                            info.properties[prop] = str(getattr(form, prop))
                with contextlib.suppress(Exception):
                    info.properties["Bereichsfarben"] = describe_section_colors(form)
                    info.section_colors = section_colors(form)
                return info
            finally:
                if not already_open:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_FORM, name, c.AC_SAVE_NO)

    def inspect_report(self, name: str) -> ReportInfo:
        if not self.report_exists(name):
            raise ObjectNotFoundError("Bericht", name)
        app = self._s.application()
        with com_guard(f"Bericht „{name}“ untersuchen"):
            already_open = self._is_loaded(app, "AllReports", name)
            if not already_open:
                app.DoCmd.OpenReport(name, c.AC_DESIGN, "", "", c.AC_HIDDEN)
            try:
                report = app.Reports(name)
                info = ReportInfo(
                    name=name,
                    record_source=str(report.RecordSource or ""),
                    caption=str(report.Caption or ""),
                    controls=self._controls(report),
                    detailed=True,
                )
                level = 0
                while level < 10:
                    try:
                        group = group_level(report, level)
                        info.group_levels.append(
                            GroupLevelInfo(
                                expression=str(group.ControlSource),
                                sort_descending=bool(group.SortOrder),
                                has_header=bool(group.GroupHeader),
                                has_footer=bool(group.GroupFooter),
                                keep_together=int(group.KeepTogether or 0) == 1,
                                new_page=self._force_new_page(report, level),
                            )
                        )
                    except Exception:
                        break
                    level += 1
                for key in ("Filter", "OrderBy"):
                    value = str(getattr(report, key, "") or "")
                    if value and bool(getattr(report, f"{key}OnLoad", False)):
                        info.properties[key] = value
                with contextlib.suppress(Exception):
                    if not bool(section(report, c.AC_DETAIL).Visible):
                        info.properties["Detailbereich"] = "ausgeblendet"
                with contextlib.suppress(Exception):
                    info.properties["Seite"] = _describe_page(self._printer_values(report), int(report.Width))
                with contextlib.suppress(Exception):
                    info.properties["Bereichsfarben"] = describe_section_colors(report)
                    info.section_colors = section_colors(report)
                return info
            finally:
                if not already_open:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_REPORT, name, c.AC_SAVE_NO)

    @staticmethod
    def _apply_page_setup(report: Any, spec: PageSetupSpec) -> None:
        """Einzeln auf ``report.Printer`` setzen: ein zurückgeschriebenes Printer-Objekt (``report.Printer = p``)
        verliert das Querformat (gemessen). Drucker von Access/Windows bleiben unverändert."""
        values = {"Orientation": None if spec.orientation is None else (2 if spec.orientation == "landscape" else 1),
                  "PaperSize": _PAPER.get(spec.paper or ""), "TopMargin": spec.margin_top_cm,
                  "BottomMargin": spec.margin_bottom_cm, "LeftMargin": spec.margin_left_cm,
                  "RightMargin": spec.margin_right_cm}
        for attr, value in values.items():
            if value is not None:
                setattr(report.Printer, attr, _cm(value) if attr.endswith("Margin") else value)
        if spec.columns is not None:  # „Spalten“: Breite wie Detailbereich (DefaultSize), Anzahl, Abstand, Richtung
            report.Printer.DefaultSize = True
            report.Printer.ItemsAcross = spec.columns
        if spec.column_spacing_cm is not None:
            report.Printer.ColumnSpacing = _cm(spec.column_spacing_cm)
        if spec.column_layout is not None:
            report.Printer.ItemLayout = ACROSS_THEN_DOWN if spec.column_layout == "across" else DOWN_THEN_ACROSS

    @staticmethod
    def _apply_labels(report: Any, labels: LabelSpec) -> None:
        """Etiketten wie der Etikettenassistent: mehrspaltiger Druck über ``report.Printer`` (A4 hoch)."""
        width, height, across, down, top, left, gap, row_gap = labels.mm()
        bottom = max(0.0, 297 - top - down * height - (down - 1) * row_gap - 0.5)
        right = max(0.0, 210 - left - across * width - (across - 1) * gap - 0.5)
        for attr, value in (("PaperSize", 9), ("Orientation", 1), ("DefaultSize", False), ("ItemsAcross", across),
                            ("ItemSizeWidth", _tw_mm(width)), ("ItemSizeHeight", _tw_mm(height)),
                            ("ColumnSpacing", _tw_mm(gap)), ("RowSpacing", _tw_mm(row_gap)),
                            ("ItemLayout", ACROSS_THEN_DOWN),  # wie der Etikettenassistent: erst quer, dann nach unten
                            ("TopMargin", _tw_mm(top)), ("BottomMargin", _tw_mm(bottom)),
                            ("LeftMargin", _tw_mm(left)), ("RightMargin", _tw_mm(right))):
            setattr(report.Printer, attr, value)

    @staticmethod
    def _printer_values(report: Any) -> dict[str, int]:
        return {key: int(getattr(report.Printer, key)) for key in PRINTER_KEYS}

    def page_setup(self, name: str) -> dict[str, int]:
        """Gespeicherte Seiteneinstellungen des Berichts (Twips bzw. Access-Konstanten) und seine Breite."""
        if not self.report_exists(name):
            raise ObjectNotFoundError("Bericht", name)
        app = self._s.application()
        with com_guard(f"Seiteneinstellungen von „{name}“ lesen"):
            already_open = self._is_loaded(app, "AllReports", name)
            if not already_open:
                app.DoCmd.OpenReport(name, c.AC_DESIGN, "", "", c.AC_HIDDEN)
            try:
                report = app.Reports(name)
                return {**self._printer_values(report), "Width": int(report.Width)}
            finally:
                if not already_open:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_REPORT, name, c.AC_SAVE_NO)

    def form_properties(self, name: str, keys: list[str]) -> dict[str, Any]:
        """Gespeicherte Formulareigenschaften direkt aus dem Entwurf (für die unabhängige Prüfung)."""
        if not self.form_exists(name):
            raise ObjectNotFoundError("Formular", name)
        app = self._s.application()
        with com_guard(f"Eigenschaften von „{name}“ lesen"):
            already_open = self._is_loaded(app, "AllForms", name)
            if not already_open:
                app.DoCmd.OpenForm(name, c.AC_DESIGN, "", "", -1, c.AC_HIDDEN)
            try:
                form = app.Forms(name)
                return {key: getattr(form, _PERSISTENT_PROPS.get(key, key)) for key in keys}
            finally:
                if not already_open:
                    with contextlib.suppress(Exception):
                        app.DoCmd.Close(c.AC_FORM, name, c.AC_SAVE_NO)

    def preview_pages(self, name: str) -> tuple[int | None, str]:
        """Seitenzahl in der Seitenansicht – nur wenn das Öffnen keinen Dialog auslösen kann.
        Gemessen: ein unbekannter Name in einem Berichtsausdruck öffnet in der unsichtbaren Instanz die
        Parameterabfrage und blockiert Access. Deshalb vorher: Datenquelle per DAO ohne Parameter lesbar,
        jeder Name in Ausdrücken/Filter/Sortierung bekannt, keine Ereignisse und kein VBA-Modul.
        Liefert (Seiten, "") oder (None, Grund)."""
        if not self.report_exists(name):
            raise ObjectNotFoundError("Bericht", name)
        app = self._s.application()
        if self._is_loaded(app, "AllReports", name):
            return None, "Der Bericht ist geöffnet."
        with com_guard(f"Seiten von „{name}“ zählen"):
            app.DoCmd.OpenReport(name, c.AC_DESIGN, "", "", c.AC_HIDDEN)
            try:
                reason = self._preview_risk(app.Reports(name))
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(c.AC_REPORT, name, c.AC_SAVE_NO)
            if reason:
                return None, reason
            # Access rechnet „Pages“ nur aus, wenn ein Steuerelement [Pages] verwendet (sonst 0, gemessen) →
            # temporäre Kopie mit unsichtbarem =[Pages]-Feld zählen; der Bericht selbst bleibt unverändert.
            temp = "~WIDS_Seitenzaehlung"
            reports = self._s.db.Containers("Reports").Documents
            reports.Refresh()  # roh lesen: report_exists blendet „~“-Namen aus
            if any(str(d.Name).lower() == temp.lower() for d in reports):  # nie ein vorhandenes Objekt löschen
                return None, f"Der Hilfsname „{temp}“ ist bereits vergeben."
            app.DoCmd.CopyObject("", temp, c.AC_REPORT, name)
            try:
                app.DoCmd.OpenReport(temp, c.AC_DESIGN, "", "", c.AC_HIDDEN)
                counter = app.CreateReportControl(temp, c.AC_TEXT_BOX, c.AC_DETAIL, "", "", 0, 0, 10, 10)
                counter.ControlSource = "=[Pages]"
                counter.Visible = False
                app.DoCmd.Save(c.AC_REPORT, temp)
                app.DoCmd.Close(c.AC_REPORT, temp, c.AC_SAVE_NO)
                app.DoCmd.OpenReport(temp, c.AC_PREVIEW, "", "", c.AC_HIDDEN)
                return int(app.Reports(temp).Pages), ""
            finally:
                with contextlib.suppress(Exception):
                    app.DoCmd.Close(c.AC_REPORT, temp, c.AC_SAVE_NO)
                with contextlib.suppress(Exception):
                    app.DoCmd.DeleteObject(c.AC_REPORT, temp)

    def _preview_risk(self, report: Any) -> str:
        """Grund, warum die Seitenansicht einen Dialog öffnen könnte – leer = gefahrlos."""
        if bool(report.HasModule) or any(str(getattr(report, e, "") or "") for e in _REPORT_EVENTS):
            return "Der Bericht enthält Ereignisse/VBA."
        source = str(report.RecordSource or "")
        if not source:
            return "Der Bericht hat keine Datenquelle."
        try:  # DAO fragt nie nach Parametern, sondern meldet einen Fehler
            rs = self._s.db.OpenRecordset(source, c.DB_OPEN_SNAPSHOT)
            fields = {str(f.Name).lower() for f in rs.Fields}
            rs.Close()
        except Exception:
            return "Die Datenquelle braucht Parameter oder ist fehlerhaft."
        known = fields | {str(x.Name).lower() for x in report.Controls} | _BUILTIN_NAMES
        texts = [str(report.Filter or "") if bool(report.FilterOnLoad) else "",
                 str(report.OrderBy or "") if bool(report.OrderByOnLoad) else ""]
        for ctl in report.Controls:
            with contextlib.suppress(Exception):
                if str(ctl.ControlSource or "").startswith("="):
                    texts.append(str(ctl.ControlSource))
        level = 0
        while level < 10:
            try:
                texts.append(str(group_level(report, level).ControlSource or ""))
            except Exception:
                break
            level += 1
        for index in range(5 + 2 * level):
            with contextlib.suppress(Exception):
                area = section(report, index)
                if any(str(getattr(area, e, "") or "") for e in _SECTION_EVENTS):
                    return "Der Bericht enthält Ereignisse/VBA."
        unknown = sorted(set().union(*(expression_names(t.lstrip("=")) for t in texts if t)) - known)
        return f"Unbekannte Namen im Bericht: {', '.join(unknown)}" if unknown else ""

    @staticmethod
    def _force_new_page(report: Any, level: int) -> bool:
        for base in (c.AC_GROUP_LEVEL1_HEADER, c.AC_GROUP_LEVEL1_FOOTER):
            with contextlib.suppress(Exception):
                if int(section(report, base + 2 * level).ForceNewPage or 0):
                    return True
        return False

    @staticmethod
    def _is_loaded(app: Any, collection: str, name: str) -> bool:
        try:
            return bool(getattr(app.CurrentProject, collection)(name).IsLoaded)
        except Exception:
            return False

    # ------------------------------------------------------------------ Umbenennen / Löschen
    def rename(self, kind: str, old: str, new: str) -> None:
        validate_name(new, "Objekt")
        object_type, exists = self._kind(kind)
        if not exists(old):
            raise ObjectNotFoundError(kind.capitalize(), old)
        if old.lower() != new.lower() and exists(new):
            raise ObjectExistsError(kind.capitalize(), new)
        app = self._s.application()
        with com_guard(f"{old} umbenennen"):
            app.DoCmd.Rename(new, object_type, old)
        self._s.refresh_db()
        self._s.refresh_navigation()

    def delete(self, kind: str, name: str) -> None:
        object_type, exists = self._kind(kind)
        if not exists(name):
            raise ObjectNotFoundError(kind.capitalize(), name)
        app = self._s.application()
        with com_guard(f"{name} löschen"):
            app.DoCmd.DeleteObject(object_type, name)
        self._s.refresh_db()
        self._s.refresh_navigation()

    def _kind(self, kind: str) -> tuple[int, Any]:
        lowered = kind.lower()
        if lowered in ("form", "formular"):
            return c.AC_FORM, self.form_exists
        if lowered in ("report", "bericht"):
            return c.AC_REPORT, self.report_exists
        raise AccessError(f"Unbekannter Objekttyp: {kind}")

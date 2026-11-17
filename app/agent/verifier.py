"""Result Verifier: prüft das Ergebnis jedes Schritts unabhängig von Planung und Ausführung.

Der Verifier verlässt sich nicht auf die Rückmeldung des Executors. Er liest den Zustand
der Datenbank neu aus (Struktur, gespeichertes SQL, Daten) und vergleicht ihn mit dem, was
die Aktion laut Aufgabenmodell bewirken sollte.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.access import constants as c
from app.access.constants import AccessType
from app.access.engine import AccessEngine
from app.access.naming import quote_ident, sql_literal
from app.access.sqlbuilder import READ_ONLY_KINDS, classify_sql
from app.access.values import canonical_format, default_expression, to_dao
from app.agent.executor import StepResult
from app.agent.planner import expected_columns
from app.core.errors import WidsError
from app.core.logging_setup import get_logger
from app.tasks import models as m

log = get_logger(__name__)


#: so viele Datensätze werden beim Ergebnisvergleich gespeicherte ↔ verlangte Abfrage höchstens gelesen
_COMPARE_LIMIT = 50_000


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    #: ``False`` = nur Hinweis; beeinflusst das Gesamtergebnis nicht
    critical: bool = True


class Verification(BaseModel):
    step_id: int
    checks: list[Check] = Field(default_factory=list)
    info: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.critical)

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.critical]

    def add(self, name: str, passed: bool, detail: str = "", *, critical: bool = True) -> None:
        self.checks.append(Check(name=name, passed=bool(passed), detail=detail, critical=critical))


def _same(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _check_form_properties(e: AccessEngine, name: str, wanted: dict[str, Any], v: Verification) -> None:
    """Angeforderte Formulareigenschaften (PopUp, Filter, …) aus dem gespeicherten Formular nachlesen."""
    if not wanted:
        return
    actual = e.objects.form_properties(name, list(wanted))
    for key, value in wanted.items():
        got = actual.get(key)
        if isinstance(value, bool):
            same = bool(got) == value
        elif isinstance(value, int | float):
            same = got is not None and float(got) == float(value)
        else:
            same = str(got or "").strip() == str(value).strip()
        v.add(f"Formulareigenschaft {key}", same, str(got))


def _normalize_source(text: str) -> str:
    """Vergleichsform einer Datenquelle/eines Ausdrucks, tokenweise: Zeichenketten "…"/'…' bleiben exakt; jeder
    Bezeichner wird zu ``[name]`` in Kleinschreibung – egal ob er geklammert war (``[Preis]`` = ``Preis``), aber ein
    geklammerter Name bleibt EIN Token (``[A-B]`` ≠ ``[A]-[B]``, ``[O'Brien]`` ist kein Zeichenkettenbeginn);
    Leerraum zwischen Tokens und ein abschließendes „;“ zählen nicht."""
    source = (text or "").strip()
    out: list[str] = []
    i = 0
    while i < len(source):
        ch = source[i]
        if ch in "\"'":
            end = source.find(ch, i + 1)
            end = len(source) - 1 if end < 0 else end
            out.append(source[i:end + 1])
            i = end + 1
        elif ch == "[":
            end = source.find("]", i + 1)
            end = len(source) if end < 0 else end
            out.append("[" + source[i + 1:end].lower() + "]")  # Name exakt (auch mehrfache Leerzeichen)
            i = end + 1
        elif ch.isalnum() or ch == "_":
            start = i
            while i < len(source) and (source[i].isalnum() or source[i] == "_"):
                i += 1
            word = source[start:i].lower()
            out.append(word if word[0].isdigit() else "[" + word + "]")  # Zahl 1 ≠ Feld [1]
        else:
            if not ch.isspace():
                out.append(ch)
            i += 1
    while out and out[-1] == ";":
        out.pop()
    return "".join(out)


def _same_source(actual: str, wanted: str) -> bool:
    """Datenquelle/Steuerelementinhalt gleich? Gleich sind ``=[Preis]*[Menge]`` und ``=Preis * Menge`` oder SQL mit
    anderem Leerraum/Semikolon – verschieden sind ``="A B"`` und ``="AB"`` bzw. ``[A B]`` und ``[AB]``."""
    return _normalize_source(actual) == _normalize_source(wanted)


def _overlaps(controls: list[Any], touched: set[str], tolerance: int = 30) -> list[tuple[str, str]]:
    """Paare (geändert/neu, anderes Steuerelement) desselben Bereichs, deren Rechtecke sich mehr als ``tolerance``
    Twips (≈ 0,05 cm) überdecken. Verglichen wird nur innerhalb desselben Bereichs und desselben Elternelements
    (Formular/Bericht bzw. Registerseite); Linien, Rechtecke und Register bleiben außen vor."""
    boxes = [x for x in controls if x.control_type in (c.AC_LABEL, c.AC_TEXT_BOX, c.AC_COMBO_BOX, c.AC_CHECK_BOX)
             and x.width > 0 and x.height > 0]
    found: list[tuple[str, str]] = []
    for a in boxes:
        if a.name.lower() not in touched:
            continue
        for b in boxes:
            if b is a or b.section != a.section or b.parent != a.parent:
                continue
            horizontal = min(a.left + a.width, b.left + b.width) - max(a.left, b.left)
            vertical = min(a.top + a.height, b.top + b.height) - max(a.top, b.top)
            if horizontal > tolerance and vertical > tolerance:
                found.append((a.name, b.name))
    return found


def _row(ctl: Any, controls: list[Any]) -> list[Any]:
    """Steuerelemente in derselben Zeile (gleicher Bereich/Elternteil, senkrecht überlappend), ohne ``ctl``."""
    return [x for x in controls if x is not ctl and x.section == ctl.section and x.parent == ctl.parent
            and x.control_type in (c.AC_LABEL, c.AC_TEXT_BOX, c.AC_COMBO_BOX, c.AC_CHECK_BOX)
            and min(x.top + x.height, ctl.top + ctl.height) - max(x.top, ctl.top) > 30]


def _same_row_rank(ctl: Any, wanted_left: int, controls: list[Any]) -> bool:
    """Nach der automatischen Neuordnung einer Zeile weicht die Lage ab – gemeint war die REIHENFOLGE: das Feld steht
    an der Stelle, die die angeforderte Position unter den übrigen Feldern der Zeile ergibt, und überdeckt keines."""
    siblings = _row(ctl, controls)
    if not siblings or _overlaps(controls, {ctl.name.lower()}):
        return False
    if sum(x.left < ctl.left for x in siblings) != sum(x.left < wanted_left for x in siblings):
        return False
    # nur eine echte Neuordnung rechtfertigt die Abweichung: das Feld schließt (0,1 cm Abstand) an seinen linken
    # Nachbarn an bzw. steht als erstes links von der angeforderten Lage – sonst gilt die exakte Position
    before = [x for x in siblings if x.left < ctl.left]
    if not before:
        return ctl.left <= wanted_left
    prev = max(before, key=lambda x: x.left + x.width)
    return abs(ctl.left - (prev.left + prev.width + 57)) <= 60


def _caption_key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _check_report_layout(info: Any, spec: Any, v: Verification) -> None:
    """Angeordnete Steuerelemente im gespeicherten Bericht nachmessen (Live-Fund „Students“: Felder vorhanden, aber
    die Anordnung war nicht belegt). Spaltenlayout: je Feld die Beschriftung LINKS in derselben Zeile, Felder in der
    verlangten Reihenfolge untereinander. Tabellarisch: Überschrift im Seitenkopf über ihrem Feld, Spalten von links
    nach rechts in der verlangten Reihenfolge. In beiden Fällen keine überlappenden Steuerelemente im Detailbereich.
    Mehrspaltiger DRUCK (Seite einrichten → Spalten) ist davon unabhängig und wird in ``_check_page`` geprüft."""
    if spec.labels:
        return
    groups = {g.field.lower() for g in spec.group_by}
    fields = [f for f in spec.fields if f.lower() not in groups] or list(spec.fields)
    bound = (c.AC_TEXT_BOX, c.AC_COMBO_BOX, c.AC_CHECK_BOX)
    boxes = {f: next((x for x in info.controls if x.section == c.AC_DETAIL and x.control_type in bound
                      and x.control_source.strip("[]").lower() == f.lower()), None) for f in fields}
    missing = [f for f, box in boxes.items() if box is None]
    v.add("Felder im Detailbereich", not missing, ("fehlen: " + ", ".join(missing)) if missing else f"{len(fields)} Felder")
    placed = [(f, box) for f, box in boxes.items() if box is not None]
    labels = [x for x in info.controls if x.control_type == c.AC_LABEL]

    def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
        return min(a1, b1) - max(a0, b0)

    wrong: list[str] = []
    if spec.layout == "columnar":
        for f, box in placed:
            if not any(lbl.section == c.AC_DETAIL and _caption_key(lbl.caption) == _caption_key(f)
                       and lbl.left + lbl.width <= box.left + 30
                       and overlap(lbl.top, lbl.top + lbl.height, box.top, box.top + box.height)
                       >= min(lbl.height, box.height) // 2 for lbl in labels):
                wrong.append(f)
        v.add("Beschriftung links neben dem richtigen Feld", not wrong,
              ("falsch: " + ", ".join(wrong)) if wrong else "alle Zeilen")
        tops = [box.top for _, box in placed]
        v.add("Felder untereinander in der verlangten Reihenfolge", tops == sorted(tops) and len(set(tops)) == len(tops),
              " → ".join(f for f, _ in sorted(placed, key=lambda p: p[1].top)))
    else:
        for f, box in placed:
            if not any(lbl.section == c.AC_PAGE_HEADER and _caption_key(lbl.caption) == _caption_key(f)
                       and overlap(lbl.left, lbl.left + lbl.width, box.left, box.left + box.width)
                       >= min(lbl.width, box.width) // 2 for lbl in labels):
                wrong.append(f)
        v.add("Spaltenüberschrift über dem richtigen Feld", not wrong,
              ("falsch: " + ", ".join(wrong)) if wrong else "alle Spalten")
        lefts = [box.left for _, box in placed]
        v.add("Spalten in der verlangten Reihenfolge", lefts == sorted(lefts) and len(set(lefts)) == len(lefts),
              " → ".join(f for f, _ in sorted(placed, key=lambda p: p[1].left)))
    detail = [x for x in info.controls if x.section == c.AC_DETAIL]
    clashes = _overlaps(detail, {x.name.lower() for x in detail})
    v.add("Steuerelemente im Detailbereich überlappen nicht", not clashes,
          "; ".join(f"{a} überdeckt {b}" for a, b in clashes[:5]) or "keine Überlappung")


def _check_bound_fields(e: AccessEngine, kind: str, controls: list[Any], source: str, v: Verification) -> None:
    """Nach einem Wechsel der Datenquelle: Alle gebundenen Steuerelemente müssen ein Feld der neuen Quelle zeigen
    (sonst „#Name?“). Bei einer SELECT-Quelle ohne lesbare Feldliste wird nichts behauptet."""
    available = {f.lower() for f in e.schema().source_fields(source)}
    if not available and source.lstrip()[:6].upper() == "SELECT":
        try:  # direkte SELECT-Quelle: Spalten aus der Abfrage selbst (lesend, ohne Datensätze)
            available = {col.lower() for col in e.queries.run_select_sql(source, limit=1)[0]}
        except WidsError:
            available = set()
    if not available:
        return
    bound = sorted({x.control_source for x in controls
                    if x.control_source and not x.control_source.startswith("=")
                    and x.control_type in (c.AC_TEXT_BOX, c.AC_COMBO_BOX, c.AC_CHECK_BOX)})
    missing = [b for b in bound if b.strip("[]").lower() not in available]
    v.add(f"Gebundene Felder in der neuen Datenquelle des {kind}s", not missing,
          ("fehlen: " + ", ".join(missing)) if missing else f"{len(bound)} gebunden")


def _check_width_fit(values: dict[str, int], v: Verification) -> None:
    """Berichtsbreite (bei mehrspaltigem Druck alle Spalten samt Abstand) passt zwischen die Seitenränder."""
    tw = c.TWIPS_PER_CM
    paper = {9: (11906, 16838), 8: (16838, 23811), 11: (8391, 11906), 1: (12240, 15840)}.get(values["PaperSize"])
    if paper is None:
        v.add("Berichtsbreite passt auf die Seite", False, f"unbekanntes Papierformat {values['PaperSize']}")
        return
    usable = (paper[1] if values["Orientation"] == 2 else paper[0]) - values["LeftMargin"] - values["RightMargin"]
    cols = values["ItemsAcross"] if values["DefaultSize"] and values["ItemsAcross"] > 1 else 1
    needed = values["Width"] * cols + values["ColumnSpacing"] * (cols - 1)  # mehrspaltig: alle Spalten nebeneinander
    v.add("Berichtsbreite passt auf die Seite", needed <= usable,
          f"{needed / tw:.1f} cm von {usable / tw:.1f} cm".replace(".", ",") + (f" ({cols} Spalten)" if cols > 1 else ""))


def _check_page(e: AccessEngine, name: str, page: Any, labels: Any, record_source: str, v: Verification) -> None:
    """Seiteneinstellungen/Etiketten aus dem gespeicherten Bericht nachlesen und die Seitenansicht nachzählen."""
    import math

    if page is None and labels is None:
        return
    values = e.objects.page_setup(name)
    tw = c.TWIPS_PER_CM

    def near(key: str, twips: float) -> bool:
        return abs(values[key] - twips) <= 15  # Access rundet auf ganze Twips/Druckereinheiten

    if page is not None:
        if page.orientation:
            v.add("Ausrichtung", values["Orientation"] == (2 if page.orientation == "landscape" else 1),
                  "quer" if values["Orientation"] == 2 else "hoch")
        if page.paper:
            v.add(f"Papierformat {page.paper}", values["PaperSize"] == {"A4": 9, "A3": 8, "A5": 11, "Letter": 1}[page.paper],
                  str(values["PaperSize"]))
        for key, label, cm in (("TopMargin", "oben", page.margin_top_cm), ("BottomMargin", "unten", page.margin_bottom_cm),
                               ("LeftMargin", "links", page.margin_left_cm), ("RightMargin", "rechts", page.margin_right_cm)):
            if cm is not None:
                v.add(f"Seitenrand {label}", near(key, cm * tw), f"{values[key] / tw:.2f} cm")
        if page.columns is not None:
            v.add(f"{page.columns} Spalte(n)", bool(values["DefaultSize"]) and values["ItemsAcross"] == page.columns,
                  f"{values['ItemsAcross']} Spalte(n)" + ("" if values["DefaultSize"] else ", eigene Spaltengröße"))
        if page.column_spacing_cm is not None:
            v.add("Spaltenabstand", near("ColumnSpacing", page.column_spacing_cm * tw), f"{values['ColumnSpacing'] / tw:.2f} cm")
        if page.column_layout is not None:
            from app.access.forms_reports import ACROSS_THEN_DOWN, DOWN_THEN_ACROSS

            v.add("Spaltenlayout " + ("erst quer" if page.column_layout == "across" else "erst nach unten"),
                  values["ItemLayout"] == (ACROSS_THEN_DOWN if page.column_layout == "across" else DOWN_THEN_ACROSS),
                  str(values["ItemLayout"]))
    if labels is not None:
        width, height, across, down = labels.mm()[:4]
        from app.access.forms_reports import ACROSS_THEN_DOWN

        v.add("Etiketten nebeneinander", not values["DefaultSize"] and values["ItemsAcross"] == across,
              str(values["ItemsAcross"]))
        v.add("Etiketten erst quer, dann nach unten", values["ItemLayout"] == ACROSS_THEN_DOWN, str(values["ItemLayout"]))
        v.add("Etikettengröße", near("ItemSizeWidth", width * tw / 10) and near("ItemSizeHeight", height * tw / 10),
              f"{values['ItemSizeWidth'] / tw:.2f} × {values['ItemSizeHeight'] / tw:.2f} cm")
    multi_column = page is not None and (page.columns or 1) > 1
    if labels is None and not page.max_pages and not multi_column:
        return
    if labels is None:  # „Pages“ zählt nur nach unten – rechts überstehende Seiten erkennt nur die Breite (gemessen)
        _check_width_fit(values, v)
    if labels is None and not page.max_pages:
        return  # nur Spalten verlangt: Breite geprüft, keine Seitenzählung nötig
    pages, reason = e.objects.preview_pages(name)
    if pages is None:
        v.add("Seitenzahl in der Seitenansicht", False, f"nicht automatisch zählbar: {reason}")
        return
    if page is not None and page.max_pages:
        v.add(f"Höchstens {page.max_pages} Seite(n)", pages <= page.max_pages, f"{pages} Seite(n)")
    if labels is not None:
        count = e.tables.count_records(record_source)
        expected = max(1, math.ceil(count / (across * down)))
        v.add(f"{across * down} Etiketten je Seite", pages == expected, f"{pages} Seite(n) für {count} Etiketten")


def missing_records(engine: AccessEngine, table: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Welche der verlangten Datensätze (samt Mehrfachnennung) fehlen in der Tabelle? Verglichen werden alle
    vergleichbaren Werte, leere als ``Is Null``. Ohne vergleichbaren Wert lässt sich nichts belegen → gilt als fehlend."""
    from collections import Counter

    info = engine.schema().table(table)
    if info is None:
        return list(records)
    missing: list[dict[str, Any]] = []
    for record, times in Counter(repr(sorted(r.items())) for r in records).items():
        values = dict(next(r for r in records if repr(sorted(r.items())) == record))
        conditions = []
        for key, value in values.items():
            field = info.field(key)
            if field is None or field.type in (AccessType.LONG_TEXT, AccessType.OLE, AccessType.HYPERLINK,
                                               AccessType.SINGLE, AccessType.DOUBLE):
                continue
            if value is None and field.type == AccessType.AUTONUMBER:
                continue  # leerer AutoWert: Access vergibt die Nummer beim Einfügen selbst
            conditions.append(f"{quote_ident(field.name)} Is Null" if value is None else
                              f"{quote_ident(field.name)} = {sql_literal(to_dao(value, field.type, field.name))}")
        found = engine.tables.count_records(table, " AND ".join(conditions)) if conditions else 0
        missing += [values] * max(times - found, 0)
    return missing


def records_present(engine: AccessEngine, table: str, records: list[dict[str, Any]]) -> bool:
    """Sind ALLE Datensätze (samt Mehrfachnennung) mit ihren vergleichbaren Werten in der Tabelle?"""
    return not missing_records(engine, table, records)


_SET_CLAUSE = re.compile(r"^\s*UPDATE\s+(\[[^\]]+\]|[^\s\[\]]+)(.*?)\bSET\b(.*?)(?:\bWHERE\b(.*?))?;?\s*$",
                         re.IGNORECASE | re.DOTALL)
_ALIAS = re.compile(r"(\[[^\]]+\]|[^\s\[\](),]+)\s+AS\s+(\[[^\]]+\]|[^\s\[\](),]+)", re.IGNORECASE)


def _updated_fields(sql: str) -> list[tuple[str, str, str]]:
    """(Tabelle, Feld, WHERE-Bedingung) jeder Zuweisung „SET Feld = Ausdruck“ einer UPDATE-Abfrage – außer „= Null“.
    Tabellen-Aliase („CDs AS c“) werden aufgelöst."""
    found = _SET_CLAUSE.match(sql or "")
    if found is None:
        return []
    target = found.group(1).strip("[]")
    aliases = {a.strip("[]").lower(): t.strip("[]") for t, a in _ALIAS.findall(found.group(1) + found.group(2))}
    where = (found.group(4) or "").strip()
    for alias in aliases:  # „c.Nr < 5“ → „Nr < 5“ (gezählt wird direkt in der Tabelle)
        where = re.sub(rf"(?i)(?<![\w\]])\[?{re.escape(alias)}\]?\.", "", where)
    parts, depth, quote, current = [], 0, "", ""
    for char in found.group(3):  # an Kommas außerhalb von Klammern und Texten trennen
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    fields = []
    for part in parts:
        left, sep, right = part.partition("=")
        if not sep or right.strip().lower() == "null":
            continue
        names = [n.strip("[] ") for n in re.findall(r"\[[^\]]+\]|[^.\s\[\]]+", left.strip())]
        if names:
            table = names[-2] if len(names) > 1 else target
            fields.append((aliases.get(table.lower(), table), names[-1], where))
    return fields


def _fills(value: Any) -> bool:
    """Ein berechneter Wert („=Ausdruck“), der das Feld befüllen soll – „=Null“ leert absichtlich."""
    return isinstance(value, str) and value.startswith("=") and value[1:].strip().lower() != "null"


def _check_filled(e: AccessEngine, table: str, field_name: str, affected: int, v: Verification,
                  scope: str = "") -> None:
    """Live-Fund CD-Sammlung: „33 Datensätze geändert“, aber der berechnete Wert (DLookUp ohne Treffer) war überall
    leer. Wer ein Feld befüllt, muss danach in den betroffenen Datensätzen (``scope``) Werte darin haben – sonst ist der
    Schritt nicht bestätigt. Lässt sich das nicht lesen, gilt es ebenfalls als nicht bestätigt."""
    if affected <= 0:
        return
    name = f"{field_name} enthält danach Werte"
    filled_condition = f"{quote_ident(field_name)} Is Not Null"
    try:
        if scope and e.tables.count_records(table, scope) == 0:
            scope = ""  # die Bedingung betraf das Zielfeld selbst (z. B. „… Is Null“) – dann über die ganze Tabelle
        filled = e.tables.count_records(table, f"({scope}) AND {filled_condition}" if scope else filled_condition)
    except WidsError as exc:
        v.add(name, False, f"nicht prüfbar: {exc.user_message}")
        return
    v.add(name, filled > 0, f"{filled} Datensätze mit Wert" if filled
          else f"„{field_name}“ ist nach der Änderung in den betroffenen Datensätzen leer")


class ResultVerifier:
    _schema: Any = None

    def verify(self, engine: AccessEngine, step: m.PlanStep, result: StepResult) -> Verification:
        verification = Verification(step_id=step.id)
        self._schema = None  # Struktur höchstens einmal je Prüfung lesen
        handler = getattr(self, f"_verify_{step.action.action}", None)
        if handler is None:  # pragma: no cover
            verification.add("Prüfung verfügbar", False, "Für diese Aktion gibt es keine Prüfung.", critical=False)
            return verification
        try:
            handler(engine, step.action, result, verification)
        except WidsError as exc:
            verification.add("Prüfung durchführbar", False, exc.user_message)
        except Exception as exc:
            log.exception("Verifikation von Schritt %d fehlgeschlagen", step.id)
            verification.add("Prüfung durchführbar", False, f"{type(exc).__name__}: {exc}")
        if not any(c.critical for c in verification.checks):  # leere Prüfung wäre sonst ein Erfolg (all([]))
            verification.add("Ergebnis nachgeprüft", False, "Für diesen Schritt konnte nichts nachgeprüft werden.")
        return verification

    # ------------------------------------------------------------------ Tabellen
    def _schema_of(self, e: AccessEngine) -> Any:
        if self._schema is None:
            self._schema = e.schema()
        return self._schema

    def _table(self, e: AccessEngine, name: str) -> Any:
        return self._schema_of(e).table(name)

    def _verify_create_table(self, e: AccessEngine, a: m.CreateTable, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.name
        table = self._table(e, name)
        v.add("Tabelle vorhanden", table is not None, name)
        if table is None:
            return
        for spec in a.fields:
            info = table.field(spec.name)
            if info is None:
                v.add(f"Feld {spec.name}", False, "fehlt")
                continue
            ok = info.type == spec.type
            detail = info.type_label
            if spec.type == AccessType.SHORT_TEXT and spec.size and info.size != spec.size:
                ok = False
                detail += f" statt Größe {spec.size}"
            v.add(f"Feld {spec.name}", ok, detail)
            if spec.required and spec.type != AccessType.AUTONUMBER:
                v.add(f"{spec.name}: Eingabe erforderlich", info.required)
        expected_pk = a.primary_key or [f.name for f in a.fields if f.primary_key]
        if expected_pk:
            v.add(
                "Primärschlüssel",
                [k.lower() for k in table.primary_key] == [k.lower() for k in expected_pk],
                ", ".join(table.primary_key) or "keiner",
            )
        v.add("Keine zusätzlichen Felder", len(table.fields) == len(a.fields), f"{len(table.fields)} Felder")

    def _verify_rename_table(self, e: AccessEngine, a: m.RenameTable, r: StepResult, v: Verification) -> None:
        new = r.final_name or a.new_name
        v.add("Neuer Name vorhanden", e.tables.table_exists(new), new)
        if not _same(a.old_name, new):
            v.add("Alter Name entfernt", not e.tables.table_exists(a.old_name), a.old_name)

    def _verify_copy_table(self, e: AccessEngine, a: m.CopyTable, r: StepResult, v: Verification) -> None:
        new = r.final_name or a.new_name
        schema = self._schema_of(e)
        source, copy = schema.table(a.source), schema.table(new)
        v.add("Kopie vorhanden", copy is not None, new)
        if source is None or copy is None:
            return
        v.add("Gleiche Felder", [f.name for f in source.fields] == [f.name for f in copy.fields])
        v.add("Gleicher Primärschlüssel", source.primary_key == copy.primary_key)
        # alle Feldeigenschaften (Typ, Größe, Gültigkeit, Format, Nachschlagefeld …) wie im Original
        differs = [f.name for f in source.fields if (other := copy.field(f.name)) is not None
                   and f.model_dump(exclude={"ordinal"}) != other.model_dump(exclude={"ordinal"})]
        v.add("Gleiche Feldeigenschaften", not differs, ", ".join(differs))
        v.add("Gleiche Tabellen-Gültigkeitsregel", source.validation_rule == copy.validation_rule,
              copy.validation_rule or "(keine)")
        expected = e.tables.count_records(a.source) if a.with_data else 0
        actual = e.tables.count_records(new)
        v.add("Datensatzanzahl", actual == expected, f"{actual} (erwartet {expected})")

    def _verify_delete_table(self, e: AccessEngine, a: m.DeleteTable, r: StepResult, v: Verification) -> None:
        v.add("Tabelle entfernt", not e.tables.table_exists(a.name), a.name)

    def _verify_add_field(self, e: AccessEngine, a: m.AddField, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        info = table.field(a.field.name) if table else None
        v.add("Feld vorhanden", info is not None, f"{a.table}.{a.field.name}")
        if info is not None:
            v.add("Datentyp", info.type == a.field.type, info.type_label)
            if a.field.type == AccessType.SHORT_TEXT and a.field.size:
                v.add("Feldgröße", info.size == a.field.size, str(info.size))

    def _verify_remove_field(self, e: AccessEngine, a: m.RemoveField, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        v.add("Feld entfernt", table is not None and table.field(a.field) is None, f"{a.table}.{a.field}")

    def _verify_rename_field(self, e: AccessEngine, a: m.RenameField, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        v.add("Neuer Feldname vorhanden", table is not None and table.field(a.new_name) is not None, a.new_name)
        if table is not None and not _same(a.old_name, a.new_name):
            v.add("Alter Feldname entfernt", table.field(a.old_name) is None, a.old_name)

    def _verify_change_field_type(self, e: AccessEngine, a: m.ChangeFieldType, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        info = table.field(a.field) if table else None
        v.add("Feld vorhanden", info is not None)
        if info is not None:
            v.add("Datentyp", info.type == a.new_type, info.type_label)
            if a.new_type == AccessType.SHORT_TEXT and a.size:
                v.add("Feldgröße", info.size == a.size, str(info.size))

    def _verify_set_field_properties(self, e: AccessEngine, a: m.SetFieldProperties, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        info = table.field(a.field) if table else None
        v.add("Feld vorhanden", info is not None)
        if info is None:
            return
        p = a.properties
        if p.size is not None:
            v.add("Feldgröße", info.size == p.size, str(info.size))
        if p.required is not None:
            v.add("Eingabe erforderlich", info.required == p.required, "Ja" if info.required else "Nein")
        if p.allow_zero_length is not None:
            v.add("Leere Zeichenfolge", info.allow_zero_length == p.allow_zero_length)
        if p.default_value is not None:
            expected = default_expression(p.default_value, info.type)
            v.add("Standardwert", info.default_value == expected, info.default_value or "(leer)")
        if p.validation_rule is not None:
            v.add("Gültigkeitsregel", info.validation_rule == p.validation_rule, info.validation_rule)
        if p.validation_text is not None:
            v.add("Gültigkeitsmeldung", info.validation_text == p.validation_text, info.validation_text)
        if p.description is not None:
            v.add("Beschreibung", info.description == p.description, info.description)
        if p.caption is not None:
            v.add("Beschriftung", info.caption == p.caption, info.caption)
        if p.format is not None:
            v.add("Format", info.format == canonical_format(p.format), info.format)  # gespeicherter Wert muss stimmen
        if p.input_mask is not None:
            v.add("Eingabeformat", info.input_mask == p.input_mask, info.input_mask)
        if p.decimal_places is not None:
            v.add("Dezimalstellen", info.decimal_places == p.decimal_places, str(info.decimal_places))
        if p.lookup is not None:
            v.add("Nachschlageliste", info.lookup_source == p.lookup.row_source_text(), info.lookup_source or "(keine)")
            v.add("Nur Listeneinträge", info.lookup_limit_to_list == p.lookup.limit_to_list)
            v.add("Liste bearbeitbar", info.lookup_allow_edits == p.lookup.allow_value_list_edits)
        if p.indexed is not None:
            ok = info.indexed == p.indexed or (info.primary_key and p.indexed != "no")
            v.add("Indiziert", bool(ok), info.indexed)

    def _verify_set_primary_key(self, e: AccessEngine, a: m.SetPrimaryKey, r: StepResult, v: Verification) -> None:
        actual = e.tables.primary_key(a.table)
        v.add("Primärschlüssel", [k.lower() for k in actual] == [k.lower() for k in a.fields], ", ".join(actual) or "keiner")

    def _verify_create_index(self, e: AccessEngine, a: m.CreateIndex, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        index = next((i for i in table.indexes if _same(i.name, a.name)), None) if table else None
        v.add("Index vorhanden", index is not None, a.name)
        if index is not None:
            v.add("Indexfelder", [f.lower() for f in index.fields] == [f.lower() for f in a.fields], ", ".join(index.fields))
            v.add("Eindeutigkeit", index.unique == a.unique, "eindeutig" if index.unique else "Duplikate möglich")

    def _verify_delete_index(self, e: AccessEngine, a: m.DeleteIndex, r: StepResult, v: Verification) -> None:
        table = self._table(e, a.table)
        v.add("Index entfernt", table is not None and not any(_same(i.name, a.name) for i in table.indexes), a.name)

    def _verify_set_table_sort_filter(self, e: AccessEngine, a: m.SetTableSortFilter, r: StepResult, v: Verification) -> None:
        from app.access.tables import collection_has, get_property

        db = e.session.db
        db.TableDefs.Refresh()
        db.QueryDefs.Refresh()
        tabledef = db.TableDefs(a.table) if collection_has(db.TableDefs, a.table) else db.QueryDefs(a.table)
        if a.order_by is not None:
            v.add("Sortierung gespeichert", str(get_property(tabledef, "OrderBy", "")) == a.order_by, a.order_by or "(keine)")
            if a.order_by:  # ohne „beim Laden sortieren“ zeigt das Datenblatt die gespeicherte Sortierung nicht
                v.add("Sortierung aktiv", bool(get_property(tabledef, "OrderByOn", False)))
        if a.filter is not None:
            v.add("Filter gespeichert", str(get_property(tabledef, "Filter", "")) == a.filter, a.filter or "(keiner)")
            if a.filter:
                v.add("Filter beim Laden aktiv", bool(get_property(tabledef, "FilterOnLoad", False)))

    def _verify_set_datasheet_layout(self, e: AccessEngine, a: m.SetDatasheetLayout, r: StepResult, v: Verification) -> None:
        from app.access.tables import _column_order, collection_has, get_property

        db = e.session.db
        db.TableDefs.Refresh()
        obj = db.TableDefs(a.object) if collection_has(db.TableDefs, a.object) else db.QueryDefs(a.object)
        fields = {str(f.Name).lower(): f for f in obj.Fields}
        spec = a.layout
        order = [n.lower() for _, _, n in _column_order(obj)]
        wanted = [n.lower() for n in [*spec.frozen, *spec.column_order]]
        wanted = list(dict.fromkeys(wanted))
        if wanted:
            v.add("Spaltenreihenfolge", order[:len(wanted)] == wanted, ", ".join(order[:len(wanted)]))
        if spec.frozen or spec.unfreeze:
            v.add("Fixierte Spalten", int(get_property(obj, "FrozenColumns", 1) or 1) - 1 == len(spec.frozen), str(len(spec.frozen)))
        for col in spec.hidden:
            v.add(f"„{col}“ ausgeblendet", bool(get_property(fields[col.lower()], "ColumnHidden", False)))
        for col in spec.unhide:
            v.add(f"„{col}“ eingeblendet", not get_property(fields[col.lower()], "ColumnHidden", False))
        for col, width in spec.widths_cm.items():
            got = int(get_property(fields[col.lower()], "ColumnWidth", -1) or -1)
            v.add(f"Breite „{col}“", abs(got - width * 567) < 30, f"{got / 567:.1f} cm")
        for col in spec.best_fit:
            v.add(f"Optimale Breite „{col}“", int(get_property(fields[col.lower()], "ColumnWidth", -1) or -1) > 0)
        for col, fmt in spec.formats.items():
            stored = str(get_property(fields[col.lower()], "Format", ""))
            v.add(f"Format „{col}“", stored == canonical_format(fmt), stored)
        for col, cap in spec.captions.items():
            v.add(f"Beschriftung „{col}“", str(get_property(fields[col.lower()], "Caption", "")) == cap, cap)
        if spec.alternate_back_color:
            from app.access.forms_reports import color_hex

            raw = get_property(obj, "DatasheetAlternateBackColor", None)
            actual = color_hex(raw) if raw is not None else "(keine)"
            v.add("Alternative Zeilenfarbe", actual.lower() == spec.alternate_back_color.lower(), actual)

    # ------------------------------------------------------------------ Daten
    def _verify_export_data(self, e: AccessEngine, a: m.ExportData, r: StepResult, v: Verification) -> None:
        from pathlib import Path

        from app.access import importer

        path = Path(str(r.details.get("path", "")))
        v.add("Datei vorhanden", path.is_file(), path.name)
        if path.is_file():
            rows = importer.read_rows(path, delimiter=None if path.suffix.lower() == ".xlsx" else a.delimiter,
                                      has_header=a.header)
            expected = e.tables.count_records(a.source) + (1 if a.header else 0)
            v.add("Datensätze in der Datei", len(rows) == expected, f"{len(rows)} Zeilen")
        if path.is_file() and (a.excel_no_wrap or a.excel_autofit):
            from app.access import excel

            layout = excel.workbook_layout(path)
            if a.excel_no_wrap:
                v.add("Kein Textumbruch in Excel", layout["wrapped_cells"] == 0,
                      f"{layout['wrapped_cells']} Zelle(n) mit Umbruch")
            if a.excel_autofit:
                widths, longest = layout["widths"], layout["longest_text"]
                unset = [col for col in longest if col not in widths]
                # optimale Breite ≈ Zeichenzahl des längsten Texts (Excel-Standardschrift): grob, aber unabhängig
                odd = [col for col, n in longest.items() if col in widths and n and not 0.6 * n <= widths[col] <= 1.6 * n + 4]
                v.add("Spaltenbreiten optimal angepasst", not unset and not odd,
                      f"ohne Breite: {unset}, unpassend: {odd}" if unset or odd else f"{len(widths)} Spalten")

    def _verify_import_odbc(self, e: AccessEngine, a: m.ImportOdbc, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.table
        table = self._table(e, name)
        v.add("Tabelle vorhanden", table is not None)
        if table is not None:
            v.add("Verknüpft" if a.link else "Importiert (nicht verknüpft)", table.linked == a.link)

    def _verify_modify_report(self, e: AccessEngine, a: m.ModifyReport, r: StepResult, v: Verification) -> None:
        info = e.objects.inspect_report(a.name)
        _check_section_colors(info, a.changes.section_colors, v)
        spec = a.changes
        if spec.record_source is not None:
            v.add("Datenquelle des Berichts", _same_source(info.record_source, spec.record_source), info.record_source)
            _check_bound_fields(e, "Bericht", info.controls, info.record_source, v)
        if spec.filter is not None:
            v.add("Filter des Berichts", info.properties.get("Filter", "") == spec.filter, info.properties.get("Filter", ""))
        if spec.order_by is not None:
            v.add("Sortierung des Berichts", info.properties.get("OrderBy", "") == spec.order_by, info.properties.get("OrderBy", ""))
        if spec.hide_detail is not None:
            v.add("Detailbereich", (info.properties.get("Detailbereich") == "ausgeblendet") == spec.hide_detail)
        for group in spec.groups:
            found = next((g for g in info.group_levels if g.expression.strip("[]=").lower() == group.field.lower()), None)
            v.add(f"Gruppe „{group.field}“", found is not None)
            if found is not None:  # jede verlangte Einstellung nachlesen, auch „aus“
                v.add(f"Neue Seite je „{group.field}“" + ("" if group.new_page else " – nein"),
                      found.new_page == group.new_page)
                v.add(f"Sortierung „{group.field}“", found.sort_descending == (group.sort == "DESC"),
                      "absteigend" if found.sort_descending else "aufsteigend")
                v.add(f"Zusammenhalten „{group.field}“" + ("" if group.keep_together else " – nein"),
                      found.keep_together == group.keep_together)
        self._check_control_edits(info.controls, spec.controls, spec.control_changes, spec.delete_controls, v)
        if spec.width_cm is not None:
            width = e.objects.page_setup(a.name)["Width"]
            v.add("Berichtsbreite", abs(width - spec.width_cm * c.TWIPS_PER_CM) <= 30, f"{width / c.TWIPS_PER_CM:.1f} cm")
        _check_page(e, a.name, spec.page_setup, None, info.record_source, v)
        if (spec.controls or spec.control_changes) and not any(x.name == "Berichtsbreite passt auf die Seite"
                                                               for x in v.checks):
            _check_width_fit(e.objects.page_setup(a.name), v)  # Neuordnung/neue Felder dürfen nicht über die Seite

    def _verify_set_field_from_file(self, e: AccessEngine, a: m.SetFieldFromFile, r: StepResult,
                                    v: Verification) -> None:
        """Der Feldinhalt jedes betroffenen Datensatzes ist genau der Dateiinhalt (Zeilenenden egal)."""
        from pathlib import Path

        from app.access import importer

        expected = importer.read_text_file(Path(r.details["file"]) if r.details.get("file")
                                           else importer.resolve_source(a.file, e.path))
        sql = f"SELECT {quote_ident(a.field)} FROM {quote_ident(a.table)}" + (f" WHERE {a.where}" if a.where else "")
        _, rows = e.tables.read_sql(sql, limit=5000)

        def plain(text: Any) -> str:
            return "\n".join(line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")).strip()

        same = sum(1 for row in rows if plain(row[0]) == plain(expected))
        v.add("Datensatz für den Text vorhanden", bool(rows), f"{len(rows)} Datensatz/-sätze")
        v.add(f"{a.field} enthält den Dateiinhalt", bool(rows) and same == len(rows),
              f"{same} von {len(rows)} Datensätzen, {len(expected)} Zeichen aus „{Path(r.details.get('file', a.file)).name}“")

    def _verify_map_keys(self, e: AccessEngine, a: m.MapKeys, r: StepResult, v: Verification) -> None:
        """Jeder zugeordnete Quellwert hat in ALLEN seinen Datensätzen genau den ermittelten Schlüssel, der Schlüssel
        existiert in der Nachschlagetabelle, und kein Wert blieb ohne eindeutige Zuordnung."""
        from app.access.matching import match_where

        mapping = list(r.details.get("mapping", []))
        wrong, missing_keys = [], []
        for source, key in mapping:
            where = match_where(a.source_fields, tuple(source))
            total = e.tables.count_records(a.table, where)
            stored = e.tables.count_records(a.table, f"({where}) AND {quote_ident(a.target_field)} = {sql_literal(key)}")
            if stored != total:
                wrong.append(f"{' '.join(map(str, source))}: {stored} von {total}")
            if e.tables.count_records(a.lookup_table, f"{quote_ident(a.lookup_key)} = {sql_literal(key)}") != 1:
                missing_keys.append(str(key))
        v.add("Zugeordnete Schlüssel gespeichert", bool(mapping) and not wrong,
              "; ".join(wrong) or f"{len(mapping)} Werte zugeordnet")
        v.add("Schlüssel existieren in der Nachschlagetabelle", not missing_keys, ", ".join(missing_keys))
        unresolved = list(r.details.get("unresolved", []))
        # unabhängig vom Ergebnis der Zuordnung: bleibt ein Datensatz MIT Quellwert ohne Schlüssel, ist es nicht erledigt
        has_source = " OR ".join(f"{quote_ident(f)} Is Not Null" for f in a.source_fields)
        open_rows = e.tables.count_records(a.table, f"{quote_ident(a.target_field)} Is Null AND ({has_source})")
        v.add("Kein Datensatz mit Quellwert ohne Schlüssel", open_rows == 0, f"{open_rows} ohne Schlüssel")
        v.add("Alle Werte eindeutig zugeordnet", not unresolved,
              "; ".join(f"{' '.join(map(str, s))}: {why}" for s, why in unresolved))
        if r.details.get("similar"):
            v.add("Zuordnung über Ähnlichkeit (eindeutig)", True, "; ".join(
                f"{' '.join(map(str, s))} → {k} ({ratio})" for s, k, ratio in r.details["similar"]), critical=False)

    def _verify_attach_files(self, e: AccessEngine, a: m.AttachFiles, r: StepResult, v: Verification) -> None:
        stored = {k.lower(): names for k, names in e.tables.attachment_names(a.table, a.field, a.key_field).items()}
        for key, file_name in dict(r.details.get("files", {})).items():
            names = stored.get(str(key).lower(), [])
            v.add(f"„{file_name}“ bei {a.key_field} = {key}", file_name in names, ", ".join(names))
            if a.replace:
                v.add(f"Nur das neue Foto bei {key}", names == [file_name], ", ".join(names))

    def _verify_transfer_objects(self, e: AccessEngine, a: m.TransferObjects, r: StepResult, v: Verification) -> None:
        from pathlib import Path

        from app.access import transfer

        if a.direction == "export":
            for name, ok in transfer.transferred(Path(str(r.details.get("path", ""))), a.objects).items():
                v.add(f"„{name}“ in der Zieldatenbank", ok)
            return
        schema = e.schema()
        local = {o.name.lower(): o for group in (schema.tables, schema.queries, schema.forms, schema.reports) for o in group}
        for item in a.objects:
            found = local.get(item.target.lower())
            v.add(f"„{item.target}“ vorhanden", found is not None)
            if found is not None and item.type == "table":
                v.add(f"„{item.target}“ {'verknüpft' if a.direction == 'link' else 'importiert'}",
                      bool(getattr(found, "linked", False)) == (a.direction == "link"))

    def _verify_answer_object_property(self, e: AccessEngine, a: m.AnswerObjectProperty, r: StepResult,
                                       v: Verification) -> None:
        from app.agent.executor import object_property_answer

        answer = object_property_answer(e, a)  # erneut aus dem gespeicherten Zustand gelesen
        v.add("Antwort erneut ermittelt", answer == r.details.get("answer"), answer[:200])

    def _verify_answer_query(self, e: AccessEngine, a: m.AnswerQuery, r: StepResult, v: Verification) -> None:
        columns, rows, isolated = e.queries.run_select_readonly(a.sql, limit=200)
        answer = m.answer_summary(columns, rows)
        v.add("Antwort erneut ermittelt", answer == r.details.get("answer"), answer[:200])
        if not isolated:  # ehrlich: die Nachprüfung lief über dieselbe Verbindung wie die Ausführung
            v.add("Nachprüfung über eigene Verbindung", False, "Datei exklusiv geöffnet – über die bestehende "
                  "Verbindung erneut gelesen (gleiche Datenquelle, keine unabhängige Zweitverbindung)", critical=False)
        v.info.update({"columns": columns, "sample_rows": rows[:10], "row_count": len(rows)})

    def _verify_calculate_value(self, e: AccessEngine, a: m.CalculateValue, r: StepResult, v: Verification) -> None:
        """Zwei Rechenwege (exakt und Gleitkomma) müssen dasselbe ergeben; jede Zahl stammt aus der Aufgabe."""
        from app.agent import calc

        unknown = calc.unknown_numbers(a.expression, a.given)
        v.add("Zahlen aus den Angaben der Aufgabe", not unknown, ", ".join(unknown) or "alle belegt")
        try:
            exact, approx = calc.evaluate(a.expression), calc.evaluate_float(a.expression)
        except calc.CalcError as exc:
            v.add("Rechnung nachvollziehbar", False, str(exc))
            return
        v.add("Zweiter Rechenweg stimmt überein", abs(float(exact) - approx) <= 1e-9 * max(1.0, abs(approx)),
              f"{float(exact)} / {approx}")
        answer = calc.format_value(exact, a.decimals)
        v.add("Antwort erneut ermittelt", answer == r.details.get("answer"), answer)

    def _verify_modify_form(self, e: AccessEngine, a: m.ModifyForm, r: StepResult, v: Verification) -> None:
        info = e.objects.inspect_form(a.name)
        _check_section_colors(info, a.changes.section_colors, v)
        spec = a.changes
        if spec.caption is not None:
            v.add("Titel des Formulars", info.caption == spec.caption, info.caption)
        if spec.record_source is not None:
            v.add("Datenquelle des Formulars", _same_source(info.record_source, spec.record_source), info.record_source)
            _check_bound_fields(e, "Formular", info.controls, info.record_source, v)
        _check_form_properties(e, a.name, spec.properties, v)
        if spec.default_view:
            views = {"single": 0, "continuous": 1, "datasheet": 2, "split": 5}
            v.add("Standardansicht", info.default_view == views[spec.default_view], str(info.default_view))
        self._check_control_edits(info.controls, spec.controls, spec.control_changes, spec.delete_controls, v)
        if spec.tab_control:
            pages = {x.name: x.caption for x in info.controls if x.control_type == 124}
            v.add("Registerseiten", sorted(pages.values()) == sorted(p.caption for p in spec.tab_control.pages),
                  ", ".join(pages.values()))
            by_caption = {cap: page for page, cap in pages.items()}
            for page_spec in spec.tab_control.pages:
                inside = {(x.control_source or x.source_object).lower() for x in info.controls
                          if x.parent == by_caption.get(page_spec.caption)}
                for field in page_spec.fields:
                    v.add(f"„{field}“ auf Seite „{page_spec.caption}“", field.lower() in inside)
                if page_spec.subform:
                    v.add(f"Unterformular „{page_spec.subform}“ auf Seite „{page_spec.caption}“",
                          any(s.endswith(page_spec.subform.lower()) for s in inside))

    @staticmethod
    def _check_control_edits(controls: list[Any], added: list[Any], changes: list[Any], deleted: list[str],
                             v: Verification) -> None:
        sources = {x.control_source.replace(" ", "").lower() for x in controls if x.control_source}
        by_name = {x.name.lower(): x for x in controls}
        for ctl in added:
            named = by_name.get(ctl.name.lower()) if ctl.name else None
            if ctl.name:  # genau DIESES Steuerelement – nicht irgendeins mit gleichem Inhalt (z. B. „…_2“)
                v.add(f"Steuerelement „{ctl.name}“", named is not None)
            if ctl.type == "text_box" and ctl.expression:
                matches = (named is not None and _same_source(named.control_source, ctl.expression)) if ctl.name \
                    else ctl.expression.replace(" ", "").lower() in sources
                v.add(f"Steuerelement {ctl.expression}", matches)
        for change in changes:
            found = by_name.get(change.name.lower())
            v.add(f"Steuerelement „{change.name}“", found is not None)
            if found is not None and change.left_cm is not None:
                wanted = round(change.left_cm * 567)
                ok = abs(found.left - wanted) < 30 or _same_row_rank(found, wanted, controls)
                v.add(f"Lage „{change.name}“", ok, f"{found.left / 567:.1f} cm (angefordert {change.left_cm:.1f} cm)")
            if found is not None and change.caption is not None:
                v.add(f"Beschriftung „{change.name}“", found.caption == change.caption, found.caption)
            if found is not None and change.control_source is not None:
                v.add(f"Inhalt „{change.name}“", _same_source(found.control_source, change.control_source),
                      found.control_source)
            if found is not None:
                _check_control_details(found, change, v)
                if change.border_style is not None and found.border_style >= 0:
                    v.add(f"Rahmenart „{change.name}“", found.border_style == change.border_style,
                          f"{found.border_style} (verlangt {change.border_style})")
                for wanted, axis, label in ((change.merge_down, "rows", "Zeilen"), (change.merge_right, "columns", "Spalten")):
                    if wanted:
                        covered = _cells_covered(found, controls, axis)
                        left_over = [x for x in controls if x.control_type == c.AC_EMPTY_CELL and x.layout_id == found.layout_id
                                     and x.section == found.section
                                     and found.left - 30 <= x.left < found.left + found.width - 30
                                     and found.top - 30 <= x.top < found.top + found.height - 30]
                        v.add(f"„{change.name}“ über {wanted + 1} {label} verbunden", covered >= wanted + 1 and not left_over,
                              f"reicht über {covered} {label}" + (f", {len(left_over)} leere Zelle(n) darunter/daneben"
                                                                  if left_over else ""))
            if found is None or found.control_type not in (c.AC_TEXT_BOX, c.AC_COMBO_BOX):
                continue  # diese Merkmale liest inspect nur bei Text-/Kombinationsfeldern
            flags = found.formatting.split(", ")
            for wanted, word in ((change.transparent, "transparent"),
                                 (None if change.enabled is None else not change.enabled, "nicht aktiviert"),
                                 (change.locked, "gesperrt"),
                                 (None if change.border_style is None else change.border_style == 0, "ohne Rahmen")):
                if wanted is not None:
                    v.add(f"„{change.name}“: {word}" + ("" if wanted else " – nein"), (word in flags) == wanted,
                          found.formatting or "Standard")
        for name in deleted:
            v.add(f"„{name}“ entfernt", name.lower() not in by_name)
        touched = {ch.name.lower() for ch in changes} | {(a.name or "").lower() for a in added if a.name}
        if touched:  # Live-Befund 14.11. (0501/U4): verschobenes Feld lag über einem anderen – nur eigene Änderungen
            clashes = _overlaps(controls, touched)
            v.add("Steuerelemente überlappen nicht", not clashes,
                  "; ".join(f"{a} überdeckt {b}" for a, b in clashes[:5]) or "keine Überlappung")

    def _verify_set_conditional_format(self, e: AccessEngine, a: m.SetConditionalFormat, r: StepResult, v: Verification) -> None:
        rules = e.objects.conditional_formats(a.object_type, a.name).get(a.control, [])
        exact = bool(getattr(a, "replace", False))  # ersetzen: genau diese Regeln, keine alten übrig
        v.add("Formatierungsregeln vorhanden", len(rules) == len(a.rules) if exact else len(rules) >= len(a.rules),
              "; ".join(rules)[:300])
        def norm(text: str) -> str:
            return "".join(ch for ch in text.lower() if ch not in " \"'")

        used: set[int] = set()
        for rule in a.rules:  # jede verlangte Regel = EINE eigene gespeicherte Regel mit Bedingung UND ihren Farben
            if rule.data_bar:
                needle, exact = "Datenbalken", False
            elif rule.expression:
                needle, exact = f"Ausdruck {rule.expression}", True
            else:
                needle = f"Feldwert {rule.operator} {rule.value}" + (f" und {rule.value2}" if rule.value2 else "")
                exact = True
            colors = [] if rule.data_bar else [f"{label} {color.upper()}" for label, color in
                                               (("Schrift", rule.fore_color), ("Hintergrund", rule.back_color)) if color]

            def fits(stored: str, needle: str = needle, exact: bool = exact, colors: list[str] = colors) -> bool:
                condition = stored.split(" → ")[0]
                same = norm(condition) == norm(needle) if exact else norm(needle) in norm(condition)
                return same and all(col.upper() in stored.upper() for col in colors)

            match = next((i for i, x in enumerate(rules) if i not in used and fits(x)), None)
            if match is not None:
                used.add(match)
            v.add(f"Regel „{needle}“" + (f" ({', '.join(colors)})" if colors else ""), match is not None,
                  rules[match] if match is not None else "; ".join(rules)[:300])

    def _verify_create_navigation_form(self, e: AccessEngine, a: m.CreateNavigationForm, r: StepResult, v: Verification) -> None:
        info = e.objects.inspect_form(r.final_name or a.name)
        kinds = {ctl.control_type for ctl in info.controls}
        v.add("Navigationssteuerelement", c.AC_NAVIGATION_CONTROL in kinds)
        buttons = [ctl for ctl in info.controls if ctl.navigation_target]
        for tab in a.tabs:  # je Registerkarte: Beschriftung, Ziel UND Zieltyp an derselben Schaltfläche
            same = [b for b in buttons if b.caption == tab.caption and b.navigation_target.lower() == tab.target.lower()]
            found = next((b for b in same if b.navigation_kind == tab.target_type), same[0] if same else None)
            v.add(f"Registerkarte „{tab.caption}“ → {tab.target}", found is not None)
            if found is not None:
                v.add(f"Zieltyp „{tab.caption}“", found.navigation_kind == tab.target_type, found.navigation_kind or "?")

    def _verify_import_data(self, e: AccessEngine, a: m.ImportData, r: StepResult, v: Verification) -> None:
        before, inserted = int(r.details.get("count_before", 0)), int(r.details.get("inserted", 0))
        v.add("Tabelle vorhanden", e.tables.table_exists(a.table))
        after = e.tables.count_records(a.table)
        v.add("Datensätze importiert", inserted > 0 and after == before + inserted, f"{before} → {after}")
        if "source_rows" in r.details:
            rows = int(r.details["source_rows"])
            v.add("Alle Zeilen der Quelldatei importiert", inserted == rows, f"{inserted} von {rows}")
        table = self._table(e, a.table)
        if table is not None and r.details.get("source"):
            self._check_first_source_row(e, a, r, table, v)
        for spec in a.fields:
            info = table.field(spec.name) if table else None
            v.add(f"Feld {spec.name} vorhanden", info is not None)
            if info is None or not r.details.get("created"):
                continue
            v.add(f"Datentyp {spec.name}", info.type == spec.type, info.type.value)
            if spec.description is not None:
                v.add(f"Beschreibung {spec.name}", info.description == spec.description, info.description)

    def _verify_insert_records(self, e: AccessEngine, a: m.InsertRecords, r: StepResult, v: Verification) -> None:
        before = int(r.details.get("count_before", -1))
        after = e.tables.count_records(a.table)
        if r.details.get("already_done"):  # erneuter Start: nichts eingefügt – JEDER verlangte Datensatz muss da sein
            v.add("Alle verlangten Datensätze vorhanden", records_present(e, a.table, a.records),
                  f"{len(a.records)} Datensatz/-sätze, bereits in Aufgabe {r.details['already_done']} eingefügt")
            return
        if before >= 0:
            v.add("Datensatzanzahl gestiegen", after == before + len(a.records), f"{before} → {after}")
        # Stichprobe: der erste Datensatz ist mit seinen einfachen Werten auffindbar
        schema_table = self._table(e, a.table)
        conditions: list[str] = []
        for key, value in a.records[0].items():
            info = schema_table.field(key) if schema_table else None
            if info is None or value is None or info.type in (AccessType.LONG_TEXT, AccessType.OLE, AccessType.HYPERLINK):
                continue
            literal = sql_literal(to_dao(value, info.type, info.name))
            if info.type in (AccessType.SINGLE, AccessType.DOUBLE):
                continue  # Gleitkommavergleich wäre unzuverlässig
            conditions.append(f"{quote_ident(info.name)} = {literal}")
        if conditions:
            found = e.tables.count_records(a.table, " AND ".join(conditions))
            v.add("Erster Datensatz auffindbar", found >= 1, f"{found} Treffer")

    def _verify_update_records(self, e: AccessEngine, a: m.UpdateRecords, r: StepResult, v: Verification) -> None:
        affected = int(r.details.get("affected", -1))
        v.add("Änderung ausgeführt", affected >= 0, f"{affected} Datensätze")
        v.add("Mindestens ein Datensatz betroffen", affected > 0, critical=False,
              detail="Die Bedingung traf auf keinen Datensatz zu." if affected == 0 else "")
        schema_table = self._table(e, a.table)
        if "target_keys" in r.details:  # genau die vorher betroffenen Datensätze nachprüfen
            self._check_updated_rows(e, a, r, schema_table, v)
            return
        scope = e.tables.count_records(a.table, a.where) if a.where else e.tables.count_records(a.table)
        for key, value in a.values.items():
            info = schema_table.field(key) if schema_table else None
            if info is not None and _fills(value):
                _check_filled(e, a.table, info.name, affected, v, scope=a.where or "")
            if info is None or (isinstance(value, str) and value.startswith("=")):
                continue
            if info.type in (AccessType.SINGLE, AccessType.DOUBLE, AccessType.LONG_TEXT):
                continue
            literal = sql_literal(to_dao(value, info.type, info.name))
            condition = f"{quote_ident(info.name)} = {literal}" if value is not None else f"{quote_ident(info.name)} Is Null"
            where = f"({a.where}) AND {condition}" if a.where else condition
            # Ändert die Bedingung selbst das Zielfeld, ist der Bereich danach evtl. leer – dann ohne Bedingung zählen
            matching = e.tables.count_records(a.table, where)
            if a.where and scope == 0:
                matching_any = e.tables.count_records(a.table, condition)
                v.add(f"{info.name} gesetzt", matching_any >= affected, f"{matching_any} Datensätze mit neuem Wert")
            else:
                v.add(f"{info.name} gesetzt", matching == scope, f"{matching} von {scope}")

    @staticmethod
    def _check_updated_rows(e: AccessEngine, a: m.UpdateRecords, r: StepResult, table: Any, v: Verification) -> None:
        """Jeder verlangte Wert in GENAU den Datensätzen, die die Bedingung vor der Änderung traf (Schlüssel)."""
        key, keys = str(r.details["key_field"]), list(r.details["target_keys"])
        key_info = table.field(key) if table else None
        v.add("Geänderte Datensätze = betroffene Datensätze", int(r.details.get("affected", -1)) == len(keys),
              f"{r.details.get('affected')} geändert, {len(keys)} betroffen")
        if not keys or key_info is None:
            return
        for name, value in a.values.items():
            info = table.field(name)
            if info is None:
                v.add(f"{name} gesetzt", False, "Feld fehlt")
                continue
            if isinstance(value, str) and value.startswith("="):
                v.add(f"{name}: Ausdruck nicht einzeln nachprüfbar", True, value, critical=False)
                if _fills(value):
                    filled = sum(e.tables.count_records(a.table, f"{quote_ident(key)} In ({chunk}) AND "
                                                                 f"{quote_ident(info.name)} Is Not Null")
                                 for chunk in (", ".join(sql_literal(to_dao(k, key_info.type, key))
                                                         for k in keys[i:i + 200]) for i in range(0, len(keys), 200)))
                    v.add(f"{info.name} enthält danach Werte", filled > 0,
                          f"{filled} von {len(keys)} betroffenen Datensätzen mit Wert")
                continue
            if value is None:
                condition = f"{quote_ident(info.name)} Is Null"
            elif info.type in (AccessType.SINGLE, AccessType.DOUBLE):
                condition = f"Abs({quote_ident(info.name)} - {sql_literal(to_dao(value, info.type, info.name))}) < 0.000001"
            else:
                condition = f"{quote_ident(info.name)} = {sql_literal(to_dao(value, info.type, info.name))}"
            matching = 0
            for start in range(0, len(keys), 200):
                chunk = ", ".join(sql_literal(to_dao(k, key_info.type, key)) for k in keys[start:start + 200])
                matching += e.tables.count_records(a.table, f"{quote_ident(key)} In ({chunk}) AND {condition}")
            v.add(f"{info.name} gesetzt", matching == len(keys), f"{matching} von {len(keys)} betroffenen Datensätzen")

    @staticmethod
    def _check_first_source_row(e: AccessEngine, a: m.ImportData, r: StepResult, table: Any, v: Verification) -> None:
        """Quelldatei unabhängig neu lesen: die erste Datenzeile muss mit ihren Werten in den RICHTIGEN Feldern stehen
        (vertauschte Spalten bestehen die reine Anzahlprüfung)."""
        from pathlib import Path

        from app.access import importer

        try:
            rows = importer.read_rows(Path(str(r.details["source"])), delimiter=a.delimiter,
                                      widths=a.column_widths or None, sheet=a.sheet, has_header=a.has_header)
        except Exception as exc:  # Quelle nicht mehr lesbar: kein Beleg, aber auch kein Fehler der Ausführung
            v.add("Erste Zeile der Quelldatei", False, f"nicht lesbar: {exc}", critical=False)
            return
        header = rows[0] if a.has_header and rows else None
        data = rows[1:] if header is not None else rows
        if not data:
            return
        if a.fields:
            names = [f.name for f in a.fields]
        elif header is not None and r.details.get("created"):
            names = [f.name for f in importer.infer_fields(header, data)]  # gleiche Namen wie beim Anlegen
        elif header is not None:
            names = [importer.clean_field_name(h, i) for i, h in enumerate(header)]
        else:
            return
        conditions = []
        for name, value in zip(names, data[0], strict=False):
            info = table.field(name)
            if info is None or value in ("", None) or isinstance(value, float | bool) or info.type in (
                    AccessType.SINGLE, AccessType.DOUBLE, AccessType.LONG_TEXT, AccessType.DATETIME, AccessType.YES_NO):
                continue  # nur eindeutig vergleichbare Werte (Text, ganze Zahlen)
            try:
                conditions.append(f"{quote_ident(info.name)} = {sql_literal(to_dao(value, info.type, info.name))}")
            except Exception:
                continue
        if conditions:
            found = e.tables.count_records(a.table, " AND ".join(conditions))
            v.add("Erste Zeile der Quelldatei in den richtigen Feldern", found >= 1,
                  f"{found} Treffer für {len(conditions)} Werte")

    def _verify_delete_records(self, e: AccessEngine, a: m.DeleteRecords, r: StepResult, v: Verification) -> None:
        remaining = e.tables.count_records(a.table, a.where) if a.where else e.tables.count_records(a.table)
        v.add("Betroffene Datensätze entfernt", remaining == 0, f"{remaining} verbleiben")
        before, affected = r.details.get("count_before"), r.details.get("affected")
        if before is not None and affected is not None:
            total = e.tables.count_records(a.table)
            v.add("Nur die betroffenen Datensätze entfernt", total == int(before) - int(affected), f"{before} → {total}")

    # ------------------------------------------------------------------ Beziehungen
    def _verify_create_relationship(self, e: AccessEngine, a: m.CreateRelationship, r: StepResult, v: Verification) -> None:
        rel = e.relations.find_relation(a.table, a.foreign_table, a.fields)
        v.add("Beziehung vorhanden", rel is not None, a.target())
        if rel is None:
            return
        v.add("Referenzielle Integrität", rel.enforce_integrity == a.enforce_integrity,
              "aktiv" if rel.enforce_integrity else "nicht aktiv")
        if a.enforce_integrity:
            v.add("Aktualisierungsweitergabe", rel.cascade_update == a.cascade_update)
            v.add("Löschweitergabe", rel.cascade_delete == a.cascade_delete)
        v.add("Verknüpfungstyp", rel.join_type == a.join_type, rel.join_type)
        v.info["relation_type"] = rel.relation_type

    def _verify_delete_relationship(self, e: AccessEngine, a: m.DeleteRelationship, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.name or ""
        v.add("Beziehung entfernt", not any(_same(rel.name, name) for rel in e.relations.list_relations()), name)

    # ------------------------------------------------------------------ Abfragen
    def _verify_create_query(self, e: AccessEngine, a: m.CreateQuery, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.name
        v.add("Abfrage gespeichert", e.queries.exists(name), name)
        if not e.queries.exists(name):
            return
        stored_sql = e.queries.get_sql(name)
        v.info["sql"] = stored_sql
        check = e.queries.check_sql(stored_sql)
        v.add("SQL von Access akzeptiert", check.ok, check.error)
        if not check.ok:
            return
        kind = classify_sql(stored_sql)
        # auch bei reinem SQL: eine gleichnamige Aktionsabfrage ist nie die verlangte Auswahlabfrage (und umgekehrt)
        expected_kind = (("select" if a.spec.kind == "select" else a.spec.kind) if a.spec is not None
                         else classify_sql(a.sql) if a.sql else "")
        if expected_kind:
            v.add("Abfragetyp", kind == expected_kind, kind)
        declared = {p.name.lower() for p in a.spec.parameters} if a.spec else set()
        prompts: set[str] = set()
        if a.spec is not None:
            import re

            known = {f.lower() for t in a.spec.tables for f in self._schema_of(e).source_fields(t)}
            for fld in a.spec.fields:
                for criteria in [fld.criteria, *fld.or_criteria]:
                    for token in re.findall(r"\[([^\]]+)\]", criteria or ""):
                        if token.lower() not in known:
                            prompts.add(token.lower())
        unexpected = [p for p in check.parameters if p.lower() not in declared | prompts]
        if a.spec is not None:
            v.add(
                "Keine unbekannten Feldnamen",
                not unexpected,
                ("Access behandelt als Parameter: " + ", ".join(unexpected)) if unexpected else "",
            )
        elif check.parameters:
            v.info["parameters"] = check.parameters
            v.add("Parameter", True, "Die Abfrage fragt beim Ausführen nach: " + ", ".join(check.parameters), critical=False)
        if a.spec is not None:
            columns = expected_columns(a.spec)
            if columns is not None:
                v.add(
                    "Ausgabespalten",
                    [c.lower() for c in check.fields] == [c.lower() for c in columns],
                    ", ".join(check.fields),
                )
        if kind in READ_ONLY_KINDS and not check.parameters:
            _, rows = e.queries.run_select(name, limit=_COMPARE_LIMIT)
            v.info["row_count"] = len(rows)
            v.info["sample_rows"] = rows[:5]
            v.info["columns"] = check.fields
            v.add("Abfrage ausführbar", True, f"{len(rows)} Datensätze")
            if a.spec is not None:
                self._check_sort(a, check.fields, rows, v)
            self._check_same_result(e, a, stored_sql, rows, v)

    def _check_same_result(self, e: AccessEngine, a: m.CreateQuery, stored_sql: str, rows: list[list[Any]],
                           v: Verification) -> None:
        """Weicht das gespeicherte SQL vom verlangten ab (z. B. nach einer SQL-Reparatur), müssen beide über eine eigene
        DAO-Verbindung dieselben Datensätze liefern – eine still entfernte Bedingung fällt so auf."""
        from app.agent.executor import AccessExecutor

        try:
            requested = AccessExecutor().resolve_sql(a, self._schema_of(e))
        except WidsError:
            return
        if requested.strip().rstrip(";") == stored_sql.strip().rstrip(";"):
            return
        if len(rows) >= _COMPARE_LIMIT:
            v.add("Ergebnis wie verlangt", True, f"mehr als {_COMPARE_LIMIT} Datensätze – nicht verglichen", critical=False)
            return
        try:
            _, wanted, _ = e.queries.run_select_readonly(requested, limit=_COMPARE_LIMIT + 1)
        except WidsError as exc:
            # nur Hinweis (sonst schlüge jede legitime Reparatur fehl) – außer die Reparatur hat Bedingungen entfernt
            def clauses(sql: str) -> set[str]:  # ohne [Bezeichner] und 'Literale' – „[Where]“ ist keine Bedingung
                plain = re.sub(r"\[[^\]]*\]|'[^']*'|\"[^\"]*\"", " ", sql)
                return {k for k in ("where", "having") if re.search(rf"\b{k}\b", plain, re.I)}

            dropped = sorted(k.upper() for k in clauses(requested) - clauses(stored_sql))
            v.add("Verlangtes Ergebnis vergleichbar", False, f"verlangtes SQL nicht ausführbar: {exc.user_message}"
                  + (f"; die gespeicherte Abfrage hat keine {'/'.join(dropped)}-Bedingung mehr" if dropped else ""),
                  critical=bool(dropped))
            return

        def key(row: list[Any]) -> str:
            return repr([str(x) for x in row])

        same = sorted(map(key, wanted)) == sorted(map(key, rows))
        v.add("Ergebnis wie verlangt", same, f"{len(rows)} Datensätze gespeichert, {len(wanted)} verlangt")

    @staticmethod
    def _check_sort(a: m.CreateQuery, columns: list[str], rows: list[list[Any]], v: Verification) -> None:
        assert a.spec is not None
        first_sorted = next((f for f in a.spec.fields if f.sort and f.show and not f.aggregate), None)
        if first_sorted is None or len(rows) < 2:
            return
        column = (first_sorted.alias or (first_sorted.field or "")).strip("[]").split(".")[-1].lower()
        lowered = [c.lower() for c in columns]
        if column not in lowered:
            return
        index = lowered.index(column)
        values = [row[index] for row in rows if row[index] is not None]
        if not values or not all(isinstance(x, int | float) for x in values):
            return  # Textsortierung folgt der Access-Sortierreihenfolge – hier nicht nachgebildet
        ordered = sorted(values, reverse=first_sorted.sort == "DESC")
        v.add("Sortierung", values == ordered, "aufsteigend" if first_sorted.sort == "ASC" else "absteigend")

    def _verify_delete_query(self, e: AccessEngine, a: m.DeleteQuery, r: StepResult, v: Verification) -> None:
        v.add("Abfrage entfernt", not e.queries.exists(a.name), a.name)

    def _verify_rename_query(self, e: AccessEngine, a: m.RenameQuery, r: StepResult, v: Verification) -> None:
        new = r.final_name or a.new_name
        v.add("Neuer Name vorhanden", e.queries.exists(new), new)
        if not _same(a.old_name, new):
            v.add("Alter Name entfernt", not e.queries.exists(a.old_name), a.old_name)

    def _verify_run_action_query(self, e: AccessEngine, a: m.RunActionQuery, r: StepResult, v: Verification) -> None:
        affected = int(r.details.get("affected", -1))
        v.add("Aktionsabfrage ausgeführt", affected >= 0, f"{affected} Datensätze betroffen")
        v.add("Mindestens ein Datensatz betroffen", affected > 0, critical=False)
        sql = a.sql or (e.queries.get_sql(a.name) if a.name and e.queries.exists(a.name) else "")
        for table, field_name, where in _updated_fields(sql):
            _check_filled(e, table, field_name, affected, v, scope=where)

    # ------------------------------------------------------------------ Formulare / Berichte
    def _verify_create_form(self, e: AccessEngine, a: m.CreateForm, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.form.name
        v.add("Formular vorhanden", e.objects.form_exists(name), name)
        if not e.objects.form_exists(name):
            return
        info = e.objects.inspect_form(name)
        if a.form.record_source:
            v.add("Datensatzquelle", _same(info.record_source, a.form.record_source), info.record_source)
        sources = {c.control_source.lower() for c in info.controls if c.control_source}
        wanted = [*a.form.fields, *(c.field for c in a.form.controls if c.field)]
        missing = [f for f in wanted if f.lower() not in sources]
        v.add("Alle Felder als Steuerelemente vorhanden", not missing, ", ".join(missing))
        if a.form.layout == "stacked":
            # je verlangter Feldspalte ein eigenes Layout (Access rastet Positionen im Layout ein – die Spalte zählt)
            detail: dict[str, list[Any]] = {}
            for ctl in info.controls:
                if ctl.control_source and ctl.section == 0:
                    detail.setdefault(ctl.control_source.lower(), []).append(ctl)
            columns: dict[int, set[int]] = {}
            for spec in a.form.controls:
                found = detail.get((spec.field or "").lower(), []) if spec.section == "detail" else []
                # nur eindeutige Zuordnung prüfen (dasselbe Feld zweimal = nicht eindeutig, wird übersprungen)
                if len(found) == 1 and spec.type not in ("subform", "button"):
                    columns.setdefault(spec.layout_column or 0, set()).add(found[0].layout_id)
            if len(columns) > 1:
                ids = [next(iter(s)) for s in columns.values() if len(s) == 1]
                ok = len(ids) == len(columns) and len(set(ids)) == len(ids) and 0 not in ids
                v.add("Feldspalten im Steuerelementlayout", ok,
                      "; ".join(f"Spalte {k + 1}: Layout {sorted(s)}" for k, s in sorted(columns.items())))
            _check_layout_widths(info, columns, a.form.column_widths_cm, v)
        for spec in (ctl for ctl in a.form.controls if ctl.type == "subform"):
            sub = next((x for x in info.controls if x.source_object.split(".")[-1].lower()
                        == (spec.source_object or "").split(".")[-1].lower()), None)
            v.add(f"Unterformular {spec.source_object}", sub is not None)
            if sub is not None and spec.link_master_fields:
                ok = (sub.link_master_fields.lower() == ";".join(spec.link_master_fields).lower()
                      and sub.link_child_fields.lower() == ";".join(spec.link_child_fields).lower())
                v.add("Verknüpfungsfelder", ok, f"{sub.link_master_fields} → {sub.link_child_fields}")
        _check_form_properties(e, name, a.form.properties, v)
        v.info["controls"] = len(info.controls)

    def _verify_create_report(self, e: AccessEngine, a: m.CreateReport, r: StepResult, v: Verification) -> None:
        name = r.final_name or a.report.name
        v.add("Bericht vorhanden", e.objects.report_exists(name), name)
        if not e.objects.report_exists(name):
            return
        info = e.objects.inspect_report(name)
        v.add("Datensatzquelle", _same(info.record_source, a.report.record_source), info.record_source)
        sources = {c.control_source.lower() for c in info.controls if c.control_source}
        missing = [f for f in a.report.fields
                   if f.lower() not in sources and not any(f"[{f.lower()}]" in s for s in sources)]
        v.add("Alle Felder vorhanden", not missing, ", ".join(missing))
        groups = [g.expression.strip("[]").lower() for g in info.group_levels if g.has_header or g.has_footer]
        wanted_groups = [g.field.lower() for g in a.report.group_by if g.header or g.footer]
        v.add("Gruppierung", groups == wanted_groups, ", ".join(groups) or "keine")
        footers = [c.AC_GROUP_LEVEL1_FOOTER + 2 * i for i, g in enumerate(info.group_levels) if g.has_footer]
        for total in a.report.totals:  # jede Summe im RICHTIGEN Bereich: je Gruppenfuß und/oder Berichtsfuß
            func = {"sum": "sum", "count": "count", "avg": "avg", "min": "min", "max": "max"}[total.function]
            arg = "*" if total.field == "*" else f"[{total.field.lower()}]"
            formula = f"={func}({arg})"
            where = {x.section for x in info.controls if x.control_source.replace(" ", "").lower() == formula}
            label = f"{total.function}({total.field})"
            if total.scope in ("group", "both"):
                v.add(f"Gruppenberechnung {label}", bool(footers) and all(s in where for s in footers),
                      "kein Gruppenfuß" if not footers else f"in Bereichen {sorted(where)}")
            if total.scope in ("report", "both"):
                v.add(f"Gesamtberechnung {label} im Berichtsfuß", c.AC_FOOTER in where, f"in Bereichen {sorted(where)}")
        spec = a.report
        levels = [g for g in info.group_levels if g.has_header or g.has_footer]
        for group, found in zip([g for g in spec.group_by if g.header or g.footer], levels, strict=False):
            if group.new_page:
                v.add(f"Neue Seite je „{group.field}“", found.new_page)
            if group.keep_together:
                v.add(f"„{group.field}“ zusammenhalten", found.keep_together)
        if spec.filter:
            v.add("Filter des Berichts", info.properties.get("Filter") == spec.filter, info.properties.get("Filter", ""))
        if spec.order_by:
            v.add("Sortierung des Berichts", info.properties.get("OrderBy") == spec.order_by, info.properties.get("OrderBy", ""))
        if spec.hide_detail:
            v.add("Detailbereich ausgeblendet", info.properties.get("Detailbereich") == "ausgeblendet")
        expressions = {s.replace(" ", "").lower() for s in sources}
        for ctl in spec.controls:
            if ctl.type == "text_box" and ctl.expression:
                v.add(f"Steuerelement {ctl.expression}", ctl.expression.replace(" ", "").lower() in expressions)
        _check_report_layout(info, spec, v)
        _check_page(e, name, spec.page_setup, spec.labels, spec.record_source, v)

    def _verify_rename_object(self, e: AccessEngine, a: m.RenameObject, r: StepResult, v: Verification) -> None:
        exists = e.objects.form_exists if a.object_type == "form" else e.objects.report_exists
        new = r.final_name or a.new_name
        v.add("Neuer Name vorhanden", exists(new), new)
        if not _same(a.old_name, new):
            v.add("Alter Name entfernt", not exists(a.old_name), a.old_name)

    def _verify_delete_object(self, e: AccessEngine, a: m.DeleteObject, r: StepResult, v: Verification) -> None:
        exists = e.objects.form_exists if a.object_type == "form" else e.objects.report_exists
        v.add("Objekt entfernt", not exists(a.name), a.name)


def _check_layout_widths(info: Any, columns: dict[int, set[int]], wanted: list[float], v: Verification) -> None:
    """Verlangte Spaltenbreiten je Layout (Beschriftung, Feld) in Access nachmessen. Das Ergebnis mit der Standardbreite
    beim Anlegen ist zugleich der Beleg für Anforderungen wie „rechte Spalte verschmälern“."""
    from collections import Counter

    from app.access import constants as c
    from app.access.forms_reports import DEFAULT_CONTROL_WIDTH_CM, DEFAULT_LABEL_WIDTH_CM

    if not wanted:
        return
    measured: list[str] = []
    ok = True
    for column, ids in sorted(columns.items()):
        if len(ids) != 1:
            continue
        layout = next(iter(ids))
        members = [x for x in info.controls if x.layout_id == layout and x.section == 0]
        for index, labels in enumerate((True, False)):
            if column * 2 + index >= len(wanted):
                continue
            widths = [x.width for x in members if (x.control_type == c.AC_LABEL) == labels and x.control_type != c.AC_EMPTY_CELL]
            if not widths:
                continue
            actual = Counter(widths).most_common(1)[0][0] / c.TWIPS_PER_CM
            target = wanted[column * 2 + index]
            ok = ok and abs(actual - target) <= 0.15
            measured.append(f"Layout {column + 1} {'Beschriftung' if labels else 'Feld'} {actual:.1f} cm (verlangt {target:.1f})")
    if measured:
        v.add("Spaltenbreiten im Layout", ok, "; ".join(measured)
              + f" – Standard beim Anlegen: Beschriftung {DEFAULT_LABEL_WIDTH_CM} cm, Feld {DEFAULT_CONTROL_WIDTH_CM} cm")


def _check_section_colors(info: Any, wanted: list[Any], v: Verification) -> None:
    """Bereichsfarben exakt aus Access nachlesen (Bereich → BackColor/AlternateBackColor). Optisch ähnliche Lösungen –
    z. B. nur die Steuerelemente eingefärbt oder die Farbe im falschen Bereich – bestehen diese Prüfung nicht."""
    from app.access.forms_reports import section_index

    for spec in wanted:
        index = section_index(spec.section, spec.group)
        back, alternate = info.section_colors.get(index, ("(fehlt)", "(fehlt)"))
        if spec.back_color:
            v.add(f"Hintergrundfarbe {spec.section}", back.lower() == spec.back_color.lower(), back)
        if spec.alternate_back_color:
            v.add(f"Alternative Hintergrundfarbe {spec.section}", alternate.lower() == spec.alternate_back_color.lower(),
                  alternate)


def _cells_covered(target: Any, controls: list[Any], axis: str) -> int:
    """Über wie viele Zeilen/Spalten seines Steuerelementlayouts reicht ein Steuerelement? Gemessen an den Anfängen der
    anderen Zellen desselben Layouts (Access gibt Zeile/Spalte nicht preis)."""
    cells = [x for x in controls if x.layout_id and x.layout_id == target.layout_id]
    if axis == "rows":
        starts, begin, size = sorted({x.top for x in cells}), target.top, target.height
    else:
        starts, begin, size = sorted({x.left for x in cells}), target.left, target.width
    return sum(1 for start in starts if begin - 30 <= start < begin + size - 30)


def _check_control_details(found: Any, change: Any, v: Verification) -> None:
    """Jede verlangte Steuerelement-Änderung am gespeicherten Steuerelement nachlesen – nicht nur „vorhanden“."""
    for wanted_cm, actual, label in ((change.width_cm, found.width, "Breite"), (change.height_cm, found.height, "Höhe"),
                                     (None if found.layout else change.top_cm, found.top, "Lage oben")):
        if wanted_cm is not None:  # im Steuerelementlayout rastet „oben“ ein – dort zählt die Zeile, nicht der Wert
            v.add(f"{label} „{change.name}“", abs(actual - wanted_cm * c.TWIPS_PER_CM) < 30,
                  f"{actual / c.TWIPS_PER_CM:.1f} cm (verlangt {wanted_cm:.1f} cm)")
    flags = [f.strip() for f in found.formatting.split(",") if f.strip()]
    for wanted, word in ((change.bold, "fett"), (change.italic, "kursiv"),
                         (None if change.visible is None else not change.visible, "unsichtbar")):
        if wanted is not None:
            v.add(f"„{change.name}“: {word}" + ("" if wanted else " – nein"), (word in flags) == wanted,
                  found.formatting or "Standard")
    colors = ((change.back_color, "Hintergrund", "#FFFFFF"), (change.fore_color, "Schrift", "#000000"))
    for color, prefix, default in colors:
        if not color or (prefix == "Hintergrund" and (change.transparent
                                                    or found.control_type not in (c.AC_TEXT_BOX, c.AC_LABEL,
                                                                                  c.AC_COMBO_BOX))):
            continue  # Hintergrund liest inspect nur bei Text-/Bezeichnungs-/Kombinationsfeldern
        if color.upper() == default:  # Standardfarbe erscheint nicht in der Formatierung – keine andere darf dastehen
            ok = not any(f.startswith(prefix + " #") for f in flags)
        else:
            ok = f"{prefix} {color.upper()}" in flags
        v.add(f"„{change.name}“: {prefix} {color.upper()}", ok, found.formatting or "Standard")

"""Universelle Testmatrix: WIDS hängt nicht von Datei-, Übungs- oder Objektnamen ab.

Dieselbe mehrteilige Aufgabe läuft auf Datenbanken mit beliebigen Namen („0201_U1“, „CD-Sammlung“, „Datenbank1“,
Umlaute/Leerzeichen/Sonderzeichen) und einer eigenen, unbekannten Struktur (Leerzeichen, Bindestriche und Umlaute in
Tabellen- und Feldnamen). Geprüft wird jeweils unabhängig mit eigenem SQL bzw. direkt aus Access – nicht nur anhand
des WIDS-Berichts. Die KI-Antworten sind vorgegeben (offline); die Planung unbekannter Aufgaben mit echter KI deckt
das nicht ab.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from app.access.constants import AccessType
from app.access.service import AccessService
from app.access.specs import FieldSpec
from app.agent.orchestrator import AutoInteraction
from app.tasks.models import CapturedTask
from tests.helpers import build_harness, solution

pytestmark = [pytest.mark.access]

MEMBERS = "Mitglieder Liste"


def verein(engine: Any) -> None:
    """Unbekannte Struktur: Leerzeichen, Bindestrich und Umlaute in Namen, eigene Schlüssel, Datums-/Währungsfelder."""
    engine.tables.create_table(MEMBERS, [
        FieldSpec(name="Mitglieds-Nr", type=AccessType.LONG), FieldSpec(name="Familien Name", size=60),
        FieldSpec(name="Ort", size=40), FieldSpec(name="Beitrag", type=AccessType.CURRENCY),
        FieldSpec(name="Eintritt", type=AccessType.DATETIME)], ["Mitglieds-Nr"])
    engine.tables.create_table("Kurse", [
        FieldSpec(name="KursID", type=AccessType.LONG), FieldSpec(name="Kursname", size=60),
        FieldSpec(name="Gebühr", type=AccessType.CURRENCY)], ["KursID"])
    engine.tables.create_table("Buchungen", [
        FieldSpec(name="BuchungID", type=AccessType.AUTONUMBER), FieldSpec(name="Mitglieds-Nr", type=AccessType.LONG),
        FieldSpec(name="KursID", type=AccessType.LONG), FieldSpec(name="Datum", type=AccessType.DATETIME)], ["BuchungID"])
    engine.tables.insert_records(MEMBERS, [
        {"Mitglieds-Nr": 1, "Familien Name": "Öztürk", "Ort": "Imst", "Beitrag": 120, "Eintritt": "01.03.2020"},
        {"Mitglieds-Nr": 2, "Familien Name": "Bauer", "Ort": "Landeck", "Beitrag": 80, "Eintritt": "15.06.2021"},
        {"Mitglieds-Nr": 3, "Familien Name": "Müller", "Ort": "Imst", "Beitrag": 150, "Eintritt": "02.01.2019"},
        {"Mitglieds-Nr": 4, "Familien Name": "Gruber", "Ort": "Telfs", "Beitrag": 60, "Eintritt": "10.10.2022"},
        {"Mitglieds-Nr": 5, "Familien Name": "Strasser", "Ort": "Imst", "Beitrag": 95, "Eintritt": "05.05.2023"}])
    engine.tables.insert_records("Kurse", [{"KursID": 10, "Kursname": "Yoga", "Gebühr": 40},
                                           {"KursID": 20, "Kursname": "Pilates", "Gebühr": 55},
                                           {"KursID": 30, "Kursname": "Zumba", "Gebühr": 35}])
    engine.tables.insert_records("Buchungen", [{"Mitglieds-Nr": m, "KursID": k, "Datum": "01.02.2024"}
                                               for m, k in ((1, 10), (1, 20), (2, 10), (3, 30), (3, 10), (4, 20))])


UMSATZ_SQL = ("SELECT k.Kursname, Count(b.BuchungID) AS Anzahl, Sum(k.[Gebühr]) AS Umsatz "
              "FROM Kurse AS k INNER JOIN Buchungen AS b ON k.KursID = b.KursID "
              "GROUP BY k.Kursname HAVING Count(b.BuchungID) >= 1 ORDER BY Sum(k.[Gebühr]) DESC")
OHNE_SQL = (f"SELECT [Familien Name], Ort FROM [{MEMBERS}] WHERE [Mitglieds-Nr] NOT IN "
            "(SELECT [Mitglieds-Nr] FROM Buchungen) ORDER BY [Familien Name]")

PLAN = [
    {"title": "Mitglieder ↔ Buchungen", "action": {"action": "create_relationship", "table": MEMBERS,
                                                    "foreign_table": "Buchungen", "enforce_integrity": True,
                                                    "fields": [{"primary": "Mitglieds-Nr", "foreign": "Mitglieds-Nr"}]}},
    {"title": "Kurse ↔ Buchungen", "action": {"action": "create_relationship", "table": "Kurse",
                                               "foreign_table": "Buchungen", "enforce_integrity": True,
                                               "fields": [{"primary": "KursID", "foreign": "KursID"}]}},
    {"title": "E-Mail-Feld mit Gültigkeitsregel", "action": {"action": "add_field", "table": MEMBERS, "field": {
        "name": "E-Mail", "type": "short_text", "size": 120, "validation_rule": 'Is Null Or Like "*@*"',
        "validation_text": "Bitte eine E-Mail-Adresse eingeben."}}},
    {"title": "Umsatz je Kurs (Join, Gruppierung, HAVING)", "action": {"action": "create_query", "name": "Umsatz je Kurs",
                                                                        "sql": UMSATZ_SQL}},
    {"title": "Mitglieder ohne Buchung (Unterabfrage)", "action": {"action": "create_query",
                                                                  "name": "Mitglieder ohne Buchung", "sql": OHNE_SQL}},
    {"title": "Formular mit bedingter Formatierung", "action": {"action": "create_form", "form": {
        "name": "Mitglieder bearbeiten", "record_source": MEMBERS,
        "fields": ["Mitglieds-Nr", "Familien Name", "Ort", "Beitrag"], "layout": "stacked"}}},
    {"title": "Beitrag > 100 hervorheben", "action": {"action": "set_conditional_format", "object_type": "form",
                                                       "name": "Mitglieder bearbeiten", "control": "Beitrag",
                                                       "rules": [{"expression": "[Beitrag]>100", "back_color": "#FFF2CC",
                                                                  "bold": True}]}},
    {"title": "Zweispaltige Kursliste mit Summe", "action": {"action": "create_report", "report": {
        "name": "Kursliste", "record_source": "Kurse", "fields": ["Kursname", "Gebühr"],
        "totals": [{"field": "Gebühr", "function": "sum", "scope": "report"}],
        "page_setup": {"columns": 2, "column_spacing_cm": 0.5, "column_layout": "down"}}}},
]
TASK = ("1. Verknüpfen Sie die Mitglieder und die Kurse mit den Buchungen (referenzielle Integrität). 2. Ergänzen Sie ein "
        "Feld E-Mail mit Gültigkeitsprüfung. 3. Erstellen Sie eine Abfrage mit Anzahl und Umsatz je Kurs, absteigend. "
        "4. Listen Sie alle Mitglieder ohne Buchung. 5. Erstellen Sie ein Formular und heben Sie Beiträge über 100 hervor. "
        "6. Erstellen Sie eine zweispaltige Kursliste mit der Summe der Gebühren.")


def _create(path: Path) -> None:
    service = AccessService(operation_timeout_s=120)
    try:
        service.create_database(str(path), verein)
    finally:
        service.shutdown()


@pytest.mark.access_app
@pytest.mark.parametrize("file_name", ["0201_U1.accdb", "CD-Sammlung.accdb", "Datenbank1.accdb",
                                       "Übung (Kopie) #2 – Schularbeit ä ö ü.accdb"])
def test_same_task_logic_works_regardless_of_file_name(file_name: str, tmp_path: Path) -> None:
    """Gleiche mehrteilige Aufgabe, unterschiedliche Dateinamen: identisches, unabhängig bestätigtes Ergebnis."""
    db = tmp_path / file_name
    _create(db)
    h = build_harness(db, [solution(PLAN, task_type="mixed", requirements=[f"Teil {i}" for i in range(1, 7)])], tmp_path)
    try:
        prepared = h.pipeline.prepare(CapturedTask(text=TASK, method="manual"))
        outcome = h.pipeline.execute(prepared, AutoInteraction())
        assert outcome.status == "erfolgreich", (outcome.error, [(r.step_id, r.status, r.message) for r in outcome.results])
        assert all(v.passed for v in outcome.verifications), [(v.step_id, [c.name for c in v.failed_checks])
                                                               for v in outcome.verifications]
        assert outcome.backup_path and Path(outcome.backup_path).is_file()

        def independent(e: Any) -> None:
            relations = {(r.table, r.foreign_table): r.enforce_integrity for r in e.relations.list_relations()}
            assert relations == {(MEMBERS, "Buchungen"): True, ("Kurse", "Buchungen"): True}
            rows = e.queries.run_select("Umsatz je Kurs")[1]
            own = e.queries.run_select_sql(UMSATZ_SQL)[1]
            assert rows == own and [r[0] for r in rows] == ["Yoga", "Pilates", "Zumba"], rows
            assert [r[0] for r in e.queries.run_select("Mitglieder ohne Buchung")[1]] == ["Strasser"]
            field = e.schema().table(MEMBERS).field("E-Mail")
            assert field is not None and "@" in field.validation_rule
            assert e.objects.inspect_form("Mitglieder bearbeiten").record_source == MEMBERS
            rules = e.objects.conditional_formats("form", "Mitglieder bearbeiten").get("Beitrag", [])
            assert any("100" in r for r in rules), rules
            page = e.objects.page_setup("Kursliste")
            assert page["ItemsAcross"] == 2 and bool(page["DefaultSize"]) and page["ItemLayout"] == 1954, page  # erst nach unten

        h.access.run(independent)
    finally:
        h.close()


@pytest.mark.access_app
def test_report_columns_and_data_binding_on_existing_objects(tmp_path: Path) -> None:
    """Neue Access-Fähigkeiten: vorhandenen Bericht mehrspaltig einrichten, Datenquelle wechseln und den
    Steuerelementinhalt (Ausdruck) ändern; vorhandenes Formular an eine Abfrage binden – alles unabhängig nachgelesen."""
    from app.access.specs import (
        ControlChange,
        FormChangeSpec,
        FormSpec,
        PageSetupSpec,
        ReportChangeSpec,
        ReportSpec,
    )
    from app.agent.executor import StepResult
    from app.agent.verifier import ResultVerifier
    from app.tasks import models as m

    db = tmp_path / "Aufgabe.accdb"
    _create(db)
    service = AccessService(operation_timeout_s=120)
    service.connect(str(db), prefer_running=False)
    try:
        def build(e: Any) -> tuple[str, list[Any]]:
            e.queries.create_query("Imster", f"SELECT * FROM [{MEMBERS}] WHERE Ort = 'Imst'")
            e.objects.create_report(ReportSpec(name="Liste", record_source=MEMBERS, fields=["Familien Name", "Ort"]))
            e.objects.create_form(FormSpec(name="Eingabe", record_source=MEMBERS, fields=["Familien Name", "Ort"]))
            info = e.objects.inspect_report("Liste")
            # alles, was über 9,4 cm hinausragt, ohne neue Überdeckung einpassen: rechte Kante auf 9,4 cm kürzen,
            # ganz rechts liegende Elemente (z. B. Seitenzahl) an den rechten Rand der schmalen Spalte schieben
            fit = []
            for ctl in info.controls:
                left, width = ctl.left / 567, ctl.width / 567
                if left + width > 9.4:
                    width = min(width, 3.0) if left >= 8.4 else 9.4 - left
                    fit.append(ControlChange(name=ctl.name, left_cm=9.4 - width if left >= 8.4 else left, width_cm=width))
            return next(c.name for c in info.controls if c.control_source.lower() == "ort"), fit

        ort, fit = service.run(build)
        # zwei Spalten à 9,6 cm + 0,4 cm Abstand = 19,6 cm ≤ 21 cm − 2 × 0,5 cm Rand (Breitenprüfung muss bestehen)
        page = PageSetupSpec(columns=2, column_spacing_cm=0.4, column_layout="across", margin_left_cm=0.5,
                             margin_right_cm=0.5)
        report_change = ReportChangeSpec(record_source="Imster", page_setup=page, width_cm=9.6,
                                         control_changes=[*fit, ControlChange(name=ort, control_source='="Ort: " & [Ort]')])
        form_change = FormChangeSpec(record_source="Imster")
        steps = [m.PlanStep(id=1, title="Bericht", action=m.ModifyReport(name="Liste", changes=report_change)),
                 m.PlanStep(id=2, title="Formular", action=m.ModifyForm(name="Eingabe", changes=form_change))]

        def verify(e: Any) -> list[Any]:
            return [ResultVerifier().verify(e, s, StepResult(step_id=s.id, message="")) for s in steps]

        before = service.run(verify)
        assert not any(v.passed for v in before), "vorher nicht erfüllt – die Prüfung liest wirklich nach"
        service.run(lambda e: e.objects.modify_report("Liste", report_change))
        service.run(lambda e: e.objects.modify_form("Eingabe", form_change))
        after = service.run(verify)
        assert all(v.passed for v in after), [(c.name, c.detail) for v in after for c in v.failed_checks]
        names = {c.name for v in after for c in v.checks}
        assert {"2 Spalte(n)", "Spaltenabstand", "Spaltenlayout erst quer", "Berichtsbreite",
                "Berichtsbreite passt auf die Seite", "Datenquelle des Berichts", f"Inhalt „{ort}“",
                "Datenquelle des Formulars", "Gebundene Felder in der neuen Datenquelle des Formulars"} <= names
        stored = service.run(lambda e: e.objects.page_setup("Liste"))
        assert stored["ItemsAcross"] == 2 and bool(stored["DefaultSize"]) and stored["ItemLayout"] == 1953

        # Gegenproben: Datenquelle ohne das gebundene Feld „Ort“ → nicht bestanden; 3 Spalten passen nicht → nicht bestanden
        service.run(lambda e: e.queries.create_query("NurBeitrag", f"SELECT [Mitglieds-Nr], Beitrag FROM [{MEMBERS}]"))
        broken = FormChangeSpec(record_source="NurBeitrag")
        service.run(lambda e: e.objects.modify_form("Eingabe", broken))
        bad = service.run(lambda e: ResultVerifier().verify(e, m.PlanStep(id=3, title="x", action=m.ModifyForm(
            name="Eingabe", changes=broken)), StepResult(step_id=3, message="")))
        assert not bad.passed and any("fehlen" in c.detail for c in bad.failed_checks), [(c.name, c.detail) for c in bad.checks]
        too_wide = ReportChangeSpec(page_setup=PageSetupSpec(columns=3, column_spacing_cm=0.4))
        service.run(lambda e: e.objects.modify_report("Liste", too_wide))
        wide = service.run(lambda e: ResultVerifier().verify(e, m.PlanStep(id=4, title="y", action=m.ModifyReport(
            name="Liste", changes=too_wide)), StepResult(step_id=4, message="")))
        assert {c.name for c in wide.failed_checks} == {"Berichtsbreite passt auf die Seite"}, [(c.name, c.detail)
                                                                                             for c in wide.checks]
    finally:
        service.shutdown()


def test_faulty_start_state_and_name_conflict_in_unknown_structure(tmp_path: Path) -> None:
    """Fehlerhafter Ausgangszustand (verwaiste Buchung verhindert die referenzielle Integrität) und Namenskonflikt
    (Abfrage existiert schon): WIDS sucht einen nicht destruktiven Weg, löscht nichts und legt nichts doppelt an."""
    db = tmp_path / "Golfclub.accdb"
    _create(db)
    repair = {"steps": [{"title": "Beziehung ohne referenzielle Integrität", "action": {
        "action": "create_relationship", "table": MEMBERS, "foreign_table": "Buchungen", "enforce_integrity": False,
        "fields": [{"primary": "Mitglieds-Nr", "foreign": "Mitglieds-Nr"}]}}], "notes": []}
    plan = [PLAN[0], {"action": {"action": "create_query", "name": "Umsatz je Kurs", "sql": UMSATZ_SQL}}]
    h = build_harness(db, [solution(plan, task_type="mixed", requirements=["Beziehung", "Abfrage"]), repair], tmp_path)
    try:
        h.access.run(lambda e: e.tables.insert_records("Buchungen", [{"Mitglieds-Nr": 999, "KursID": 10}]))
        h.access.run(lambda e: e.queries.create_query("Umsatz je Kurs", "SELECT Kursname FROM Kurse"))
        prepared = h.pipeline.prepare(CapturedTask(text="Verknüpfen Sie Mitglieder und Buchungen und erstellen Sie die "
                                                        "Abfrage Umsatz je Kurs.", method="manual"))
        outcome = h.pipeline.execute(prepared, AutoInteraction(conflict="rename"))
        results = {r.step_id: r for r in outcome.results}
        assert results[1].status == "ok" and "Nach Korrektur" in results[1].message, results[1].message
        assert h.access.run(lambda e: e.tables.count_records("Buchungen", "[Mitglieds-Nr] = 999")) == 1, "nichts gelöscht"
        queries = {q.name for q in h.access.schema(refresh=True).queries}
        assert {"Umsatz je Kurs", "Umsatz je Kurs1"} <= queries or {"Umsatz je Kurs", "Umsatz je Kurs (2)"} <= queries \
            or len([q for q in queries if q.startswith("Umsatz je Kurs")]) == 2, queries
        assert h.access.run(lambda e: e.queries.get_sql("Umsatz je Kurs")).upper().startswith("SELECT KURSNAME"), \
            "vorhandene Abfrage nicht überschrieben"
        renamed = next(q for q in queries if q.startswith("Umsatz je Kurs") and q != "Umsatz je Kurs")
        assert h.access.run(lambda e: e.queries.run_select(renamed))[1][0][0] == "Yoga", "neue Abfrage unter neuem Namen"
        assert outcome.status in ("erfolgreich", "unklar", "teilweise"), outcome.error
    finally:
        h.close()


def _pdf_positions(pdf: Path) -> dict[str, tuple[int, int]]:
    """Textpositionen (x, y) aus einem Access-PDF: Inhaltsströme entpacken, Tm-Lage je Text (ohne Zusatzpakete)."""
    import re
    import zlib

    out: dict[str, tuple[int, int]] = {}
    for raw in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf.read_bytes(), re.S):
        try:
            text = zlib.decompress(raw).decode("latin-1")
        except zlib.error:
            continue
        x = y = 0.0
        for m in re.finditer(r"([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) Tm|\[(.*?)\]\s*TJ|"
                             r"\((.*?)\)\s*Tj", text):
            if m.group(1):
                x, y = float(m.group(5)), float(m.group(6))
            else:
                word = "".join(re.findall(r"\((.*?)\)", m.group(7))) if m.group(7) else m.group(8)
                if word:
                    out.setdefault(word, (round(x), round(y)))
    return out


@pytest.mark.access_app
def test_column_direction_matches_the_real_print_order(tmp_path: Path) -> None:
    """Gemessen statt angenommen: Etiketten (wie der Assistent) drucken erst quer – Name02 steht NEBEN Name01;
    ein Bericht mit column_layout "down" druckt erst nach unten – Name11 beginnt oben in Spalte 2."""
    from app.access.engine import AccessEngine
    from app.access.specs import PageSetupSpec, ReportChangeSpec, ReportControlSpec, ReportSpec

    e = AccessEngine.create(tmp_path / "druck.accdb")
    try:
        e.tables.create_table("T", [FieldSpec(name="ID", type=AccessType.LONG), FieldSpec(name="N", size=20)], ["ID"])
        e.tables.insert_records("T", [{"ID": i, "N": f"Name{i:02d}"} for i in range(1, 41)])
        e.objects.create_report(ReportSpec(name="Etiketten", record_source="T", fields=["N"],
                                           sort_by=[{"field": "ID", "direction": "ASC"}],
                                           labels={"width_cm": 8.0, "height_cm": 2.0, "across": 2, "down": 10},
                                           controls=[ReportControlSpec(type="text_box", section="detail", expression="N")]))
        e.objects.create_report(ReportSpec(name="Spalten", record_source="T", fields=["N"],
                                           sort_by=[{"field": "ID", "direction": "ASC"}],
                                           labels={"width_cm": 8.0, "height_cm": 2.0, "across": 2, "down": 10},
                                           controls=[ReportControlSpec(type="text_box", section="detail", expression="N")]))
        e.objects.modify_report("Spalten", ReportChangeSpec(page_setup=PageSetupSpec(column_layout="down")))
        app = e.session.application()
        positions = {}
        for name in ("Etiketten", "Spalten"):
            pdf = tmp_path / f"{name}.pdf"
            app.DoCmd.OutputTo(3, name, "PDF Format (*.pdf)", str(pdf))  # 3 = acOutputReport
            positions[name] = _pdf_positions(pdf)
        across, down = positions["Etiketten"], positions["Spalten"]
        assert across["Name02"][1] == across["Name01"][1] and across["Name02"][0] > across["Name01"][0], across
        assert down["Name02"][0] == down["Name01"][0] and down["Name11"][1] == down["Name01"][1], down
    finally:
        e.close()


@pytest.mark.access_app
@pytest.mark.parametrize("layout", ["tabular", "columnar"])
def test_new_multi_column_report_fits_the_page(layout: str, tmp_path: Path) -> None:
    """Codex-Befund: Ein NEU erstellter mehrspaltiger Bericht muss mit allen Spalten auf die Seite passen – in beiden
    Layouts; die Prüfung liest Breite, Spaltenzahl und Ränder aus dem gespeicherten Bericht."""
    from app.agent.executor import StepResult
    from app.agent.verifier import ResultVerifier
    from app.tasks import models as m

    db = tmp_path / f"spalten_{layout}.accdb"
    _create(db)
    service = AccessService(operation_timeout_s=120)
    service.connect(str(db), prefer_running=False)
    try:
        step = m.PlanStep(id=1, title="Bericht", action=m.CreateReport.model_validate({"action": "create_report", "report": {
            "name": "Mitgliederliste", "record_source": MEMBERS, "fields": ["Familien Name", "Ort", "Beitrag"],
            "layout": layout, "page_setup": {"columns": 2, "column_spacing_cm": 0.5}}}))
        service.run(lambda e: e.objects.create_report(step.action.report))  # type: ignore[union-attr]
        result = service.run(lambda e: ResultVerifier().verify(e, step, StepResult(step_id=1, message="")))
        assert result.passed, [(c.name, c.detail) for c in result.failed_checks]
        assert {"2 Spalte(n)", "Berichtsbreite passt auf die Seite"} <= {c.name for c in result.checks}
    finally:
        service.shutdown()


@pytest.mark.access_app
def test_moved_control_must_not_cover_another(tmp_path: Path) -> None:
    """Live-Befund 14.11. (0501/U4): ein verschobenes Feld lag über einem anderen und die Schrittprüfung bestand
    trotzdem. Jetzt: Überdeckung durch ein geändertes Steuerelement = Prüfung nicht bestanden (→ Selbstkorrektur);
    sauber daneben = bestanden."""
    from app.access.specs import ControlChange, ReportChangeSpec, ReportSpec
    from app.agent.executor import StepResult
    from app.agent.verifier import ResultVerifier
    from app.tasks import models as m

    db = tmp_path / "Abschlag.accdb"
    _create(db)
    service = AccessService(operation_timeout_s=120)
    service.connect(str(db), prefer_running=False)
    try:
        def build(e: Any) -> tuple[Any, Any]:
            e.objects.create_report(ReportSpec(name="Liste", record_source=MEMBERS, fields=["Familien Name", "Ort", "Beitrag"]))
            info = e.objects.inspect_report("Liste")
            detail = {x.control_source: x for x in info.controls if x.control_source in ("Familien Name", "Beitrag")}
            return detail["Familien Name"], detail["Beitrag"]

        first, beitrag = service.run(build)

        def move(left_cm: float) -> Any:
            change = ReportChangeSpec(control_changes=[ControlChange(name=beitrag.name, left_cm=left_cm)])
            service.run(lambda e: e.objects.modify_report("Liste", change))
            step = m.PlanStep(id=1, title="verschieben", action=m.ModifyReport(name="Liste", changes=change))
            return service.run(lambda e: ResultVerifier().verify(e, step, StepResult(step_id=1, message="")))

        # auf ein anderes Feld geschoben: der Bericht ordnet die Zeile selbst neu (keine Überdeckung, Reihenfolge
        # wie angefordert) – die Prüfung besteht nur, weil nichts mehr überdeckt ist
        covering = move(first.left / 567 + 0.5)
        assert covering.passed, [(c.name, c.detail) for c in covering.failed_checks]
        assert next(c for c in covering.checks if c.name == "Steuerelemente überlappen nicht").passed
        clean = move(14.0)
        assert clean.passed, [(c.name, c.detail) for c in clean.failed_checks]
    finally:
        service.shutdown()


@pytest.mark.access_app
def test_report_layout_reflows_rows_after_moves_and_additions(tmp_path: Path) -> None:
    """Live-Befund 0501/U4 nachgestellt (ohne te.comp-Daten): erstes Feld per control_changes HINTER ein anderes
    geschoben (überdeckt es), zwei neue Felder ergänzt. Erwartet: Zeile neu geordnet – keine Überdeckung, Reihenfolge wie
    gewünscht, Beginn am alten linken Rand, Seitenkopf bündig zum Detailbereich, Prüfung bestanden."""
    from app.access import constants as c
    from app.access.specs import ControlChange, ReportChangeSpec, ReportControlSpec, ReportSpec
    from app.agent.executor import StepResult
    from app.agent.verifier import ResultVerifier
    from app.tasks import models as m

    db = tmp_path / "Abschlag.accdb"
    _create(db)
    service = AccessService(operation_timeout_s=120)
    service.connect(str(db), prefer_running=False)
    try:
        def build(e: Any) -> dict[str, Any]:
            e.objects.create_report(ReportSpec(name="Buchungsliste", record_source="Buchungen",
                                               fields=["Mitglieds-Nr", "KursID", "Datum"]))
            return {x.name: x for x in e.objects.inspect_report("Buchungsliste").controls}

        ctl = service.run(build)
        start = ctl["Mitglieds-Nr"].left / 567
        datum = ctl["Datum"]
        behind = datum.left / 567 + 0.1  # wie die KI: „Mitglied nach Datum“, aber Datum nicht verschoben → Überdeckung
        change = ReportChangeSpec(
            control_changes=[ControlChange(name="Mitglieds-Nr", left_cm=behind), ControlChange(name="lblMitglieds-Nr", left_cm=behind)],
            controls=[ReportControlSpec(type="text_box", section="detail", expression="BuchungID", name="BuchungID",
                                        left_cm=behind + 2.5, width_cm=2.0),
                      ReportControlSpec(type="label", section="page_header", caption="Buchung", name="lblBuchungID",
                                        left_cm=behind + 2.5, width_cm=2.0)])
        service.run(lambda e: e.objects.modify_report("Buchungsliste", change))
        step = m.PlanStep(id=1, title="umordnen", action=m.ModifyReport(name="Buchungsliste", changes=change))
        result = service.run(lambda e: ResultVerifier().verify(e, step, StepResult(step_id=1, message="")))
        assert result.passed, [(x.name, x.detail) for x in result.failed_checks]

        after = service.run(lambda e: e.objects.inspect_report("Buchungsliste").controls)
        detail = sorted((x for x in after if x.section == c.AC_DETAIL and x.control_type == c.AC_TEXT_BOX
                         and x.control_source and not x.control_source.startswith("=")), key=lambda x: x.left)
        assert [x.control_source for x in detail] == ["KursID", "Datum", "Mitglieds-Nr", "BuchungID"]
        assert abs(detail[0].left / 567 - start) < 0.05, "beginnt am bisherigen linken Rand"
        for a, b in itertools.pairwise(detail):
            assert a.left + a.width <= b.left, f"{a.name} überdeckt {b.name}"
        heads = {x.name: x.left for x in after if x.section == c.AC_PAGE_HEADER}
        for box, label in (("Mitglieds-Nr", "lblMitglieds-Nr"), ("BuchungID", "lblBuchungID"), ("Datum", "lblDatum")):
            assert abs(heads[label] - next(x.left for x in detail if x.name == box)) < 30, f"{label} bündig zu {box}"
    finally:
        service.shutdown()

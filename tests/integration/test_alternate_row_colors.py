"""Alternative Zeilenfarben – Befund aus der te.comp-Übung E-01 SI („Jede zweite Zeile farblich unterlegen“, 0 von 1
Punkt): WIDS setzte die Datenblattfarbe der Tabelle, verlangt war der Detailbereich des Berichts.

Die Prüfungen lesen das gespeicherte Ergebnis unabhängig über den Access-Textexport (SaveAsText) bzw. direkt aus DAO,
und optisch ähnliche, aber technisch falsche Lösungen müssen vom Verifier abgelehnt werden.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access.engine import AccessEngine
from app.access.specs import ReportChangeSpec, ReportSpec
from app.agent.executor import StepResult
from app.agent.verifier import ResultVerifier
from app.tasks import models as m
from app.utils.sampledb import create_sample_database

pytestmark = pytest.mark.access_app

COLOR = "#DDEBF7"  # BGR-Zahl 16247773


def _report(engine: AccessEngine) -> None:
    engine.objects.create_report(ReportSpec.model_validate(
        {"name": "rptTelefonliste", "record_source": "Kunden", "fields": ["Nachname", "Vorname", "Ort"],
         "group_by": [{"field": "Ort"}]}))


def _saved_section_text(db: Path, report: str, tmp_path: Path) -> str:
    """Unabhängig vom WIDS-Code: eigenes Access, Berichtsentwurf als Text exportieren."""
    import win32com.client

    app = win32com.client.DispatchEx("Access.Application")
    out = tmp_path / f"{report}.txt"
    try:
        app.OpenCurrentDatabase(str(db))
        app.SaveAsText(3, report, str(out))  # 3 = acReport
    finally:
        app.CloseCurrentDatabase()
        app.Quit()
    raw = out.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("cp1252", errors="replace")
    start = text.index('Name ="Detailbereich"') if 'Name ="Detailbereich"' in text else text.index("Detail")
    return text[max(0, start - 600):start + 200]


def _verify(engine: AccessEngine, changes: dict) -> m.PlanStep:
    step = m.PlanStep.model_validate({"id": 1, "title": "Farbe", "action": {
        "action": "modify_report", "name": "rptTelefonliste", "changes": changes}})
    return ResultVerifier().verify(engine, step, StepResult(step_id=1))


def test_report_detail_alternate_color_is_stored_and_verified(tmp_path: Path) -> None:
    db = create_sample_database(tmp_path / "Telefonliste.accdb")
    engine = AccessEngine.open(db, prefer_running=False)
    try:
        _report(engine)
        changes = {"section_colors": [{"section": "detail", "alternate_back_color": COLOR}]}
        engine.objects.modify_report("rptTelefonliste", ReportChangeSpec.model_validate(changes))
        verification = _verify(engine, changes)
        assert verification.passed, [c for c in verification.checks if not c.passed]
        assert "alternativ #DDEBF7" in engine.objects.inspect_report("rptTelefonliste").properties["Bereichsfarben"]
    finally:
        engine.close()
    assert "AlternateBackColor =16247773" in _saved_section_text(db, "rptTelefonliste", tmp_path)


def test_visually_similar_but_wrong_solutions_are_rejected(tmp_path: Path) -> None:
    """Nur die Steuerelemente eingefärbt, Farbe im falschen Bereich oder eine andere Farbe: keine bestandene Prüfung."""
    db = create_sample_database(tmp_path / "Falsch.accdb")
    engine = AccessEngine.open(db, prefer_running=False)
    wanted = {"section_colors": [{"section": "detail", "alternate_back_color": COLOR}]}
    try:
        _report(engine)
        controls = [str(x.name) for x in engine.objects.inspect_report("rptTelefonliste").controls
                    if x.section == 0 and x.control_source]
        engine.objects.modify_report("rptTelefonliste", ReportChangeSpec.model_validate(
            {"control_changes": [{"name": n, "back_color": COLOR} for n in controls]}))
        assert not _verify(engine, wanted).passed, "nur Steuerelemente eingefärbt"
        engine.objects.modify_report("rptTelefonliste", ReportChangeSpec.model_validate(
            {"section_colors": [{"section": "detail", "back_color": COLOR}]}))
        assert not _verify(engine, wanted).passed, "Hintergrund statt alternativer Hintergrund"
        engine.objects.modify_report("rptTelefonliste", ReportChangeSpec.model_validate(
            {"section_colors": [{"section": "group_header", "alternate_back_color": COLOR}]}))
        assert not _verify(engine, wanted).passed, "richtige Eigenschaft im falschen Bereich"
        engine.objects.modify_report("rptTelefonliste", ReportChangeSpec.model_validate(
            {"section_colors": [{"section": "detail", "alternate_back_color": "#FFF2CC"}]}))
        assert not _verify(engine, wanted).passed, "andere Farbe"
    finally:
        engine.close()


def test_datasheet_alternate_color_checks_the_value(tmp_path: Path) -> None:
    """Datenblatt: früher genügte „irgendeine Farbe gesetzt“ – jetzt zählt der gespeicherte Wert."""

    from app.access.tables import set_property

    db = create_sample_database(tmp_path / "Datenblatt.accdb")
    engine = AccessEngine.open(db, prefer_running=False)
    step = m.PlanStep.model_validate({"id": 1, "title": "Datenblatt", "action": {
        "action": "set_datasheet_layout", "object": "Kunden", "layout": {"alternate_back_color": COLOR}}})
    try:
        tabledef = engine.session.db.TableDefs("Kunden")
        set_property(tabledef, "DatasheetAlternateBackColor", 0xCCF2FF, 4)  # andere Farbe (#FFF2CC)
        assert not ResultVerifier().verify(engine, step, StepResult(step_id=1)).passed
        set_property(tabledef, "DatasheetAlternateBackColor", 16247773, 4)
        assert ResultVerifier().verify(engine, step, StepResult(step_id=1)).passed
    finally:
        engine.close()


def test_page_sections_have_no_alternate_color() -> None:
    """Access kennt die alternative Hintergrundfarbe im Seitenkopf/-fuß nicht – schon der Plan wird abgewiesen."""
    with pytest.raises(ValueError, match="Seitenkopf und Seitenfuß"):
        ReportChangeSpec.model_validate({"section_colors": [{"section": "page_header", "alternate_back_color": COLOR}]})


def test_unnamed_report_details_reach_the_plan_and_the_change_list(tmp_path: Path) -> None:
    """E-01 SI: Die Aufgabe nennt den Bericht nicht. Die Analyse muss trotzdem seine Bereichsfarben sehen, und die
    Änderung erscheint im Diagnosebericht (vorher: KI sah nur den Namen, Änderungsliste leer)."""
    from app.agent.orchestrator import AutoInteraction
    from app.tasks.models import CapturedTask
    from tests.helpers import build_harness, solution

    db = create_sample_database(tmp_path / "Zeilenfarbe.accdb")
    engine = AccessEngine.open(db, prefer_running=False)
    try:
        _report(engine)
    finally:
        engine.close()
    plan = solution([{"action": {"action": "modify_report", "name": "rptTelefonliste", "changes": {
        "section_colors": [{"section": "detail", "alternate_back_color": COLOR}]}}}], task_type="report")
    h = build_harness(db, [plan], tmp_path / "h")
    try:
        prepared = h.pipeline.prepare(CapturedTask(text="Jede zweite Zeile soll farbig hinterlegt werden.", method="manual"))
        assert "Bereichsfarben: Detailbereich" in h.provider.requests[0].messages[0].content
        outcome = h.pipeline.execute(prepared, AutoInteraction())
        assert outcome.status == "erfolgreich", outcome.error
        assert any("rptTelefonliste" in c and "alternativ #DDEBF7" in c for c in outcome.changes), outcome.changes
    finally:
        h.close()

"""Befunde aus dem Praxistest 15.11. (Formular mit Steuerelementlayout, zwei Feldspalten):

1. Eine vorhandene Abfrage verwies auf eine nicht vorhandene Tabelle. WIDS meldete sie der KI als normale Abfrage, die
   KI nahm sie als Datenquelle → Schritt scheiterte, die Reparatur verlor die Layoutspalten.
2. Die Schrittprüfung kontrollierte die Feldspalten des Layouts nicht – der Fehler fiel erst am Ende auf.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.orchestrator import AutoInteraction
from app.agent.planner import PlanValidator
from app.tasks import models as m
from app.tasks.models import CapturedTask
from app.utils.sampledb import create_sample_database
from tests.helpers import build_harness, solution

pytestmark = pytest.mark.access

FIELDS_LEFT = ["Nachname", "Vorname", "Ort"]
FIELDS_RIGHT = ["PLZ", "Bundesland"]


def _form_step(source: str) -> dict:
    controls = [{"type": "text_box", "field": f, "layout_column": 0} for f in FIELDS_LEFT]
    controls += [{"type": "text_box", "field": f, "layout_column": 1} for f in FIELDS_RIGHT]
    return {"title": "Formular", "action": {"action": "create_form", "form": {
        "name": "frmKundenLayout", "record_source": source, "layout": "stacked", "controls": controls,
        "column_widths_cm": [3.0, 4.5, 2.5, 2.0]}}}


@pytest.fixture
def broken_query_db(tmp_path: Path) -> Path:
    """Abfrage „qryKaputt“ auf eine Tabelle, die es nicht (mehr) gibt – wie in der Übungsdatenbank."""
    db = create_sample_database(tmp_path / "Formularuebung.accdb")
    h = build_harness(db, [], tmp_path / "h0")
    try:
        h.access.run(lambda e: e.session.db.Execute("SELECT * INTO KundenAlt FROM Kunden"))
        h.access.run(lambda e: e.queries.create_query("qryKaputt", "SELECT Nachname, Ort FROM KundenAlt"))
        h.access.run(lambda e: e.tables.delete_table("KundenAlt"))
    finally:
        h.close()
    return db


def test_non_executable_query_is_flagged_and_rejected_as_data_source(broken_query_db: Path, tmp_path: Path) -> None:
    h = build_harness(broken_query_db, [], tmp_path / "h1")
    try:
        schema = h.access.run(lambda e: e.schema())
    finally:
        h.close()
    query = schema.query("qryKaputt")
    assert query is not None and query.error, "nicht ausführbare Abfrage erkannt"
    assert "NICHT AUSFÜHRBAR" in schema.to_prompt_text(), "die KI erfährt es im Prompt"
    plan = m.ExecutionPlan.model_validate({"steps": [_form_step("qryKaputt") | {"id": 1}]})
    report = PlanValidator().validate(plan, schema)
    assert any("nicht ausführbar" in e for e in report.error_text().splitlines()), report.error_text()
    plan_ok = m.ExecutionPlan.model_validate({"steps": [_form_step("Kunden") | {"id": 1}]})
    assert not PlanValidator().validate(plan_ok, schema).errors


def test_form_with_two_layout_columns_is_verified_per_column(tmp_path: Path) -> None:
    db = create_sample_database(tmp_path / "Zwei Spalten.accdb")
    h = build_harness(db, [solution([_form_step("Kunden")], task_type="form")], tmp_path / "h2")
    provider = h.provider
    try:
        prepared = h.pipeline.prepare(CapturedTask(text="Formular mit zwei Feldspalten erstellen.", method="manual"))
        outcome = h.pipeline.execute(prepared, AutoInteraction())
    finally:
        h.close()
    checks = {c.name: c for v in outcome.verifications for c in v.checks}
    assert outcome.status == "erfolgreich", outcome.error
    assert checks["Feldspalten im Steuerelementlayout"].passed, checks["Feldspalten im Steuerelementlayout"].detail
    widths = checks["Spaltenbreiten im Layout"]
    assert widths.passed and "Feld 4.5 cm" in widths.detail and "Standard beim Anlegen" in widths.detail, widths.detail
    # Praxistest Golfclub: „rechte Spalte verschmälern“ braucht die gemessene Breite samt Standardbreite als Beleg
    from app.ai import prompts

    check = next(r for r in provider.requests if r.system == prompts.REQUIREMENTS_SYSTEM).messages[0].content
    assert "in Access gemessen" in check and "Standard beim Anlegen" in check


def test_lossless_type_widening_runs_in_background_but_narrowing_stops(tmp_path: Path) -> None:
    """Praxistest CD-Sammlung Schritt 2: „Anmerkung“ Kurzer → Langer Text ist verlustfrei und läuft mit der zentralen
    Freigabe im Hintergrund; die Gegenrichtung (kann abschneiden) hält weiterhin an."""
    from app.access.constants import AccessType

    db = create_sample_database(tmp_path / "Typen.accdb")
    widen = {"action": "change_field_type", "table": "Kunden", "field": "Ort", "new_type": "long_text"}
    narrow = {"action": "change_field_type", "table": "Kunden", "field": "Ort", "new_type": "short_text", "size": 50}
    h = build_harness(db, [solution([{"action": widen}], task_type="table"),
                           solution([{"action": narrow}], task_type="table")], tmp_path / "h3")
    try:
        background = AutoInteraction(interactive=False)
        prepared = h.pipeline.prepare(CapturedTask(text="Ort soll langen Text aufnehmen.", method="manual"))
        outcome = h.pipeline.execute(prepared, background)
        assert outcome.status == "erfolgreich", outcome.error
        assert h.access.run(lambda e: e.tables.field_type("Kunden", "Ort")) == AccessType.LONG_TEXT
        prepared = h.pipeline.prepare(CapturedTask(text="Ort wieder kurzer Text mit 50 Zeichen.", method="manual"))
        outcome = h.pipeline.execute(prepared, background)
        assert outcome.status == "blockiert" and "löschende" in outcome.error
        assert h.access.run(lambda e: e.tables.field_type("Kunden", "Ort")) == AccessType.LONG_TEXT
        assert background.requests == [] and background.confirmations == []
    finally:
        h.close()


def test_dead_access_connection_is_dropped_and_reconnected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Praxis 15.11.: nach einem Access-Neustart (te.comp „Neu laden“, derselbe Pfad) scheiterte jedes weitere Alt+W an
    der toten Verbindung. Jetzt wird sie erkannt, verworfen und neu aufgebaut."""
    db = create_sample_database(tmp_path / "Neustart.accdb")
    h = build_harness(db, [], tmp_path / "h4")
    try:
        assert h.access.is_alive()
        session = h.access.run(lambda e: e.session)
        monkeypatch.setattr(session, "is_alive", lambda: False)  # Access weg wie nach einem Neustart durch te.comp
        assert not h.access.is_alive() and h.access.path == "", "tote Verbindung verworfen"
        h.access.connect(str(db), prefer_running=False)
        assert h.access.is_alive() and h.access.run(lambda e: e.tables.table_exists("Kunden"))
    finally:
        h.close()


def test_import_never_guesses_columns_by_position(tmp_path: Path) -> None:
    """Codex-Befund: passten die Überschriften nicht, wurde nach Position importiert (Familienname → Vorname)."""
    from app.access.engine import AccessEngine
    from app.access.specs import FieldSpec
    from app.core.errors import AccessError

    db = create_sample_database(tmp_path / "Import.accdb")
    engine = AccessEngine.open(db, prefer_running=False)
    try:
        engine.tables.create_table("Personen", [FieldSpec(name="Vorname", type="short_text"),
                                                FieldSpec(name="Nachname", type="short_text")])
        rows = [["Familienname", "Vorname"], ["Müller", "Anna"]]
        with pytest.raises(AccessError, match="Familienname"):
            engine.tables.import_rows("Personen", rows)
        assert engine.tables.count_records("Personen") == 0
        engine.tables.import_rows("Personen", rows, fields=[FieldSpec(name="Nachname", type="short_text"),
                                                            FieldSpec(name="Vorname", type="short_text")])
        _, data = engine.tables.read_sql("SELECT Vorname, Nachname FROM Personen")
        assert data == [["Anna", "Müller"]]
    finally:
        engine.close()

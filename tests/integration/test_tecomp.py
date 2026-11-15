"""Tests mit der real installierten te.comp-training-Software.

* ``tecomp``: braucht nur die Installation (Original-Aufgabentexte und -Übungsvorlagen). Es wird nur gelesen;
  geändert werden ausschließlich Kopien im Testordner.
* ``live_tecomp``: braucht zusätzlich ein laufendes te.comp mit geöffneter Access-Übung. Nur lesend –
  die Übungsdatei des Benutzers wird nicht verändert.

Die KI-Antworten (Plan, Anforderungsprüfung) sind vorgegeben, weil kein API-Schlüssel verfügbar ist. Echt sind:
Aufgabentext, Übungsdatenbank, Ausführung in Access, Ergebnisprüfung auf den echten Daten.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from app.access.service import AccessService
from app.agent.diff import missing_named_objects
from app.agent.orchestrator import AutoInteraction
from app.tasks.models import CapturedTask
from app.vision import capture, tecomp
from tests.helpers import build_harness, solution

pytestmark = [pytest.mark.access, pytest.mark.tecomp]
TEMPLATE = "golf-dr.accdb"  # te.comp-Vorlage der Golf-Übungen (Kapitel 0402)


def _base() -> Path:
    base = tecomp.install_dir()
    if base is None:
        pytest.skip("te.comp-training ist nicht installiert")
    return base


def test_task_text_of_real_exercise() -> None:
    page = tecomp.task_page("04024_12345678.accdb", _base())
    assert page is not None and page.parts[-4:] == ("0402", "intro", "U4", "task.html")
    text = tecomp.html_to_text(page.read_text(encoding="utf-8"))
    # alle drei Teilaufgaben, nicht nur die erste
    for part in ('"Clubmitglieder"', "Clubmitglieder sortiert", "Spielerstatus", "Golfclubs", "Golfplatz"):
        assert part in text
    assert "Überblick" not in text and "Zu Access" not in text and "﻿" not in text
    assert '"Clubmitglieder sortiert"' in text and '"Spielerstatus"' in text, "Namen ohne Leerzeichen in den Anführungszeichen"
    u10 = tecomp.html_to_text(tecomp.task_page("040310_1.accdb", _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert "die Abfrage „50 Jahre“:" in u10 and "„Turnieranzahl“" in u10, "fette Objektnamen werden markiert"
    assert "Hinweis:" in u10 or "„Hinweis:“" not in u10, "fette Beschriftungen bleiben unmarkiert"
    assert "-->" not in tecomp.html_to_text("<p>A</p><!-- alt: <p>B</p> --><p>C</p>") == "A\nC"
    u4 = tecomp.html_to_text(tecomp.task_page("04034_1.accdb", _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert u4.endswith("Objektnamen in blauer Schrift (exakt so verwenden): „Leonardo DiCaprio“, „Robert Redford“, "
                       "„Robert De Niro“"), "die blau geschriebenen Abfragenamen stehen ausdrücklich im Text"
    form_page = tecomp.task_page("03011_1.accdb", _base())
    images = tecomp.task_images(form_page)  # type: ignore[arg-type]
    assert len(images) == 1 and images[0][:8] == b"\x89PNG\r\n\x1a\n", "Abbildung der Formularaufgabe, ohne Symbol"
    two_digit = tecomp.task_page("040311_12345678.accdb", _base())  # Kapitel 0403 hat Übungen bis U11
    assert two_digit is not None and two_digit.parts[-4:] == ("0403", "intro", "U11", "task.html")
    assert tecomp.task_page("04031_12345678.accdb", _base()).parts[-2] == "U1"  # type: ignore[union-attr]
    assert tecomp.task_page("0501S1_12345678.accdb", _base()) is None, "Selbsttests werden nicht zugeordnet"
    assert tecomp.task_page("SI-04_12345678.accdb", _base()) is None


@pytest.fixture
def exercise_04024(tmp_path: Path) -> Path:
    """Kopie der te.comp-Vorlage, reduziert auf den Ausgangszustand der Übung 04024."""
    from app.access.engine import AccessEngine

    target = tmp_path / "04024_test.accdb"
    shutil.copy2(_base() / "access" / "cbt" / "database" / TEMPLATE, target)
    engine = AccessEngine.open(target, prefer_running=False)
    try:
        for relation in engine.relations.list_relations():
            engine.relations.delete_relationship(relation.name)
        for query in engine.queries.list_queries():
            engine.queries.delete_query(query.name)
        for table in engine.schema().tables:
            if table.name not in ("Clubmitglieder", "Golfclubs"):
                engine.tables.delete_table(table.name)
    finally:
        engine.close()
    return target


ALL_FIELDS = ["M-ID", "Familienname", "Vorname", "Geburtsdatum", "Geschlecht", "Status", "Mobiltelefon"]
REQUIREMENTS = [
    "Abfrage „Clubmitglieder sortiert“ mit allen Feldern der Tabelle Clubmitglieder",
    "Clubmitglieder sortiert: alphabetisch nach Familienname und Vorname",
    "Abfrage „Spielerstatus“: zuerst nach Status, dann nach dem Namen sortiert",
    "Spielerstatus: Familienname und Vorname ganz vorne angezeigt",
    "Tabelle Golfclubs in der Datenblattansicht nach Land, dann Golfplatz sortiert",
]
PLAN = [
    {"title": "Abfrage Clubmitglieder sortiert", "covers": [1, 2], "action": {
        "action": "create_query", "name": "Clubmitglieder sortiert", "spec": {"tables": ["Clubmitglieder"], "fields": [
            {"field": "M-ID"}, {"field": "Familienname", "sort": "ASC"}, {"field": "Vorname", "sort": "ASC"},
            {"field": "Geburtsdatum"}, {"field": "Geschlecht"}, {"field": "Status"}, {"field": "Mobiltelefon"}]}}},
    {"title": "Abfrage Spielerstatus", "covers": [3, 4], "action": {
        "action": "create_query", "name": "Spielerstatus", "spec": {"tables": ["Clubmitglieder"], "fields": [
            {"field": "Status", "sort": "ASC", "show": False}, {"field": "Familienname", "sort": "ASC", "show": False},
            {"field": "Vorname", "sort": "ASC", "show": False},
            *({"field": f} for f in ["Familienname", "Vorname", "M-ID", "Geburtsdatum", "Geschlecht", "Status", "Mobiltelefon"])]}}},
    {"title": "Golfclubs sortieren", "covers": [5], "action": {
        "action": "set_table_sort_filter", "table": "Golfclubs", "order_by": "[Golfclubs].[Land], [Golfclubs].[Golfplatz]"}},
]


def test_real_tecomp_exercise_is_completed_and_verified(exercise_04024: Path, tmp_path: Path) -> None:
    text = tecomp.html_to_text(tecomp.task_page("04024_1.accdb", _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    h = build_harness(exercise_04024, [solution(PLAN, task_type="sorting", requirements=REQUIREMENTS)], tmp_path)
    try:
        prepared = h.pipeline.prepare(CapturedTask(text=text, method="tecomp", source=capture.TECOMP_PROCESS))
        # die KI bekam den echten Aufgabentext und die echte Struktur der Übungsdatenbank
        prompt = h.provider.requests[0].messages[0].content
        assert "Spielerstatus" in prompt and "Clubmitglieder, 473 Datensätze" in prompt and "M-ID: AutoWert [PK]" in prompt
        assert prepared.validation.ok and not prepared.solution.uncovered_requirements()
        interaction = AutoInteraction()
        outcome = h.pipeline.execute(prepared, interaction)
        assert outcome.status == "erfolgreich", outcome.error
        assert interaction.confirmations == [] and interaction.conflicts == [], "nach der Freigabe keine weiteren Rückfragen"
        assert all(v.passed for v in outcome.verifications)
        assert sorted(outcome.changes) == sorted([
            "neu: Abfrage „Clubmitglieder sortiert“", "neu: Abfrage „Spielerstatus“",
            "Sortierung von „Golfclubs“: (keine) → [Golfclubs].[Land], [Golfclubs].[Golfplatz]"])
        assert outcome.notes == [], "alle in der Aufgabe genannten Objekte existieren"

        def check(engine: Any) -> None:
            # Ergebnis gegen von Hand geschriebenes SQL auf den echten 473 Datensätzen vergleichen
            columns, rows = engine.queries.run_select("Clubmitglieder sortiert", limit=1000)
            assert columns == ALL_FIELDS and len(rows) == 473
            assert rows == engine.queries.run_select_sql(
                "SELECT * FROM Clubmitglieder ORDER BY Familienname, Vorname", limit=1000)[1]
            columns, rows = engine.queries.run_select("Spielerstatus", limit=1000)
            assert columns[:2] == ["Familienname", "Vorname"] and set(columns) == set(ALL_FIELDS)
            assert rows == engine.queries.run_select_sql(
                "SELECT Familienname, Vorname, [M-ID], Geburtsdatum, Geschlecht, Status, Mobiltelefon "
                "FROM Clubmitglieder ORDER BY Status, Familienname, Vorname", limit=1000)[1]
            golf = engine.schema().table("Golfclubs")
            assert golf.order_by == "[Golfclubs].[Land], [Golfclubs].[Golfplatz]"

        h.access.run(check)
        assert missing_named_objects(text, h.access.schema(refresh=True)) == []
    finally:
        h.close()


# --------------------------------------------------------------------------- laufendes te.comp (nur lesend)
@pytest.mark.live_tecomp
def test_live_tecomp_task_capture_and_attach() -> None:
    """Aufgabenerkennung und Verbinden mit dem von te.comp gestarteten Access – ohne etwas zu verändern."""
    from app.agent.task_reader import TaskReader
    from app.ai.manager import ProviderManager
    from app.core.settings import AppSettings
    from app.security.secrets import MemoryBackend, SecretStore

    reader = TaskReader(AppSettings(), ProviderManager(AppSettings(), SecretStore(MemoryBackend())))
    task = reader.from_window()
    assert task.method == "tecomp" and len(task.text) > 50 and task.db_path.lower().endswith(".accdb")
    service = AccessService()
    try:
        status = service.attach_active()
        assert status.connected and status.mode == "attached"
        assert status.path.lower() == task.db_path.lower()
        schema = service.schema(refresh=True)
        assert schema.attached_to_running_instance and schema.tables
    finally:
        service.shutdown()  # trennt nur – die te.comp-Access-Instanz läuft weiter
    assert tecomp.open_exercise() is not None, "Access läuft nach dem Trennen weiter"


@pytest.mark.live_tecomp
def test_live_tecomp_window_ocr_reads_covered_window() -> None:
    """Das Fensterbild kommt vom te.comp-Fenster selbst, auch wenn andere Fenster davor liegen."""
    if not any(w.process.lower() == capture.TECOMP_PROCESS and w.cls in capture.TECOMP_TASK_CLASSES
               for w in capture.visible_windows()):
        pytest.skip("Kein sichtbares te.comp-Aufgabenfenster (z. B. gesperrter Bildschirm oder Übung nicht geöffnet)")
    windows = capture.find_task_windows()
    assert {w.process.lower() for w in windows} == {capture.TECOMP_PROCESS}
    text = "\n".join(capture.ocr(capture.grab(w)) for w in windows)
    assert len(text) > 100
    assert "chatgpt" not in text.lower() and "claude" not in text.lower(), "kein fremdes Fenster mitfotografiert"

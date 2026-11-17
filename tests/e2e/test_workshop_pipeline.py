"""Ganzer Workshop in einem Lauf – mit echtem Access: ein Plan über alle Teilaufgaben, Abhängigkeiten (Tabelle → Daten
→ Abfrage), Status je Teilaufgabe, Teilfehler, Wiederaufnahme ohne doppelte Daten und nie „abgeschlossen“ ohne
vollständigen Nachweis. Die KI-Antworten sind vorgegeben (ScriptedProvider)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.agent.orchestrator import WORKSHOP_DONE, AutoInteraction
from app.tasks.models import CapturedTask
from tests.helpers import Harness, build_harness

pytestmark = [pytest.mark.access]

TEXT = ("TE.COMP-WORKSHOP „CD-Übung“ – ALLE 3 TEILAUFGABEN:\n1. Tabelle tblCDs anlegen.\n2. Zwei CDs erfassen.\n"
        "3. Abfrage qryJazz: nur Jazz-CDs.")
TABLE = {"title": "Tabelle tblCDs", "covers": [1], "action": {
    "action": "create_table", "name": "tblCDs", "fields": [
        {"name": "CDNr", "type": "AutoWert", "primary_key": True}, {"name": "Titel", "type": "Kurzer Text", "size": 80},
        {"name": "Kategorie", "type": "Kurzer Text", "size": 30}]}}
DATA = {"title": "CDs erfassen", "covers": [2], "action": {
    "action": "insert_records", "table": "tblCDs",
    "records": [{"Titel": "Look to the Rainbow", "Kategorie": "Jazz"}, {"Titel": "Thriller", "Kategorie": "Pop"}]}}
QUERY = {"title": "Abfrage qryJazz", "covers": [3], "action": {
    "action": "create_query", "name": "qryJazz", "sql": "SELECT Titel FROM tblCDs WHERE Kategorie='Jazz'"}}
REQUIREMENTS = ["[1] Tabelle tblCDs mit CDNr, Titel, Kategorie", "[2] Zwei CDs erfasst", "[3] Abfrage qryJazz zeigt nur Jazz"]


def plan(steps: list[dict[str, Any]], requirements: list[str] = REQUIREMENTS) -> dict[str, Any]:
    return {"analysis": {"task_type": "mixed", "title": "CD-Übung", "summary": "Workshop", "requirements": requirements,
                         "confidence": 0.9, "explanation": "Tabelle, Daten, Abfrage."},
            "plan": {"steps": steps, "notes": []}}


@pytest.fixture
def make(sample_db_path: Path, tmp_path: Path) -> Iterator[Any]:
    created: list[Harness] = []

    def factory(responses: list[Any]) -> Harness:
        created.append(build_harness(sample_db_path, responses, tmp_path))
        return created[-1]

    yield factory
    for harness in created:
        harness.close()


def run(h: Harness, total: int = 3, complete: bool = True, text: str = TEXT, images: list[bytes] | None = None) -> Any:
    task = CapturedTask(text=text, method="ocr", source="tecomptraining.exe", workshop_steps=total,
                        workshop_complete=complete, images=images or [])
    interaction = AutoInteraction(interactive=False)  # wie Alt+W: keine Rückfrage möglich
    outcome = h.pipeline.execute(h.pipeline.prepare(task), interaction)
    return outcome, interaction


def count(h: Harness, table: str, where: str | None = None) -> int:
    return int(h.access.run(lambda e: e.tables.count_records(table, where)))


def test_whole_workshop_in_one_run_with_dependencies(make: Any) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # Abbildung der Aufgabe geht mit an die KI
    h = make([plan([TABLE, DATA, QUERY])])  # Tabelle → Daten → Abfrage
    outcome, interaction = run(h, images=[png])
    assert outcome.status == "erfolgreich", outcome.error
    assert outcome.notes[0].startswith(WORKSHOP_DONE) and "3 von 3" in outcome.notes[0]
    assert interaction.requests == [] and interaction.confirmations == []
    rows = h.access.run(lambda e: e.queries.run_select("qryJazz"))[1]
    assert [r[0] for r in rows] == ["Look to the Rainbow"], "Abfrage auf den im selben Lauf angelegten Daten"
    assert h.provider.requests[0].messages[-1].images, "Abbildungen der Aufgabe wurden mitgeschickt"
    assert Path(outcome.backup_path).is_file()


def test_one_unproven_subtask_keeps_the_workshop_partial(make: Any) -> None:
    h = make([plan([TABLE, DATA, QUERY])])
    h.provider.requirement_responses = [{"unmet": {3: "qryJazz zeigt auch Pop"}}] * 4
    outcome, _ = run(h)
    assert outcome.status == "teilweise"
    assert "Workshop teilweise abgeschlossen – 2 von 3 Teilaufgaben nachgewiesen" in outcome.error
    assert count(h, "tblCDs") == 2, "die übrigen Teilaufgaben sind trotzdem erledigt"


def test_a_subtask_missing_from_the_plan_is_never_done(make: Any) -> None:
    h = make([plan([TABLE, DATA], REQUIREMENTS[:2])])
    outcome, _ = run(h)
    assert outcome.status == "teilweise" and "ohne Anforderung im Plan: 3" in outcome.error


def test_blocked_subtask_does_not_stop_the_others(make: Any) -> None:
    clear = {"title": "Alle Bestellungen löschen", "covers": [2], "action": {"action": "delete_records",
                                                                            "table": "Bestellungen"}}
    h = make([plan([TABLE, clear, QUERY], [REQUIREMENTS[0], "[2] Bestellungen leeren", REQUIREMENTS[2]])])
    before = count(h, "Bestellungen")
    outcome, _ = run(h)
    assert outcome.status == "teilweise" and count(h, "Bestellungen") == before, "nicht eindeutig: nur blockiert"
    assert h.access.run(lambda e: e.queries.exists("qryJazz")), "unabhängige Teilaufgabe lief weiter"
    assert "Workshop teilweise abgeschlossen" in outcome.error


def test_incomplete_capture_is_never_reported_as_done(make: Any) -> None:
    h = make([plan([TABLE, DATA, QUERY]), plan([TABLE], REQUIREMENTS[:1])])
    outcome, _ = run(h, complete=False)
    assert outcome.status == "teilweise" and "nicht alle Teilaufgaben erfassbar" in outcome.error
    outcome, _ = run(h, total=0, complete=False, text="1. Tabelle tblCDs anlegen.")  # nur sichtbare Karte erfasst
    assert outcome.status == "teilweise" and "nur die sichtbare Teilaufgabe" in outcome.error


def test_single_subtask_workshop(make: Any) -> None:
    h = make([plan([TABLE], REQUIREMENTS[:1])])
    outcome, _ = run(h, total=1, text="TE.COMP-WORKSHOP – ALLE 1 TEILAUFGABEN:\n1. Tabelle tblCDs anlegen.")
    assert outcome.status == "erfolgreich" and "1 von 1" in outcome.notes[0], outcome.error


def test_rerun_after_interruption_skips_done_parts_and_adds_nothing_twice(make: Any) -> None:
    steps = [TABLE, DATA, QUERY]
    h = make([plan(steps), plan(steps)])
    first, _ = run(h)
    assert first.status == "erfolgreich", first.error
    h.access.run(lambda e: e.queries.delete_query("qryJazz"))  # Teil fehlt wieder (z. B. Abbruch vor dem Speichern)
    second, _ = run(h)
    assert second.status == "erfolgreich", second.error
    assert count(h, "tblCDs") == 2, "keine doppelten CDs"
    assert "entspricht der Vorgabe (unabhängig nachgeprüft)" in second.results[0].message, "nicht neu angelegt"
    assert "Bereits in Aufgabe" in second.results[1].message
    assert h.access.run(lambda e: e.queries.exists("qryJazz")), "nur der fehlende Teil wurde nachgeholt"


def test_empty_plan_for_a_workshop_is_never_answered_or_green(make: Any) -> None:
    """Codex: plant die KI gar nichts, wäre das bisher „beantwortet“ (grün) gewesen – auch bei offenen Teilaufgaben."""
    h = make([plan([], [])])
    outcome, _ = run(h)
    assert outcome.status not in ("erfolgreich", "beantwortet") and "keine Schritte geplant" in outcome.error


def test_existing_but_wrong_object_is_never_counted_as_done(make: Any) -> None:
    """Codex: ein vorhandenes Objekt gilt nur als erledigt, wenn es die unabhängige Prüfung besteht – eine falsche
    vorhandene Abfrage bleibt offen und wird nicht überschrieben (auch wenn die KI-Anforderungsprüfung „erfüllt“ sagt)."""
    h = make([plan([TABLE, DATA, QUERY])])
    h.access.run(lambda e: e.queries.create_query("qryJazz", "SELECT Nachname FROM Kunden"))  # falsch, aber vorhanden
    outcome, _ = run(h)
    assert outcome.status == "teilweise" and "entspricht nicht der Vorgabe" in outcome.error, outcome.error
    assert "Kunden" in h.access.run(lambda e: e.queries.get_sql("qryJazz")), "vorhandene Abfrage nicht überschrieben"


def test_existing_action_query_never_passes_as_the_requested_select(make: Any) -> None:
    """Codex: eine gleichnamige vorhandene Aktualisierungsabfrage ist nie die verlangte Auswahlabfrage (reines SQL)."""
    h = make([plan([TABLE, DATA, QUERY])])
    h.access.run(lambda e: e.queries.create_query("qryJazz", "UPDATE Kunden SET Ort = Ort"))
    outcome, _ = run(h)
    assert outcome.status == "teilweise" and "Abfragetyp" in outcome.error, outcome.error
    assert "UPDATE" in h.access.run(lambda e: e.queries.get_sql("qryJazz")).upper(), "nicht überschrieben"


def test_mass_limit_spares_tables_created_in_this_run_but_protects_existing_data(sample_db_path: Path,
                                                                                tmp_path: Path) -> None:
    """Live-Fund CD-Sammlung: „CD-Nummer in allen 33 frisch importierten Tracks befüllen“ scheiterte an der Grenze von 25
    Datensätzen. Eine Tabelle, die erst dieser Lauf angelegt hat, liegt komplett in der Sicherung davor – vorher
    vorhandene Daten (Kunden) bleiben geschützt."""
    many = {"title": "8 CDs", "covers": [2], "action": {"action": "insert_records", "table": "tblCDs", "records": [
        {"Titel": f"CD {n}", "Kategorie": "Jazz"} for n in range(8)]}}
    fill = {"title": "Kategorie setzen", "covers": [3], "action": {
        "action": "run_action_query", "sql": "UPDATE tblCDs SET Kategorie = 'Pop'"}}
    existing = {"title": "Kunden ändern", "covers": [3], "action": {
        "action": "run_action_query", "sql": "UPDATE Kunden SET Ort = Ort"}}
    h = build_harness(sample_db_path, [plan([TABLE, many, fill, existing])], tmp_path,
                      security__mass_change_threshold=5)
    try:
        outcome, _ = run(h)
        assert outcome.results[2].status == "ok", outcome.results[2].message
        assert count(h, "tblCDs", "Kategorie = 'Pop'") == 8, "frische Tabelle: alle 8 (> Grenze 5) geändert"
        assert outcome.results[3].status != "ok" and "Grenze" in outcome.results[3].message, "Kunden: weiter geschützt"
        assert outcome.status != "erfolgreich"
    finally:
        h.close()


def test_renamed_existing_table_never_counts_as_created_in_this_run(sample_db_path: Path, tmp_path: Path) -> None:
    """Codex: eine umbenannte vorhandene Tabelle trägt einen neuen Namen, enthält aber alte Daten – die Mengengrenze
    bleibt."""
    rename = {"title": "Umbenennen", "covers": [1], "action": {"action": "rename_table", "old_name": "Kunden",
                                                              "new_name": "KundenNeu"}}
    touch = {"title": "Alle ändern", "covers": [2], "action": {"action": "run_action_query",
                                                              "sql": "UPDATE KundenNeu SET Ort = Ort"}}
    h = build_harness(sample_db_path, [plan([rename, touch], ["[1] Umbenennen", "[2] Ändern"])], tmp_path,
                      security__mass_change_threshold=5)
    try:
        outcome, _ = run(h, total=2, text="TE.COMP-WORKSHOP – ALLE 2 TEILAUFGABEN:\n1. Umbenennen.\n2. Ändern.")
        assert outcome.results[1].status != "ok" and "Grenze" in outcome.results[1].message, outcome.results[1].message
        assert outcome.status != "erfolgreich"
    finally:
        h.close()


@pytest.mark.parametrize("fill", [
    {"action": "run_action_query", "sql": "UPDATE tblCDs SET Kategorie = DLookUp('Ort', 'Kunden', 'False')"},
    {"action": "update_records", "table": "tblCDs", "values": {"Kategorie": "=DLookUp('Ort', 'Kunden', 'False')"},
     "where": "CDNr > 0"},
])
def test_fill_that_leaves_the_field_empty_is_never_proven(make: Any, fill: dict[str, Any]) -> None:
    """Live-Fund CD-Sammlung: „33 Datensätze geändert“, aber DLookUp fand nichts – das Feld blieb überall leer, die
    Teilaufgabe galt trotzdem als nachgewiesen. Jetzt fällt die Schrittprüfung durch, die Teilaufgabe bleibt offen."""
    clear = {"title": "Kategorie leeren", "covers": [2], "action": {
        "action": "run_action_query", "sql": "UPDATE tblCDs SET Kategorie = Null"}}
    h = make([plan([TABLE, DATA, clear, {"title": "Kategorie befüllen", "covers": [3], "action": fill}],
                   [REQUIREMENTS[0], "[2] Kategorie leeren", "[3] Kategorie aus Kunden befüllen"])])
    outcome, _ = run(h)
    checks = [c for v in outcome.verifications for c in v.checks if c.name == "Kategorie enthält danach Werte"]
    assert checks and not checks[-1].passed, checks
    assert outcome.status != "erfolgreich" and outcome.requirements is not None, "übrige Teilaufgaben wurden geprüft"
    assert "2 von 3 Teilaufgaben nachgewiesen; nicht nachgewiesen: 3" in outcome.notes[0], outcome.notes


def test_reused_name_of_a_fresh_table_does_not_inherit_its_origin(sample_db_path: Path, tmp_path: Path) -> None:
    """Codex: tblCDs anlegen → umbenennen → alte Tabelle Kunden auf „tblCDs“ umbenennen. Der Name gehört jetzt alten
    Daten – die Mengengrenze gilt wieder."""
    moves = [{"title": "weg", "covers": [2], "action": {"action": "rename_table", "old_name": "tblCDs",
                                                       "new_name": "tblTemp"}},
             {"title": "alt her", "covers": [2], "action": {"action": "rename_table", "old_name": "Kunden",
                                                           "new_name": "tblCDs"}},
             {"title": "alle ändern", "covers": [3], "action": {"action": "run_action_query",
                                                               "sql": "UPDATE tblCDs SET Ort = Ort"}}]
    h = build_harness(sample_db_path, [plan([TABLE, *moves], [REQUIREMENTS[0], "[2] Umbenennen", "[3] Ändern"])],
                      tmp_path, security__mass_change_threshold=5)
    try:
        outcome, _ = run(h)
        assert outcome.results[3].status != "ok" and "Grenze" in outcome.results[3].message, outcome.results[3].message
    finally:
        h.close()


def test_workshop_checks_all_subtasks_even_when_one_step_failed(make: Any) -> None:
    """Live-Fund: ein gescheiterter Schritt verhinderte jede Anforderungsprüfung – „0 von 12 nachgewiesen“, obwohl 10
    Schritte gelungen waren. Jetzt werden die übrigen Teilaufgaben geprüft; erfolgreich wird der Workshop trotzdem nicht."""
    broken = {"title": "Kategorie aus falschem Feld", "covers": [2], "action": {
        "action": "run_action_query", "sql": "UPDATE tblCDs SET Kategorie = [Gibtsnicht]"}}
    h = make([plan([TABLE, broken, QUERY], [REQUIREMENTS[0], "[2] Kategorie gesetzt", REQUIREMENTS[2]]),
              {"steps": [], "notes": ["kein anderer Weg"]}])  # KI-Reparatur des gescheiterten Schritts: keine
    h.provider.requirement_responses = [{"unmet": {2: "Kategorie nicht gesetzt"}}] * 4
    outcome, _ = run(h)
    assert outcome.requirements is not None, "die Anforderungsprüfung lief trotz des Fehlschlags"
    assert outcome.status == "teilweise" and "2 von 3 Teilaufgaben nachgewiesen" in outcome.error, outcome.error

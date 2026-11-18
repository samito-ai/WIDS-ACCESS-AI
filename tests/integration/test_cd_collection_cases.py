"""Die drei offenen Teilaufgaben der CD-Sammlung – nachgestellt mit EIGENER Testdatenbank (keine te.comp-Datei):
Liedtext aus einer Textdatei mit abweichendem Namen, CD-Nummer über „Interpret - Titel“ (andere Schreibweise,
Tippfehler, Interpret mit Zusatz) und Spieldauer „00:05:29“ als Zeit ohne Stunden. Echte Access-Pipeline, KI-Antworten
vorgegeben."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.access.constants import AccessType
from app.access.engine import AccessEngine
from app.access.specs import FieldSpec
from app.agent.orchestrator import AutoInteraction
from app.tasks.models import CapturedTask
from tests.helpers import Harness, build_harness

pytestmark = [pytest.mark.access]

LYRICS = "Letter perfect stars of\r\ngold in school – Größe zählt\r\nYou got a right to shout\r\n"
TRACKS = [
    {"CD": "Al Jarreau - Look to the rainbow", "Titelnummer": 1, "Titel": "Letter Perfect", "Spieldauer": "00:05:29"},
    {"CD": "Al Jarreau - Look to the rainbow", "Titelnummer": 2, "Titel": "Rainbow in your eyes", "Spieldauer": "00:06:29"},
    {"CD": "George Benson - White Rabatt", "Titelnummer": 1, "Titel": "White Rabbit", "Spieldauer": "00:04:10"},
    {"CD": "Archie Shepp - Mama Rose", "Titelnummer": 1, "Titel": "Mama Rose", "Spieldauer": "00:12:01"},
    {"CD": "Queen - Made in Heaven", "Titelnummer": 3, "Titel": "Let me live", "Spieldauer": "00:04:45"},
]
EXPECTED_KEYS = [1, 1, 2, 3, 4]


@pytest.fixture
def cd_db(tmp_path: Path) -> Path:
    path = tmp_path / "cdtest.accdb"
    engine = AccessEngine.create(path)
    try:
        engine.tables.create_table("CDs", [
            FieldSpec(name="CD-Nummer", type=AccessType.LONG, primary_key=True),
            FieldSpec(name="Interpret", type=AccessType.SHORT_TEXT, size=100),
            FieldSpec(name="Titel der CD", type=AccessType.SHORT_TEXT, size=100)])
        engine.tables.insert_records("CDs", [
            {"CD-Nummer": 1, "Interpret": "Al Jarreau", "Titel der CD": "Look to the Rainbow"},
            {"CD-Nummer": 2, "Interpret": "George Benson", "Titel der CD": "White Rabbit"},
            {"CD-Nummer": 3, "Interpret": "Archie Shepp / Jasper van't Hof", "Titel der CD": "Mama Rose"},
            {"CD-Nummer": 4, "Interpret": "Queen", "Titel der CD": "Made in Heaven"},
            {"CD-Nummer": 5, "Interpret": "Sting", "Titel der CD": "Soul Cages"}])
    finally:
        engine.close()
    (tmp_path / "LetterPerfekt.txt").write_bytes(LYRICS.encode("cp1252"))  # Name mit „k“, Aufgabe sagt „c“
    return path


@pytest.fixture
def harness(cd_db: Path, tmp_path: Path) -> Iterator[Any]:
    created: list[Harness] = []

    def factory(responses: list[Any]) -> Harness:
        created.append(build_harness(cd_db, responses, tmp_path / "wids"))
        return created[-1]

    yield factory
    for h in created:
        h.close()


def plan(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"analysis": {"task_type": "mixed", "title": "CD", "summary": "s", "confidence": 0.9, "explanation": "e",
                         "requirements": [f"Anforderung {i + 1}" for i in range(len(steps))]},
            "plan": {"steps": [{**s, "covers": [i + 1]} for i, s in enumerate(steps)], "notes": []}}


TRACKS_TABLE = {"title": "Tracks", "action": {"action": "create_table", "name": "Tracks", "fields": [
    {"name": "CD", "type": "Kurzer Text", "size": 100}, {"name": "Titelnummer", "type": "Zahl"},
    {"name": "Titel", "type": "Kurzer Text", "size": 100}, {"name": "Spieldauer", "type": "Kurzer Text", "size": 20},
    {"name": "Liedtext", "type": "Langer Text"}, {"name": "CD-Nummer", "type": "Zahl"}]}}
TRACKS_DATA = {"title": "Tracks befüllen", "action": {"action": "insert_records", "table": "Tracks", "records": TRACKS}}
LYRICS_STEP = {"title": "Liedtext", "action": {"action": "set_field_from_file", "table": "Tracks", "field": "Liedtext",
                                              "file": "LetterPerfect.txt", "where": "[Titel]='Letter Perfect'"}}
MAP_STEP = {"title": "CD-Nummer", "action": {"action": "map_keys", "table": "Tracks", "target_field": "CD-Nummer",
                                            "source_fields": ["CD"], "lookup_table": "CDs", "lookup_key": "CD-Nummer",
                                            "lookup_fields": ["Interpret", "Titel der CD"]}}
TIME_STEPS = [
    {"title": "Spieldauer als Zeit", "action": {"action": "change_field_type", "table": "Tracks", "field": "Spieldauer",
                                                "new_type": "Datum/Uhrzeit"}},
    {"title": "ohne Stunden", "action": {"action": "set_field_properties", "table": "Tracks", "field": "Spieldauer",
                                         "properties": {"format": "nn:ss"}}},
]


def run(h: Harness) -> Any:
    task = CapturedTask(text="CD-Sammlung: Liedtext, CD-Nummer, Spieldauer ohne Stunden.", method="manual")
    return h.pipeline.execute(h.pipeline.prepare(task), AutoInteraction(interactive=False))


def test_cd_collection_open_subtasks_are_solved_and_verified(harness: Any) -> None:
    h = harness([plan([TRACKS_TABLE, TRACKS_DATA, LYRICS_STEP, MAP_STEP, *TIME_STEPS])])
    outcome = run(h)
    assert outcome.status == "erfolgreich", (outcome.error, [(r.step_id, r.status, r.message) for r in outcome.results])
    assert all(v.passed for v in outcome.verifications), [c.name for v in outcome.verifications for c in v.failed_checks]
    _, rows = h.access.run(lambda e: e.tables.read_sql(
        "SELECT [CD-Nummer], Liedtext, Spieldauer, Titel FROM Tracks ORDER BY CD, Titelnummer", limit=50))
    by_title = {r[3]: r for r in rows}
    assert by_title["Letter Perfect"][1].replace("\r\n", "\n").strip() == LYRICS.replace("\r\n", "\n").strip()
    assert by_title["Rainbow in your eyes"][1] is None, "nur der verlangte Datensatz bekam den Text"
    order = [r["Titel"] for r in TRACKS]
    assert [by_title[t][0] for t in order] == EXPECTED_KEYS, "jede CD-Nummer richtig – auch Tippfehler und Zusatz"
    field = h.access.schema(refresh=True).table("Tracks").field("Spieldauer")
    assert field.type == AccessType.DATETIME and field.format == "nn:ss", (field.type, field.format)
    assert "00:05:29" in str(by_title["Letter Perfect"][2]), ("Werte bei der Typänderung erhalten", by_title["Letter Perfect"][2])
    similar = next(r for r in outcome.results if r.step_id == 4).details["similar"]
    assert [s[1] for s in similar] == [2], "nur „White Rabatt“ über Ähnlichkeit – eindeutig und ausgewiesen"


def test_ambiguous_mapping_writes_nothing_for_that_value_and_is_not_proven(harness: Any) -> None:
    h = harness([plan([TRACKS_TABLE, {"title": "zweite gleiche CD", "action": {
        "action": "insert_records", "table": "CDs", "records": [
            {"CD-Nummer": 6, "Interpret": "Queen", "Titel der CD": "Made in Heaven"}]}}, TRACKS_DATA, MAP_STEP])])
    outcome = run(h)
    queen = h.access.run(lambda e: e.tables.count_records("Tracks", "CD = 'Queen - Made in Heaven' AND [CD-Nummer] Is Null"))
    assert queen == 1, "mehrdeutig: keine zufällige Zuordnung"
    checks = {c.name: c for v in outcome.verifications for c in v.checks}
    assert not checks["Alle Werte eindeutig zugeordnet"].passed and "mehrdeutig" in checks["Alle Werte eindeutig zugeordnet"].detail
    assert outcome.status != "erfolgreich"


def test_existing_foreign_keys_are_never_overwritten(harness: Any) -> None:
    preset = {"title": "Tracks mit falscher Nummer", "action": {"action": "insert_records", "table": "Tracks", "records": [
        {**TRACKS[0], "CD-Nummer": 5}]}}
    h = harness([plan([TRACKS_TABLE, preset, MAP_STEP])])
    outcome = run(h)
    assert h.access.run(lambda e: e.tables.count_records("Tracks", "[CD-Nummer] = 5")) == 1, "nichts überschrieben"
    checks = {c.name: c for v in outcome.verifications for c in v.checks}
    assert not checks["Zugeordnete Schlüssel gespeichert"].passed, "falscher vorhandener Wert fällt in der Prüfung auf"


def test_lossy_type_change_on_a_fresh_table_stays_blocked(harness: Any) -> None:
    """Codex: auch bei einer in diesem Lauf angelegten Tabelle keine Typänderung, die Werte verliert (1,5 → Zahl)."""
    table = {"title": "T", "action": {"action": "create_table", "name": "Messung", "fields": [
        {"name": "Nr", "type": "Zahl"}, {"name": "Wert", "type": "double"}]}}
    data = {"title": "Daten", "action": {"action": "insert_records", "table": "Messung", "records": [{"Nr": 1, "Wert": 1.5}]}}
    change = {"title": "Typ", "action": {"action": "change_field_type", "table": "Messung", "field": "Wert", "new_type": "Zahl"}}
    h = harness([plan([table, data, change])])
    outcome = run(h)
    assert outcome.results[2].status == "skipped" and outcome.status != "erfolgreich"
    assert h.access.run(lambda e: e.tables.count_records("Messung", "Wert = 1.5")) == 1, "Wert unverändert"


def test_file_content_starting_with_equals_is_stored_literally(harness: Any, tmp_path: Path) -> None:
    """Codex: ein Dateiinhalt wie „=Null“ darf nie als Ausdruck ausgeführt werden."""
    (tmp_path / "Formel.txt").write_text("=Null", encoding="utf-8")
    step = {"title": "Text", "action": {"action": "set_field_from_file", "table": "Tracks", "field": "Liedtext",
                                       "file": "Formel.txt", "where": "[Titel]='Letter Perfect'"}}
    h = harness([plan([TRACKS_TABLE, TRACKS_DATA, step])])
    run(h)
    _, rows = h.access.run(lambda e: e.tables.read_sql("SELECT Liedtext FROM Tracks WHERE Titel='Letter Perfect'"))
    assert rows[0][0] == "=Null"


@pytest.mark.parametrize(("source", "value", "new_type"), [
    ("double", 1.23456, "Währung"),        # Währung hat nur 4 Nachkommastellen
    ("double", 16777217, "single"),        # Single verliert ab 2^24 ganze Zahlen
    ("Kurzer Text", "1.000", "byte"),      # „1.000“ = 1000 – passt nicht in Byte
    ("Langer Text", "A" * 300, "Kurzer Text"),  # DISTINCT würde auf 255 kürzen und die Länge verstecken
])
def test_narrowing_type_change_is_blocked_by_the_lossless_check(harness: Any, source: str, value: Any,
                                                                new_type: str) -> None:
    """Codex (2. Runde): auch Genauigkeit, Wertebereich und volle Textlänge zählen."""
    from app.agent.orchestrator import _converts_without_loss
    from app.tasks.models import ChangeFieldType

    table = {"title": "T", "action": {"action": "create_table", "name": "Messung", "fields": [
        {"name": "Nr", "type": "Zahl"}, {"name": "Wert", "type": source}]}}
    data = {"title": "Daten", "action": {"action": "insert_records", "table": "Messung", "records": [{"Nr": 1, "Wert": value}]}}
    h = harness([plan([table, data])])
    run(h)
    action = ChangeFieldType.model_validate({"action": "change_field_type", "table": "Messung", "field": "Wert",
                                             "new_type": new_type})
    assert h.access.run(lambda e: _converts_without_loss(e, action)) is False


def _existing_tracks(db: Path, rows: int = 33) -> None:
    """Wie live: „Tracks“ gibt es schon VOR dem Lauf (frühere Teilaufgabe), mehr Datensätze als die Mengengrenze (25),
    Spieldauer als Text „05:29“."""
    engine = AccessEngine.open(db, prefer_running=False)
    try:
        engine.tables.create_table("Tracks", [
            FieldSpec(name="CD", type=AccessType.SHORT_TEXT, size=255),
            FieldSpec(name="Titelnummer", type=AccessType.LONG),
            FieldSpec(name="Spieldauer", type=AccessType.SHORT_TEXT, size=255),
            FieldSpec(name="CD-Nummer", type=AccessType.LONG)])
        cds = [t["CD"] for t in TRACKS]
        engine.tables.insert_records("Tracks", [
            {"CD": cds[i % len(cds)], "Titelnummer": i + 1, "Spieldauer": f"0{i % 9}:{10 + i:02d}"} for i in range(rows)])
    finally:
        engine.close()


def test_live_replay_existing_tracks_are_mapped_and_converted_without_loss(harness: Any, cd_db: Path) -> None:
    """Live 18.11.: Zuordnung an der Grenze 25 gescheitert, Typänderung als „löschend“ blockiert."""
    _existing_tracks(cd_db)
    h = harness([plan([MAP_STEP, *TIME_STEPS])])
    outcome = run(h)
    assert outcome.status == "erfolgreich", [(r.step_id, r.status, r.message) for r in outcome.results]
    assert h.access.run(lambda e: e.tables.count_records("Tracks", "[CD-Nummer] Is Null")) == 0
    field = h.access.schema(refresh=True).table("Tracks").field("Spieldauer")
    assert field.type == AccessType.DATETIME and field.format == "nn:ss"
    assert h.access.run(lambda e: e.tables.count_records("Tracks", "Spieldauer Is Null")) == 0, "kein Wert verloren"


def test_correction_never_works_around_a_blocked_step(harness: Any, cd_db: Path) -> None:
    """Live 18.11.: Typänderung blockiert → Korrektur benannte das Feld um und legte ein leeres neues an."""
    _existing_tracks(cd_db)
    lossy = {"title": "CD als Zahl", "action": {"action": "change_field_type", "table": "Tracks", "field": "CD",
                                               "new_type": "Zahl"}}
    h = harness([plan([lossy])])
    h.provider.requirement_responses.append({"unmet": {1: "CD ist noch Text"}})
    h.provider.fix_responses.append({"steps": [
        {"title": "umbenennen", "rationale": "r", "covers": [1],
         "action": {"action": "rename_field", "table": "Tracks", "old_name": "CD", "new_name": "CD_Text"}},
        {"title": "neu", "rationale": "r", "covers": [1],
         "action": {"action": "add_field", "table": "Tracks", "field": {"name": "CD", "type": "Zahl"}}}], "notes": []})
    outcome = run(h)
    fields = [f.name for f in h.access.schema(refresh=True).table("Tracks").fields]
    assert "CD_Text" not in fields and outcome.status != "erfolgreich", (fields, outcome.status)
    assert h.access.run(lambda e: e.tables.count_records("Tracks", "CD Is Not Null")) == 33


def test_correction_may_change_a_type_when_no_value_is_lost(harness: Any, cd_db: Path) -> None:
    """Live 18.11. (2. Lauf): nur das Format geplant, die Korrektur-KI durfte den Typ nicht ändern – Teilaufgabe 9 offen."""
    _existing_tracks(cd_db)
    h = harness([plan([TIME_STEPS[1]])])
    h.provider.requirement_responses.append({"unmet": {1: "Spieldauer ist Text"}})
    h.provider.fix_responses.append({"steps": [{"title": "Typ", "rationale": "r", "covers": [1],
                                                "action": TIME_STEPS[0]["action"]}], "notes": []})
    run(h)
    field = h.access.schema(refresh=True).table("Tracks").field("Spieldauer")
    assert field.type == AccessType.DATETIME and field.format == "nn:ss", (field.type, field.format)
    assert h.access.run(lambda e: e.tables.count_records("Tracks", "Spieldauer Is Null")) == 0

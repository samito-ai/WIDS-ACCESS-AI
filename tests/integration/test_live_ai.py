"""Echte KI-Tests (kostenpflichtig): nur mit ``WIDS_LIVE_AI=1`` und ANTHROPIC_API_KEY in der Umgebung.

Hier plant das echte Modell; ausgeführt wird in echtem Access auf Kopien. Geprüft wird das Ergebnis in der
Datenbank – nicht die Formulierung der KI. Die Tests sind tolerant gegenüber gleichwertigen Lösungswegen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.access.service import AccessService
from app.agent.analyzer import TaskAnalyzer
from app.agent.orchestrator import AutoInteraction, TaskPipeline
from app.ai import prompts
from app.ai.base import ChatMessage
from app.ai.manager import ProviderManager
from app.core.settings import AppSettings
from app.security.backup import BackupManager
from app.security.policy import SafetyPolicy
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.tasks.models import CapturedTask
from app.vision import tecomp
from tests.integration.test_tecomp import _base, exercise_04024  # noqa: F401 - Fixture

pytestmark = [pytest.mark.live_ai, pytest.mark.access]

#: harte Kostenbremse je Test (WIDS-eigenes Kostenlimit): danach keine weitere KI-Anfrage
LIVE_TEST_LIMIT_USD = 0.60


@pytest.fixture(autouse=True)
def _tmp_is_tecomp_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Tests kopieren te.comp-Vorlagen unter Übungsnamen in einen Testordner: für Quelldateien und Workshop-Kontext
    gilt dieser wie der te.comp-Arbeitsordner (WIDS nutzt te.comp-Quellen sonst nur dort)."""
    original = tecomp.in_tecomp_workdir
    monkeypatch.setattr(tecomp, "in_tecomp_workdir", lambda path: Path(path).parent == tmp_path or original(path))


@pytest.fixture(autouse=True)
def _usage_log(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """Tokens und Kosten je Test aus den Test-Datenbanken lesen, ausgeben und (WIDS_LIVE_USAGE_LOG) protokollieren."""
    import json
    import os
    import sqlite3

    yield
    calls, tokens_in, tokens_out, cost = 0, 0, 0, 0.0
    for db in tmp_path.rglob("*.sqlite3"):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT count(*), sum(input_tokens), sum(output_tokens), sum(cost_usd) FROM usage").fetchone()
        except sqlite3.Error:
            continue
        finally:
            con.close()
        calls, tokens_in, tokens_out = calls + row[0], tokens_in + (row[1] or 0), tokens_out + (row[2] or 0)
        cost += row[3] or 0.0
    entry = {"test": request.node.name, "calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out,
             "cost_usd": round(cost, 4)}
    print(f"\nKI-Verbrauch {entry}")
    if path := os.environ.get("WIDS_LIVE_USAGE_LOG"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _secrets() -> SecretStore:
    """Schlüssel aus ANTHROPIC_API_KEY, sonst der in der Anmeldeinformationsverwaltung gespeicherte (nie ausgegeben)."""
    import os

    from app.security.secrets import CredentialManagerBackend

    return SecretStore(MemoryBackend() if os.environ.get("ANTHROPIC_API_KEY") else CredentialManagerBackend())


def _pipeline(db: Path, tmp_path: Path) -> tuple[TaskPipeline, AccessService, Storage]:
    settings = AppSettings()
    settings.ai.cost_limit_usd, settings.ai.cost_limit_period = LIVE_TEST_LIMIT_USD, "total"
    storage = Storage(tmp_path / "live.sqlite3")
    ai = ProviderManager(settings, _secrets(), storage)
    access = AccessService(operation_timeout_s=180)
    access.connect(str(db), prefer_running=False)
    pipeline = TaskPipeline(settings=settings, storage=storage, access=access, analyzer=TaskAnalyzer(ai),
                            backups=BackupManager(storage, tmp_path / "b", tmp_path / "w"), policy=SafetyPolicy(settings.security),
                            environment_probe=lambda: ([], []))
    return pipeline, access, storage


def _run(pipeline: TaskPipeline, text: str, conflict: str = "skip") -> Any:
    prepared = pipeline.prepare(CapturedTask(text=text, method="tecomp"))
    outcome = pipeline.execute(prepared, AutoInteraction(conflict=conflict))  # type: ignore[arg-type]
    print(outcome.report_md)  # mit -s sichtbar: Plan, Prüfungen, Änderungen, Anforderungen
    return prepared, outcome


def _check_04024(engine: Any) -> None:
    """Unabhängige Prüfung der drei Teilaufgaben von 0402/U4 direkt in der Datenbank (eigenes SQL)."""
    columns, rows = engine.queries.run_select("Clubmitglieder sortiert", limit=1000)
    assert set(columns) >= {"M-ID", "Familienname", "Vorname", "Geburtsdatum", "Geschlecht", "Status", "Mobiltelefon"}
    assert len(rows) == 473
    names = [r[columns.index("Familienname")] for r in rows]
    assert names == [r[0] for r in engine.queries.run_select_sql(
        "SELECT Familienname FROM Clubmitglieder ORDER BY Familienname", limit=1000)[1]]
    columns, rows = engine.queries.run_select("Spielerstatus", limit=1000)
    assert columns[:2] == ["Familienname", "Vorname"] and len(rows) == 473
    # zuerst Status, dann Name – gegen eigenes SQL, egal ob Status angezeigt oder nur Sortierspalte ist
    assert [r[0] for r in rows] == [r[0] for r in engine.queries.run_select_sql(
        "SELECT Familienname FROM Clubmitglieder ORDER BY Status, Familienname", limit=1000)[1]], "Status, dann Name"
    order = engine.schema().table("Golfclubs").order_by
    assert "Land" in order and "Golfplatz" in order and order.index("Land") < order.index("Golfplatz")
    assert "desc" not in order.lower(), f"aufsteigend sortiert: {order}"


@pytest.mark.tecomp
def test_live_real_tecomp_exercise(exercise_04024: Path, tmp_path: Path) -> None:  # noqa: F811
    text = tecomp.html_to_text(tecomp.task_page("04024_1.accdb", _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(exercise_04024, tmp_path)
    try:
        prepared, outcome = _run(pipeline, text)
        assert len(prepared.solution.plan.steps) >= 3, "alle drei Teilaufgaben geplant"
        assert outcome.status == "erfolgreich", outcome.error
        access.run(_check_04024)
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_release_e2e_0402_u4(exercise_04024: Path, tmp_path: Path,  # noqa: F811
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Live-Release-Ablauf an einer Übungskopie: Erkennung wie Alt+W → KI-Plan → Sicherung → Ausführung →
    unabhängige Prüfung → Diagnosebericht. Danach eigene Prüfung aller Teilanforderungen direkt in Access."""
    import shutil

    import psutil

    from app.access.engine import AccessEngine

    if any((p.info["name"] or "").lower() == "tecomptraining.exe" for p in psutil.process_iter(["name"])):
        pytest.skip("te.comp läuft – es richtet Dateien mit Übungsnamen selbst ein und würde den Test verfälschen")
    exercise = exercise_04024.rename(exercise_04024.with_name("04024_1.accdb"))  # Dateiname wie in te.comp
    task = _alt_w_capture(exercise, monkeypatch, tmp_path)
    assert task.method == "tecomp" and "Clubmitglieder sortiert" in task.text and "Spielerstatus" in task.text
    pipeline, access, storage = _pipeline(exercise, tmp_path)
    try:
        prepared, outcome = _run(pipeline, task.text)
        assert len(prepared.solution.plan.steps) >= 3, "alle drei Teilaufgaben geplant"
        assert outcome.status == "erfolgreich", outcome.error
        # Sicherung = Zustand vor der Ausführung – inhaltlich: Access schreibt schon beim Öffnen in den Dateikopf
        backup = Path(outcome.backup_path)
        assert backup.is_file()
        probe = Path(shutil.copy(backup, tmp_path / "sicherung_probe.accdb"))  # Sicherung selbst bleibt unberührt
        engine = AccessEngine.open(probe, prefer_running=False)
        try:
            assert engine.queries.list_queries() == [] and not engine.schema().table("Golfclubs").order_by
        finally:
            engine.close()
        assert "## Diagnose (Anforderung → Schritte → Prüfung)" in outcome.report_md
        diagnosis = outcome.report_md.split("## Diagnose", 1)[1]
        rows = [line.split(" | ") for line in diagnosis.splitlines() if line[:3].strip("| ").isdigit()]
        assert rows and all(r[-1].startswith("erfüllt") for r in rows), diagnosis  # letzte Spalte je Anforderung
        assert storage.get_task(prepared.task_id).status == "erfolgreich"
        access.run(_check_04024)
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_tecomp_import_exercise_1002(tmp_path: Path) -> None:
    """te.comp-Übung 1002/U1: Tab-getrennte Datei in neue Tabelle (vorhandenes Feld als Schlüssel), Datei mit fester
    Breite in eine vorhandene Tabelle – Geschlecht und Ja/Nein stehen dort ohne Trennzeichen nebeneinander („w1“)."""
    from app.access.constants import AccessType
    from app.access.specs import FieldSpec

    db = tmp_path / "10021_1.accdb"  # Dateiname wie in te.comp → Quelldateien aus access\cbt\1002\files
    files = _base() / "access" / "cbt" / "1002" / "files"
    crew1 = len(files.joinpath("Filmcrew1.txt").read_text(encoding="cp1252").splitlines()) - 1
    crew2 = len([line for line in files.joinpath("Filmcrew2.txt").read_text(encoding="cp1252").splitlines() if line.strip()])

    def structure(engine: Any) -> None:
        engine.tables.create_table("Filmcrew2", [
            FieldSpec(name="FilmSetID", type=AccessType.LONG), FieldSpec(name="Vorname", size=50),
            FieldSpec(name="Nachname", size=50), FieldSpec(name="Geschlecht", size=1),
            FieldSpec(name="WalkOfFame", type=AccessType.YES_NO)], ["FilmSetID"])

    setup = AccessService(operation_timeout_s=180)
    setup.create_database(str(db), structure)
    setup.shutdown()
    text = tecomp.html_to_text(tecomp.task_page(db.name, _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        schema = access.schema(refresh=True)
        new = next(t for t in schema.tables if t.name != "Filmcrew2")
        assert new.record_count == crew1 and new.primary_key, "Filmcrew1 vollständig, mit vorhandenem Feld als Schlüssel"
        assert schema.table("Filmcrew2").record_count == crew2
        _, rows = access.run(lambda e: e.tables.read_sql("SELECT Geschlecht, WalkOfFame FROM Filmcrew2 WHERE FilmSetID = 11"))
        assert rows == [["w", True]], "„w1“ = weiblich + Walk of Fame"
        _, rows = access.run(lambda e: e.tables.read_sql("SELECT DISTINCT Geschlecht FROM Filmcrew2"))
        assert sorted(r[0] for r in rows) == ["m", "w"]
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_tecomp_lookup_exercise_0905(tmp_path: Path) -> None:
    """te.comp-Übung 0905/U4: neues Nachschlagefeld mit der Liste aus Filmstudios.txt, danach ein Datensatz damit."""
    import shutil

    db = tmp_path / "09054_1.accdb"  # Kopie der te.comp-Filmvorlage unter dem Dateinamen der Übung
    shutil.copy(_base() / "access" / "cbt" / "database" / "filme-dm.accdb", db)
    text = tecomp.html_to_text(tecomp.task_page(db.name, _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        field = access.schema(refresh=True).table("Filme").field("Filmstudio")
        assert field is not None and field.lookup_source, "Nachschlagefeld angelegt"
        for studio in ("Warner Home", "Paramount", "Concorde", "VCL"):
            assert studio in field.lookup_source, "Liste vollständig aus der Textdatei"
        _, rows = access.run(lambda e: e.tables.read_sql("SELECT Filmtitel, Filmstudio FROM Filme WHERE Filmtitel LIKE 'Ben Hur*'"))
        assert rows and rows[0][1] == "Paramount"
    finally:
        access.shutdown()
        storage.close()


def _alt_w_capture(db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Genau die Erfassung von Alt+W (TaskReader.from_window) – nur das Access-Fenster wird vorgegeben."""
    from app.agent.task_reader import TaskReader

    monkeypatch.setattr(tecomp, "open_exercise", lambda: (str(db), f"Access - {db.stem} : Datenbank- {db}"))
    settings = AppSettings()
    reader = TaskReader(settings, ProviderManager(settings, _secrets(), Storage(tmp_path / "reader.sqlite3")))
    return reader.from_window()


@pytest.mark.tecomp
@pytest.mark.access_app
def test_live_alt_w_form_task_with_picture_0301(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Früher geplant: nichts („kein Formularname“, Details nur in der Abbildung). Jetzt: Formular wird erstellt."""
    import shutil

    original = (Path.home() / "te.comp" / "te.comp-training" / "03011_12345678.accdb")
    backups = sorted((Path.home() / "AppData/Local/WIDS-Access-AI/backups/03011_12345678").glob("*.accdb"))
    source = backups[0] if backups else original  # Ausgangszustand vor einer früheren Bearbeitung
    if not source.exists():
        pytest.skip("te.comp-Übungsdatei 03011 nicht vorhanden")
    db = tmp_path / original.name
    shutil.copy(source, db)  # nur eine Kopie – die Übungsdatei bleibt unverändert
    task = _alt_w_capture(db, monkeypatch, tmp_path)
    assert task.method == "tecomp" and len(task.images) == 1, "Text UND Abbildung der Aufgabenseite"
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        prepared = pipeline.prepare(task)
        outcome = pipeline.execute(prepared, AutoInteraction(conflict="rename"))
        print(outcome.report_md)
        assert prepared.solution.plan.steps, "es wird geplant statt nachzufragen"
        assert outcome.status in ("erfolgreich", "unklar", "ausgeführt", "teilweise"), outcome.error
        forms = access.schema(refresh=True, detailed_objects=True).forms
        assert forms, "Formular erstellt"
        assert any(c.layout == 2 for f in forms for c in f.controls), "echtes gestapeltes Steuerelementlayout"
        formatting = [c.formatting for f in forms for c in f.controls]
        assert any("fett" in x for x in formatting) and any("Hintergrund" in x for x in formatting), \
            "Fettschrift und farbige Hinterlegung aus der Abbildung umgesetzt"
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_alt_w_blue_query_names_0403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Früher: „Namen in blauer Schrift fehlen“ → nichts geplant bzw. nicht bestätigt. Jetzt: exakt diese Namen."""
    import shutil

    db = tmp_path / "04034_12345678.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / "filme-dm.accdb", db)
    task = _alt_w_capture(db, monkeypatch, tmp_path)
    assert "Objektnamen in blauer Schrift" in task.text
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, task.text)
        assert outcome.status == "erfolgreich", outcome.error
        queries = {q.name for q in access.schema(refresh=True).queries}
        assert {"Leonardo DiCaprio", "Robert Redford", "Robert De Niro"} <= queries
    finally:
        access.shutdown()
        storage.close()


#: Eigener Testtext im Stil eines OCR-Ergebnisses (mit typischen OCR-Fehlern wie „W•.l“), Inhalt sinngemäß wie ein
#: Workshop-Schritt – kein Originaltext aus te.comp
CD_WORKSHOP_OCR = """Workshop CD-Sammlung
Schritt 1 – Tabelle für die CDs
Die CD-Liste liegt bisher als Excel-Datei vor und soll nach Access.
Lege die Tabelle "CDs" an, indem du die Excel-Datei CDs.xls aus
dem Arbeitslaufwerk W•.l importierst. Die Spaltenüberschriften
werden als Feldnamen übernommen.
Weiter"""


@pytest.mark.tecomp
def test_live_workshop_import_old_excel_from_workdir(tmp_path: Path) -> None:
    """Früher: „CDs.xls nicht gefunden“ (Arbeitslaufwerk W:, altes .xls) → nichts geplant. Jetzt: Tabelle CDs importiert."""
    from app.access import importer

    source = next((p for d in tecomp.workdirs() for p in [d / "CDs.xls"] if p.exists()), None)
    if source is None:
        pytest.skip("CDs.xls liegt in keinem te.comp-Arbeitsordner (Workshop nicht gestartet)")
    db = tmp_path / "CD-Sammlung.accdb"
    setup = AccessService(operation_timeout_s=120)
    setup.create_database(str(db), None)
    setup.shutdown()
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        prepared = pipeline.prepare(CapturedTask(text=CD_WORKSHOP_OCR, method="ocr", source="tecomptraining.exe"))
        outcome = pipeline.execute(prepared, AutoInteraction(conflict="skip"))
        print(outcome.report_md)
        assert any(s.action.action == "import_data" for s in prepared.solution.plan.steps)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        table = access.schema(refresh=True).table("CDs")
        assert table is not None and table.record_count == len(importer.read_rows(source)) - 1
        assert table.field("Interpret") is not None, "Feldnamen aus Excel übernommen"
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
@pytest.mark.access_app
def test_live_tecomp_conditional_formatting_0807(tmp_path: Path) -> None:
    """te.comp-Übung 0807/U1: Datenblattformular auf eine Abfrage, Zeilen einfärben bei Suite, Kosten > 2000 hervorheben."""
    import shutil

    from app.access.constants import AccessType
    from app.access.specs import FieldSpec

    db = tmp_path / "08071_1.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / "hotels-dr.accdb", db)
    text = tecomp.html_to_text(tecomp.task_page(db.name, _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)

    def bookings(e: Any) -> None:  # die Vorlage enthält die Buchungsabfrage der echten Übung nicht
        e.tables.create_table("Buchungen", [FieldSpec(name="BuchungID", type=AccessType.AUTONUMBER),
                                            FieldSpec(name="Gast"), FieldSpec(name="Zimmerart"),
                                            FieldSpec(name="Kosten", type=AccessType.CURRENCY)], ["BuchungID"])
        e.tables.insert_records("Buchungen", [{"Gast": "Huber", "Zimmerart": "Suite", "Kosten": 2400},
                                              {"Gast": "Moser", "Zimmerart": "Doppelzimmer", "Kosten": 800}])
        e.queries.create_query("Zimmerbuchungen", "SELECT Gast, Zimmerart, Kosten FROM Buchungen")

    access.run(bookings)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        schema = access.schema(refresh=True)
        new_forms = [f.name for f in schema.forms]
        assert new_forms, "Formular angelegt"
        rules = {}
        for name in new_forms:
            rules.update(access.run(lambda e, n=name: e.objects.conditional_formats("form", n)))
        flat = " ".join(" ".join(v) for v in rules.values())
        assert "2000" in flat, "Kosten > 2000 hervorgehoben"
        assert "suite" in flat.lower(), "Zeilen bei Suite eingefärbt"
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.access_app
def test_live_multipart_task_with_report_and_form(sample_db_path: Path, tmp_path: Path) -> None:
    text = (
        "1. Verknüpfen Sie die Tabellen Kunden und Bestellungen über die KundenNr mit referenzieller Integrität.\n"
        "2. Erstellen Sie die Abfrage \"qryUmsatzJeLand\", die je Bundesland die Summe des Umsatzes der Kunden zeigt, "
        "absteigend nach der Summe sortiert.\n"
        "3. Erstellen Sie den Bericht \"rptKundenNachLand\" auf Basis der Tabelle Kunden mit Nachname, Ort und Umsatz, "
        "gruppiert nach Bundesland, mit der Summe des Umsatzes je Bundesland.\n"
        "4. Erstellen Sie das Formular \"frmArtikel\" für die Tabelle Artikel mit allen Feldern."
    )
    pipeline, access, storage = _pipeline(sample_db_path, tmp_path)
    try:
        prepared, outcome = _run(pipeline, text)
        assert outcome.status == "erfolgreich", outcome.error
        schema = access.schema(refresh=True)
        assert any(r.table == "Kunden" and r.foreign_table == "Bestellungen" and r.enforce_integrity for r in schema.relations)
        assert [f.name for f in schema.forms] == ["frmArtikel"] and [r.name for r in schema.reports] == ["rptKundenNachLand"]
        columns, rows = access.run(lambda e: e.queries.run_select("qryUmsatzJeLand"))
        sums = [r[-1] if isinstance(r[-1], int | float) else r[1] for r in rows]
        assert len(rows) == 7 and sums == sorted(sums, reverse=True)  # 7 Bundesländer in den Beispieldaten
    finally:
        access.shutdown()
        storage.close()


def test_live_runtime_error_is_handled_and_plan_continues(sample_db_path: Path, tmp_path: Path) -> None:
    """Ein verwaister Datensatz lässt die Beziehung erst in Access scheitern. Erwartet: Ursache erkannt,
    Korrektur nur nicht destruktiv (der Datensatz bleibt), der Rest des Plans wird trotzdem erledigt."""
    pipeline, access, storage = _pipeline(sample_db_path, tmp_path)
    access.run(lambda e: e.tables.insert_records("Bestellungen", [{"KundenNr": 4711, "ArtikelNr": 1, "Menge": 1}]))
    text = ("1. Verknüpfen Sie die Tabellen Kunden und Bestellungen über die KundenNr mit referenzieller Integrität.\n"
            "2. Erstellen Sie die Abfrage \"qryArtikelListe\" mit Bezeichnung und Einzelpreis aller Artikel, "
            "aufsteigend nach Bezeichnung sortiert.")
    try:
        _, outcome = _run(pipeline, text)
        assert access.run(lambda e: e.queries.exists("qryArtikelListe")), "zweite Teilaufgabe trotz Fehler erledigt"
        assert access.run(lambda e: e.tables.count_records("Bestellungen", "KundenNr = 4711")) == 1, "nichts gelöscht"
        relation_step = next(r for r in outcome.results if r.step_id == 1)
        assert relation_step.status in ("ok", "failed")
        if relation_step.status == "ok":
            assert "Nach Korrektur" in relation_step.message
        assert outcome.status != "erfolgreich" or relation_step.status == "ok"
    finally:
        access.shutdown()
        storage.close()


def test_live_connection_with_key_from_credential_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schlüssel in der Windows-Anmeldeinformationsverwaltung speichern, „neu starten“, echte Testanfrage."""
    import os

    from app.security.secrets import CredentialManagerBackend

    key = os.environ["ANTHROPIC_API_KEY"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # ab hier nur noch der gespeicherte Schlüssel
    backend = CredentialManagerBackend(prefix="WIDS-Access-AI-PYTEST-LIVE")
    try:
        SecretStore(backend).set_api_key("anthropic", key)
        manager = ProviderManager(AppSettings(), SecretStore(CredentialManagerBackend(prefix="WIDS-Access-AI-PYTEST-LIVE")))
        check = manager.test_connection("anthropic")
        assert check.model == "claude-opus-5-5" and check.unavailable == []
        assert check.message() == "Anthropic Claude verbunden. Getestetes Modell: claude-opus-5-5"
    finally:
        backend.delete("api-key-anthropic")


def test_live_invalid_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import ProviderAuthError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ungueltig-0000000000000000000000")
    with pytest.raises(ProviderAuthError):
        ProviderManager(AppSettings(), SecretStore(MemoryBackend())).test_connection("anthropic")


def test_live_multiple_choice_question(tmp_path: Path) -> None:
    ai = ProviderManager(AppSettings(), _secrets(), Storage(tmp_path / "q.sqlite3"))
    answer = ai.complete(system=prompts.tutor_context(None), purpose="frage", messages=[ChatMessage(role="user", content=(
        "Welche Aussage zum Primärschlüssel in Access ist richtig?\n"
        "A) Er darf doppelte Werte enthalten.\nB) Er identifiziert jeden Datensatz eindeutig.\n"
        "C) Jede Tabelle muss mindestens zwei Primärschlüsselfelder haben."))]).text
    print(answer)
    assert "B" in answer and "eindeutig" in answer.lower()


@pytest.mark.tecomp
def test_live_tecomp_datasheet_layout_0201(tmp_path: Path) -> None:
    """te.comp 0201/U3: Hotelliste einrichten – Sauna/Hallenbad nach vorne, ID ausblenden, Hotelname und Sterne
    fixieren, erste 5 Felder optimal breit. Geprüft wird der gespeicherte Zustand der Tabelle."""
    import shutil

    db = tmp_path / "02013_1.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / "hotels-dr.accdb", db)
    text = tecomp.html_to_text(tecomp.task_page(db.name, _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        hotels = access.schema(refresh=True).table("Hotels")
        assert hotels is not None
        ds = hotels.datasheet.lower()
        assert "ausgeblendet: id" in ds, ds
        frozen = ds.split("fixiert: ")[1].split(";")[0].split(", ")  # das Feld heißt in der Vorlage „Name“
        assert {"name", "sterne"} <= set(frozen), ds
        order = ds.split("spaltenreihenfolge ")[1].split(";")[0].split(", ")
        assert order.index("sauna") < 6 and order.index("hallenbad") < 6, order
        assert "spaltenbreiten:" in ds, ds
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
@pytest.mark.access_app
def test_live_tecomp_grouped_report_0501(tmp_path: Path) -> None:
    """te.comp 0501/U5: tabellarischer Bericht auf die Abfrage „Klassenliste“, nach Klasse gruppiert."""
    import shutil

    db = tmp_path / "05015_1.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / "schule-dr.accdb", db)
    text = tecomp.html_to_text(tecomp.task_page(db.name, _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        reports = access.schema(refresh=True).reports
        assert reports, "Bericht angelegt"
        info = access.run(lambda e: e.objects.inspect_report(reports[0].name))
        assert info.record_source == "Klassenliste"
        assert any("klasse" in g.expression.lower() for g in info.group_levels if g.has_header), info.group_levels
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
@pytest.mark.access_app
def test_live_tecomp_modify_existing_report_0503(tmp_path: Path) -> None:
    """te.comp 0503/U2: der VORHANDENE Bericht „Telefonliste“ (direkt auf „Clubmitglieder“) wird alphabetisch
    sortiert und auf Mitglieder gefiltert – im Bericht, ohne Datenquelle oder Bericht neu anzulegen."""
    import shutil

    # neutraler Dateiname: ein laufendes te.comp richtet Dateien mit Übungsnummer (05032_…) selbst als Übung ein
    # und benennt dabei Berichte um – das würde den Test von außen verfälschen
    db = tmp_path / "telefonliste_test.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / "golf-dr.accdb", db)
    text = tecomp.html_to_text(tecomp.task_page("05032_1.accdb", _base()).read_text(encoding="utf-8"))  # type: ignore[union-attr]
    pipeline, access, storage = _pipeline(db, tmp_path)

    def exercise_state(e: Any) -> None:  # so richtet te.comp die Übung 05032 ein (beobachtet): Berichte getauscht
        e.objects.rename("report", "Telefonliste", "Telefonliste Abfrage")
        e.objects.rename("report", "Telefonliste Tabelle", "Telefonliste")

    access.run(exercise_state)
    reports_before = sorted(r.name for r in access.schema(refresh=True).reports)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        schema = access.schema(refresh=True)
        assert sorted(r.name for r in schema.reports) == reports_before, "kein zusätzlicher Bericht"
        # in der Vorlage ist „Telefonliste Tabelle“ der Bericht direkt auf „Clubmitglieder“ („Telefonliste“ = Abfrage)
        infos = [access.run(lambda e, n=r.name: e.objects.inspect_report(n)) for r in schema.reports
                 if r.name.startswith("Telefonliste")]
        done = [i for i in infos if i.record_source == "Clubmitglieder" and "M" in i.properties.get("Filter", "")
                and "name" in i.properties.get("OrderBy", "").lower()]
        assert done, [(i.name, i.record_source, i.properties) for i in infos]
    finally:
        access.shutdown()
        storage.close()


def _template_copy(tmp_path: Path, template: str, exercise_file: str) -> tuple[Path, str]:
    """Kopie einer te.comp-Vorlage unter NEUTRALEM Namen (ein laufendes te.comp richtet Dateien mit Übungsnummer
    sonst selbst ein) und der Original-Aufgabentext der Übung."""
    import shutil

    db = tmp_path / f"praxis_{exercise_file.split('_')[0]}.accdb"
    shutil.copy(_base() / "access" / "cbt" / "database" / template, db)
    page = tecomp.task_page(exercise_file, _base())
    assert page is not None
    return db, tecomp.html_to_text(page.read_text(encoding="utf-8"))


def _rows(access: AccessService, sql_or_query: str, *, sql: bool = False) -> list[list[Any]]:
    if sql:
        return access.run(lambda e: e.queries.run_select_sql(sql_or_query, limit=5000))[1]
    return access.run(lambda e: e.queries.run_select(sql_or_query, limit=5000))[1]


def _query(schema: Any, name: str) -> Any:
    found = next((q for q in schema.queries if q.name.lower() == name.lower()), None)
    assert found is not None, f"Abfrage „{name}“ fehlt: {[q.name for q in schema.queries]}"
    return found


@pytest.mark.tecomp
def test_live_practice_grouping_0608_u2(tmp_path: Path) -> None:
    """te.comp 0608/U2 (komplex, 3 Gruppierungsabfragen): Ergebnisse unabhängig gegen eigenes SQL geprüft."""
    db, text = _template_copy(tmp_path, "golf-dr.accdb", "06082_1.accdb")
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        schema = access.schema(refresh=True)
        club = _rows(access, _query(schema, "Club-Punkte").name)
        own = _rows(access, "SELECT Club, Sum(Punktestand) FROM Turnierteilnehmer GROUP BY Club "
                            "HAVING Sum(Punktestand)>1000", sql=True)
        assert len(club) == len(own), (len(club), len(own))
        sums = [next(v for v in reversed(r) if isinstance(v, int | float)) for r in club]
        assert sums == sorted(sums, reverse=True) and min(sums) > 1000, sums[:5]
        top = _rows(access, _query(schema, "Top-Punkte je Club").name)
        own_top = _rows(access, "SELECT Club, Max(Punktestand) FROM Turnierteilnehmer GROUP BY Club", sql=True)
        assert len(top) == len(own_top)
        assert sorted(max(v for v in r if isinstance(v, int | float)) for r in top) == \
            sorted(r[1] for r in own_top), "Höchstwert je Club stimmt"
        land = _query(schema, "Bundesländerauswertung")
        assert "count(" in land.sql.lower() and "bundesland" in land.sql.lower(), land.sql
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_practice_calculated_fields_0604_u2(tmp_path: Path) -> None:
    """te.comp 0604/U2: vorhandene Abfrage „Abreiseliste“ um Tabelle „Preise“ und berechnete Felder Preis/Netto
    erweitern. Geprüft: SQL-Logik und dass die Abfrage mit echten Daten rechnet (Netto = Preis / 1,19)."""
    db, text = _template_copy(tmp_path, "seehotel-do.accdb", "06042_1.accdb")
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text, conflict="overwrite")  # Vorlage enthält gleichnamige Objekte
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        query = _query(access.schema(refresh=True), "Abreiseliste")
        sql = query.sql
        low = sql.lower()
        assert "preise" in low and "tarif1" in low and "belegung" in low and "abreise" in low, sql
        assert " as preis" in low.replace("[", "").replace("]", "") and "netto" in low, sql
        # unabhängige Rechenprobe ohne das Tagesdatum-Kriterium: Netto * 1,19 = Preis für alle Buchungen
        probe = sql.split("WHERE")[0] if "WHERE" in sql else sql.split("ORDER BY")[0]
        cols, rows = access.run(lambda e: e.queries.run_select_sql(probe, limit=200))
        names = [c.lower() for c in cols]
        assert rows, "Buchungen vorhanden"
        for row in rows:
            preis, netto = row[names.index("preis")], row[names.index("netto")]
            if preis is not None:
                assert abs(float(netto) * 1.19 - float(preis)) < 0.05, (preis, netto)
    finally:
        access.shutdown()
        storage.close()


@pytest.mark.tecomp
def test_live_practice_query_formats_0405_u1(tmp_path: Path) -> None:
    """te.comp 0405/U1: Abfrage auf zwei Tabellen mit Datumskriterium und formatierten Feldern, zweite Abfrage
    „Teilnehmerpunkte“ mit Tausendertrennzeichen – Formate und Datensätze unabhängig geprüft."""
    db, text = _template_copy(tmp_path, "golf-dr.accdb", "04051_1.accdb")
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, text, conflict="overwrite")  # Vorlage enthält gleichnamige Objekte
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        schema = access.schema(refresh=True)
        points = _query(schema, "Teilnehmerpunkte")
        own = _rows(access, "SELECT * FROM Turnierteilnehmer WHERE Punktestand>1000", sql=True)
        assert len(_rows(access, points.name)) == len(own)
        assert "#,##0.00" in points.datasheet or "standard" in points.datasheet.lower() \
            or "format(" in points.sql.lower(), (points.datasheet, points.sql)
        both = [q for q in schema.queries if "abschlagszeiten" in q.sql.lower() and "clubmitglieder" in q.sql.lower()
                and ("07" in q.sql or "month(" in q.sql.lower())]  # z. B. Day([Tag])=15 And Month([Tag])=7
        assert both, [(q.name, q.sql[:120]) for q in schema.queries]
        shown = " ".join(q.datasheet + q.sql for q in both).lower()
        assert "ddd" in shown and "personen" in shown and ("hh:nn" in shown or "hh:mm" in shown), shown
    finally:
        access.shutdown()
        storage.close()


# --------------------------------------------------------------------------- universell: unbekannte Namen/Strukturen
def test_live_unknown_database_with_arbitrary_name(tmp_path: Path) -> None:
    """Echte KI, völlig unbekannte Datei („Datenbank1.accdb“) und Struktur, sechs Teilaufgaben OHNE vorgegebene
    Objektnamen: Ergebnis wird inhaltlich und unabhängig geprüft (Objekte werden nach Inhalt gesucht, nicht nach Namen)."""
    from tests.integration.test_universal_access import MEMBERS, TASK, _create

    db = tmp_path / "Datenbank1.accdb"
    _create(db)
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        _, outcome = _run(pipeline, TASK, conflict="rename")
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error

        def independent(e: Any) -> None:
            relations = {(r.table, r.foreign_table): r.enforce_integrity for r in e.relations.list_relations()}
            assert relations.get((MEMBERS, "Buchungen")) and relations.get(("Kurse", "Buchungen")), relations
            field = e.schema().table(MEMBERS).field("E-Mail") or next(
                (f for f in e.schema().table(MEMBERS).fields if "mail" in f.name.lower()), None)
            assert field is not None and field.validation_rule, "E-Mail-Feld mit Gültigkeitsregel"
            queries = e.schema().queries
            per_course = [q for q in queries if "kurse" in q.sql.lower() and "count(" in q.sql.lower()]
            assert per_course, [(q.name, q.sql[:80]) for q in queries]
            rows = e.queries.run_select(per_course[0].name)[1]
            assert len(rows) == 3, rows  # Yoga, Pilates, Zumba
            without = [q for q in queries if "buchungen" in q.sql.lower() and q not in per_course]
            names = [[str(v) for v in r] for q in without for r in e.queries.run_select(q.name)[1]]
            assert any(row and "Strasser" in row for row in names) and not any("Bauer" in row for row in names), names
            forms = e.schema().forms
            assert forms, "Formular angelegt"
            rules = {k: v for f in forms for k, v in e.objects.conditional_formats("form", f.name).items()}
            assert any("100" in " ".join(v) for v in rules.values()), rules
            reports = e.schema().reports
            assert any(e.objects.page_setup(r.name)["ItemsAcross"] == 2 for r in reports), "zweispaltiger Bericht"

        access.run(independent)
    finally:
        access.shutdown()
        storage.close()


def test_live_workshop_without_numbering(tmp_path: Path) -> None:
    """te.comp-Workshop „CD-Sammlung“ (Datei ohne Kapitelnummer): Quelldatei CDs.xls aus dem Workshop-Ordner,
    Aufgabe aus dem OCR-Text des Workshops – Ergebnis gegen die Excel-Datei geprüft."""
    from app.access import importer

    folder = tecomp.workshop_for("CD-Sammlung_1.accdb")
    if folder is None or not (folder / "CDs.xls").exists():
        pytest.skip("te.comp-Workshop „CD-Sammlung“ nicht installiert")
    db = tmp_path / "CD-Sammlung_1.accdb"
    setup = AccessService(operation_timeout_s=120)
    setup.create_database(str(db), None)
    setup.shutdown()
    pipeline, access, storage = _pipeline(db, tmp_path)
    try:
        prepared = pipeline.prepare(CapturedTask(text=CD_WORKSHOP_OCR, method="ocr", source="tecomptraining.exe"))
        outcome = pipeline.execute(prepared, AutoInteraction(conflict="skip"))
        print(outcome.report_md)
        assert outcome.status in ("erfolgreich", "unklar"), outcome.error
        table = access.schema(refresh=True).table("CDs")
        assert table is not None and table.record_count == len(importer.read_rows(folder / "CDs.xls")) - 1
        assert table.field("Interpret") is not None, "Feldnamen aus Excel übernommen"
    finally:
        access.shutdown()
        storage.close()

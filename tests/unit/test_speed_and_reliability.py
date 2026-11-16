"""Geschwindigkeit und Zuverlässigkeit: Zeitlimit für Netzlaufwerke, keine zweite KI-Analyse/Bilderkennung für
unveränderten Inhalt, Verbindungen, Prüfungen. (Das frühere Freigabesystem ist entfernt.)"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.agent.analyzer import TaskAnalyzer
from app.agent.task_reader import TaskReader
from app.ai.manager import ProviderManager
from app.core.settings import AppSettings
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.tasks.models import CapturedTask
from app.vision import capture, tecomp
from tests.helpers import ScriptedProvider, solution


def test_unreachable_network_drive_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein hängendes Laufwerk (is_dir antwortet nicht) gilt nach dem Zeitlimit als nicht erreichbar – und wird
    danach eine Weile nicht erneut abgefragt."""
    release = threading.Event()
    calls: list[str] = []

    def slow_is_dir(self: Path) -> bool:
        calls.append(str(self))
        release.wait(10)
        return True

    monkeypatch.setattr(tecomp, "REACHABLE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(tecomp, "_REACHABLE", {})
    monkeypatch.setattr(Path, "is_dir", slow_is_dir)
    started = time.perf_counter()
    try:
        assert tecomp.reachable(Path("Q:\\")) is False
        assert tecomp.reachable(Path("Q:\\")) is False
    finally:
        release.set()
    assert time.perf_counter() - started < 2 and len(calls) == 1, "Zeitlimit greift, Ergebnis zwischengespeichert"


def _manager(tmp_path: Path, provider: ScriptedProvider) -> ProviderManager:
    settings = AppSettings()
    ai = ProviderManager(settings, SecretStore(MemoryBackend()), Storage(tmp_path / "s.sqlite3"), sleep=lambda _: None)
    ai.set_provider_for_testing("anthropic", provider)
    return ai


def test_analysis_of_unchanged_content_is_reused_until_a_failure(tmp_path: Path) -> None:
    query = {"action": "create_query", "name": "qryA", "sql": "SELECT 1"}
    provider = ScriptedProvider([solution([query]), solution([query]), solution([query])])
    analyzer = TaskAnalyzer(_manager(tmp_path, provider))
    task = CapturedTask(text="Erstellen Sie die Abfrage qryA.", method="manual")
    first = analyzer.analyze(task, None)
    second = analyzer.analyze(task, None)
    assert len(provider.requests) == 1 and second == first and second is not first, "Kopie aus dem Cache"
    analyzer.analyze(task, None, extra_context="andere Quelldatei")
    assert len(provider.requests) == 2, "anderer Inhalt = neue Analyse"
    analyzer.clear_cache()
    analyzer.analyze(task, None)
    assert len(provider.requests) == 3, "nach einem Fehlschlag wird neu geplant"


def test_vision_reading_of_the_same_window_image_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider(["Erstellen Sie eine Abfrage qryKunden."] * 3)
    ai = _manager(tmp_path, provider)
    monkeypatch.setattr(ai, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(capture, "ocr", lambda png, language="de": "")  # OCR liefert nichts → KI-Bilderkennung
    reader = TaskReader(ai.settings, ai)
    for _ in range(2):
        task = reader._from_png([b"bild-1"], method="ocr", source="tecomptraining.exe", allow_vision=True)
        assert task.method == "vision" and "qryKunden" in task.text
    assert len(provider.requests) == 1, "dasselbe Fensterbild nur einmal an die KI"
    reader._from_png([b"bild-2"], method="ocr", source="tecomptraining.exe", allow_vision=True)
    assert len(provider.requests) == 2


def test_analysis_cache_sees_every_schema_change(tmp_path: Path) -> None:
    """Auch Änderungen, die der gekürzte Prompt nicht zeigt (z. B. die 41. Abfrage), erzwingen eine neue Analyse."""
    from app.access.models import DatabaseSchema, QueryInfo

    query = {"action": "create_query", "name": "qryA", "sql": "SELECT 1"}
    provider = ScriptedProvider([solution([query]), solution([query])])
    analyzer = TaskAnalyzer(_manager(tmp_path, provider))
    task = CapturedTask(text="Erstellen Sie die Abfrage qryA.", method="manual")
    many = [QueryInfo(name=f"qry{i:02}", sql="SELECT 1") for i in range(41)]
    analyzer.analyze(task, DatabaseSchema(path="x.accdb", name="x", queries=many))
    analyzer.analyze(task, DatabaseSchema(path="x.accdb", name="x", queries=many[:40]))
    assert len(provider.requests) == 2


def test_disconnected_source_folder_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein nach der Erreichbarkeitsprüfung getrenntes Laufwerk bricht die Quelldateisuche nicht ab."""
    from app.access import importer

    gone = tmp_path / "getrennt"
    (tmp_path / "Liste.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    monkeypatch.setattr(tecomp, "workdirs", lambda: [gone])
    db = str(tmp_path / "Datenbank1.accdb")
    assert importer.mentioned_sources("Importiere Liste.csv", db) == [tmp_path / "Liste.csv"]


def test_broken_query_blocks_form_source_unless_the_plan_repairs_its_basis() -> None:
    """Nicht ausführbare Abfrage als Datenquelle → Planfehler; repariert ein früherer Schritt die Grundlage (hier:
    Tabelle neu angelegt), ist der gelesene Fehler überholt und der Plan gültig."""
    from app.access.models import DatabaseSchema, FieldInfo, QueryInfo, TableInfo
    from app.agent.planner import PlanValidator
    from app.tasks import models as m

    schema = DatabaseSchema(path="x.accdb", name="x", tables=[TableInfo(name="Kunden", fields=[
        FieldInfo(name="Nachname", type="short_text")])], queries=[QueryInfo(
            name="qryAnzeige", sql="SELECT Nachname FROM KundenAlt", error="Eingabetabelle 'KundenAlt' fehlt")])
    form = {"action": "create_form", "form": {"name": "frmA", "record_source": "qryAnzeige", "fields": ["Nachname"]}}
    plan = m.ExecutionPlan.model_validate({"steps": [{"id": 1, "title": "Formular", "action": form}]})
    assert "nicht ausführbar" in PlanValidator().validate(plan, schema).error_text()
    repair = {"action": "create_table", "name": "KundenAlt", "fields": [{"name": "Nachname", "type": "short_text"}]}
    plan = m.ExecutionPlan.model_validate({"steps": [{"id": 1, "title": "Tabelle", "action": repair},
                                                     {"id": 2, "title": "Formular", "action": form}]})
    assert "nicht ausführbar" not in PlanValidator().validate(plan, schema).error_text()


def test_notes_columns_are_imported_as_long_text() -> None:
    """Praxistest CD-Sammlung: te.comp erwartet für „Anmerkung“ den Typ Langer Text, auch bei kurzen Werten."""
    from app.access import importer
    from app.access.constants import AccessType

    specs = importer.infer_fields(["Titel", "Anmerkung", "Bemerkungen"], [["A", "kurz", ""], ["B", "", "x"]])
    assert [s.type for s in specs] == [AccessType.SHORT_TEXT, AccessType.LONG_TEXT, AccessType.LONG_TEXT]


def test_screeninfo_exercise_finds_its_info_page_with_umlauts(tmp_path: Path) -> None:
    """E-01 SI: „0501S2_….accdb“ gehört zur Bildschirminfo SI-02 (Windows-1252) – sie sagt, WO die Aufgabe gemeint ist."""
    folder = tmp_path / "access" / "cbt" / "0501" / "screeninfo"
    folder.mkdir(parents=True)
    (folder / "SI-02a.htm").write_bytes("<p>In der Layoutansicht jede zweite Zeile färben</p>".encode("cp1252"))
    page = tecomp.screeninfo_page("0501S2_12345678.accdb", tmp_path)
    assert page == folder / "SI-02a.htm"
    assert "Layoutansicht jede zweite Zeile färben" in tecomp.html_to_text(tecomp.read_page(page))
    assert tecomp.screeninfo_page("05012_12345678.accdb", tmp_path) is None, "normale Übung: Aufgabenseite statt Info"


def test_control_change_details_are_read_back_not_assumed() -> None:
    """Codex-Befund: Breite/Sichtbarkeit/Farbe wurden nicht geprüft – ein unverändertes Steuerelement bestand."""
    from app.access.models import ControlInfo
    from app.access.specs import ControlChange
    from app.agent.verifier import Verification, _check_control_details

    unchanged = ControlInfo(name="txtOrt", control_type=109, width=3402, height=340, top=600, formatting="")
    change = ControlChange.model_validate({"name": "txtOrt", "width_cm": 2.0, "visible": False, "bold": True,
                                          "back_color": "#FFFF00"})
    v = Verification(step_id=1)
    _check_control_details(unchanged, change, v)
    assert not any(c.passed for c in v.checks) and len(v.checks) == 4
    done = ControlInfo(name="txtOrt", control_type=109, width=1134, height=340, top=600,
                       formatting="fett, unsichtbar, Hintergrund #FFFF00")
    v = Verification(step_id=1)
    _check_control_details(done, change, v)
    assert v.passed, [c for c in v.checks if not c.passed]


class _FakeSession:
    def __init__(self, alive: bool) -> None:
        self.alive, self.killed = alive, 0

    def is_alive(self) -> bool:
        return self.alive

    def kill_owned_instance(self) -> bool:
        self.killed += 1
        return True


class _FakeEngine:
    def __init__(self, alive: bool) -> None:
        self.session, self.closed, self.path = _FakeSession(alive), 0, "x.accdb"

    def close(self) -> None:
        self.closed += 1


def test_dead_connection_is_closed_gently_busy_or_replaced_ones_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Praxis 15.11.: 9 Fehlschläge nach Access-Neustart. Tot → ordentlich schließen (nie hart beenden);
    beschäftigt (Zeitüberschreitung) → behalten; inzwischen neu verbunden → die neue Verbindung nicht anfassen."""
    from app.access.service import AccessService
    from app.core.errors import AccessTimeoutError

    service = AccessService()
    try:
        dead = _FakeEngine(alive=False)
        service._engine = dead  # type: ignore[assignment]
        assert service.is_alive() is False and service._engine is None
        assert dead.closed == 1 and dead.session.killed == 0, "ordentlich geschlossen, keine fremde Instanz beendet"
        busy = _FakeEngine(alive=True)
        service._engine = busy  # type: ignore[assignment]

        def timeout(*a: Any, **k: Any) -> Any:
            raise AccessTimeoutError()

        monkeypatch.setattr(service._worker, "call", timeout)
        assert service.is_alive() is True and service._engine is busy, "beschäftigt ≠ tot"
        monkeypatch.undo()
        replaced = _FakeEngine(alive=True)
        service._engine = replaced  # type: ignore[assignment]
        service._drop_dead_engine(dead)  # type: ignore[arg-type]  # veraltete Prüfung einer alten Verbindung
        assert service._engine is replaced and replaced.closed == 0
    finally:
        service._engine = None
        service.shutdown()


def test_reverification_only_when_a_later_step_recreates_the_object(tmp_path: Path) -> None:
    """Codex-Befund: ein späteres Neu-Anlegen kann frühere Ergebnisse zerstören → Neuprüfung; gewollte Änderungen
    (modify_*) lösen keine Neuprüfung aus, Datenschritte nie."""
    from app.agent.executor import StepResult
    from app.agent.verifier import Verification
    from app.tasks import models as m
    from tests.helpers import build_harness

    h = build_harness(None, [], tmp_path)
    seen: list[int] = []

    class Verifier:
        def verify(self, engine: Any, step: Any, result: Any) -> Verification:
            seen.append(step.id)
            return Verification(step_id=step.id)

    h.pipeline._verifier = Verifier()  # type: ignore[assignment]
    h.pipeline._access.run = lambda func, **k: func(None)  # type: ignore[method-assign]
    step = lambda i, action: m.PlanStep.model_validate({"id": i, "title": str(i), "action": action})  # noqa: E731
    fmt = {"action": "set_conditional_format", "object_type": "form", "name": "frmA", "control": "Umsatz",
           "rules": [{"expression": "[Umsatz]>10", "back_color": "#FF0000"}]}
    recreate = {"action": "create_form", "form": {"name": "frmA", "record_source": "Kunden", "fields": ["Umsatz"]}}
    modify = {"action": "modify_form", "name": "frmA", "changes": {"caption": "Neu"}}
    ok = [StepResult(step_id=i) for i in (1, 2)]
    try:
        assert len(h.pipeline._reverify_touched([step(1, fmt), step(2, recreate)], ok)) == 1 and seen == [1]
        seen.clear()
        assert h.pipeline._reverify_touched([step(1, fmt), step(2, modify)], ok) == [] and seen == []
    finally:
        h.close()

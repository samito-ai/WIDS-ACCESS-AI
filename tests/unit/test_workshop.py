"""Ganzer te.comp-Workshop mit einem Alt+W: Aufgabenleiste lesen (geblättert mit te.comps Pfeilen, danach zurück),
Teilaufgaben zerlegen, Erfassung zusammenführen und den Workshop-Status nur bei vollständigem Nachweis vergeben."""
from __future__ import annotations

from typing import Any

import pytest

from app.agent.orchestrator import WORKSHOP_DONE, workshop_summary
from app.agent.progress import ProgressTracker
from app.tasks import models as m
from app.vision import tecomp

CARDS = ["CD-Sammlung: Lesen Sie die Ausgangssituation.", "Informieren Sie sich! Datenbanken im Musikbereich.",
         "o 1. Die CD-Tabelle: Tabelle CDs aus CDs.xls.", "o 2. Felddatentypen wählen.", "3. Feld Cover anlegen.",
         "o 4. Daten filtern nach Jazz.", "o 5. Tabelle Tracks.", "o 6. Abfrage Top-Jazz."]


class FakeStrip:
    """Nachbau der te.comp-Leiste: 3 Karten sichtbar, jeder Pfeilklick verschiebt um eine Karte (277 px)."""

    def __init__(self, cards: list[str], position: int = 0, stuck_after: int | None = None) -> None:
        self.cards, self.position, self.stuck_after, self.clicks = cards, position, stuck_after, 0

    count = property(lambda self: len(self.cards))

    def offset(self) -> int:
        return 379 - 277 * self.position

    def at_start(self) -> bool:
        return self.position == 0

    def at_end(self) -> bool:
        return self.position + 3 >= len(self.cards)

    def visible_cards(self) -> dict[int, str]:
        return {i: self.cards[i] for i in range(self.position, min(self.position + 3, len(self.cards)))}

    def click(self, direction: int) -> None:
        self.clicks += 1
        if self.stuck_after is not None and self.clicks > self.stuck_after:
            return  # te.comp reagiert nicht mehr
        self.position = max(0, min(self.position + direction, len(self.cards) - 3))


def test_reads_every_card_and_restores_the_users_position() -> None:
    strip = FakeStrip(CARDS, position=2)  # der Benutzer hatte schon weitergeblättert
    cards, restored, problem = tecomp.read_strip(strip, wait=0.3, sleep=lambda s: None)
    assert [cards[i] for i in range(len(CARDS))] == CARDS, "auch die Karten LINKS der Ausgangsposition"
    assert restored and strip.position == 2 and problem == ""
    steps = tecomp.parse_steps([cards[i] for i in range(len(CARDS))])
    assert steps.complete and [s.split(".")[0] for s in steps.steps] == ["1", "2", "3", "4", "5", "6"]
    assert steps.steps[0] == "1. Die CD-Tabelle: Tabelle CDs aus CDs.xls.", "OCR-Kästchen „o“ entfernt"
    assert len(steps.notes) == 2 and steps.notes[1].startswith("Informieren")


def test_stuck_navigation_is_reported_and_never_complete() -> None:
    strip = FakeStrip(CARDS, stuck_after=1)
    cards, _, problem = tecomp.read_strip(strip, wait=0.3, sleep=lambda s: None)
    assert len(cards) < len(CARDS) and "nicht weiterblättern" in problem


@pytest.mark.parametrize(("cards", "reason"), [
    (["1. A", "", "3. C"], "nicht lesbar"),
    (["1. A", "3. C"], "nicht lückenlos"),
    (["Nur ein Hinweis"], "keine nummerierten"),
    (["Hinweis", "1. Tabelle anlegen", "Z. Abfrage erstellen"], "keine erkennbare Nummer"),  # OCR verlor die „2“
])
def test_incomplete_step_lists_are_never_complete(cards: list[str], reason: str) -> None:
    steps = tecomp.parse_steps(cards)
    assert not steps.complete and reason in steps.problem


def _task(total: int, complete: bool = True) -> m.CapturedTask:
    return m.CapturedTask(text="Workshop", workshop_steps=total, workshop_complete=complete)


def _report(statuses: list[str]) -> m.RequirementReport:
    return m.RequirementReport(results=[m.RequirementResult(number=i, status=s)  # type: ignore[arg-type]
                                        for i, s in enumerate(statuses, 1)])


def test_workshop_counts_only_fully_proven_subtasks() -> None:
    reqs = ["[1] Tabelle CDs", "[1] Feldnamen aus Excel", "[2] Abfrage", "[3] Sortierung"]
    proven, text = workshop_summary(_task(3), reqs, _report(["erfüllt"] * 4))
    assert proven == 3 and text.startswith(WORKSHOP_DONE)
    proven, text = workshop_summary(_task(3), reqs, _report(["erfüllt", "unklar", "erfüllt", "erfüllt"]))
    assert proven == 2 and "nicht nachgewiesen: 1" in text and not text.startswith(WORKSHOP_DONE)
    proven, text = workshop_summary(_task(3), [*reqs[:3], "Sortierung ohne Nummer"], _report(["erfüllt"] * 4))
    assert proven == 2 and "ohne Anforderung im Plan: 3" in text, "Anforderung ohne [n] zählt für keine Teilaufgabe"
    proven, text = workshop_summary(_task(4), reqs, _report(["erfüllt"] * 4))
    assert proven == 3 and "ohne Anforderung im Plan: 4" in text, "vergessene Teilaufgabe ist nie erledigt"
    _, text = workshop_summary(_task(3, complete=False), reqs, _report(["erfüllt"] * 4))
    assert "nicht alle Teilaufgaben erfassbar" in text and not text.startswith(WORKSHOP_DONE)
    _, text = workshop_summary(_task(0, complete=False), reqs, None)
    assert "nur die sichtbare Teilaufgabe" in text


def test_progress_shows_recognised_and_proven_subtasks() -> None:
    tracker = ProgressTracker()
    tracker.start()
    tracker.workshop_total = 12
    assert tracker.lines()[0] == "Workshop: 12 Teilaufgaben erkannt"
    tracker.on_event("status", "teilweise", data={"workshop": [9, 12]})
    assert tracker.lines()[0] == "Workshop: 12 Teilaufgaben erkannt, 9 nachgewiesen"


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    from app.agent.task_reader import TaskReader
    from app.core.settings import AppSettings
    from app.vision import capture

    monkeypatch.setattr(capture, "environment", lambda: (["te.comp-training"], ["tecomptraining.exe"]))
    monkeypatch.setattr(tecomp, "workshop_context", lambda folder: "Hilfe: So ändern Sie einen Feldnamen")
    return TaskReader(AppSettings(), ai=None)  # type: ignore[arg-type]


def test_capture_merges_all_subtasks_into_one_task(reader: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(tecomp, "workshop_steps", lambda: tecomp.parse_steps(CARDS))
    task = m.CapturedTask(text="1. Die CD-Tabelle (nur sichtbar)", method="ocr")
    reader._whole_workshop(task, tmp_path / "CD-Sammlung")
    assert task.workshop_steps == 6 and task.workshop_complete and not task.warnings
    assert all(f"{n}. " in task.text for n in range(1, 7)) and "Abfrage Top-Jazz" in task.text
    assert "[3]" in task.text and "Informieren Sie sich" in task.text and "Feldnamen" in task.text


def test_capture_without_full_list_never_counts_as_whole_workshop(reader: Any, monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Any) -> None:
    monkeypatch.setattr(tecomp, "workshop_steps", lambda: None)
    task = m.CapturedTask(text="1. Die CD-Tabelle", method="ocr")
    reader._whole_workshop(task, tmp_path / "CD-Sammlung")
    assert task.workshop_steps == 0 and not task.workshop_complete and "sichtbare Teilaufgabe" in task.warnings[0]
    assert task.text.startswith("1. Die CD-Tabelle"), "die sichtbare Teilaufgabe bleibt erfasst"

    monkeypatch.setattr(tecomp, "workshop_steps", lambda: tecomp.parse_steps(["1. A", "3. C"]))
    task = m.CapturedTask(text="x", method="ocr")
    reader._whole_workshop(task, tmp_path / "CD-Sammlung")
    assert task.workshop_steps == 2 and not task.workshop_complete and "nicht vollständig" in task.warnings[0]


def test_no_navigation_inside_an_exam(reader: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from app.vision import capture

    monkeypatch.setattr(capture, "environment", lambda: ([], ["safeexambrowser.exe"]))
    monkeypatch.setattr(tecomp, "workshop_steps", lambda: pytest.fail("in einer Prüfung wird nichts angeklickt"))
    task = m.CapturedTask(text="x", method="ocr")
    reader._whole_workshop(task, tmp_path / "CD-Sammlung")
    assert not task.workshop_complete


@pytest.mark.parametrize(("sql", "target"), [
    ("UPDATE [CD Titel] SET Kategorie = 'Jazz'", "cd titel"),  # Codex: Name mit Leerzeichen nie abschneiden
    ("UPDATE Tracks SET [CD-Nummer] = DLookUp('Nr', 'CDs')", "tracks"),
    ("INSERT INTO [Neue Tabelle] (A) VALUES (1)", "neue tabelle"),
    ("DELETE FROM Tracks WHERE Titelnummer > 5", "tracks"),
    ("UPDATE Neu INNER JOIN Alt ON Neu.ID = Alt.ID SET Alt.X = 0", ""),  # schreibt in Alt: keine Ausnahme
    ("DELETE Alt.* FROM Alt INNER JOIN Neu ON Alt.ID = Neu.ID", ""),
    ("SELECT * INTO Kopie FROM Alt", ""),
])
def test_write_target_is_only_recognised_for_unambiguous_single_table_statements(sql: str, target: str) -> None:
    from app.agent.orchestrator import _write_target

    assert _write_target(m.RunActionQuery(sql=sql), {}) == target


def test_verified_attachments_and_action_queries_count_as_data_evidence() -> None:
    """Live-Fund: „Bilddateien für die ersten 5 CDs“ war erledigt und nachgeprüft, galt aber als „nicht nachweisbar“,
    weil die Anforderungsprüfung den bestätigten Anlage-Schritt nicht als Beleg bekam."""
    from app.agent.orchestrator import TaskPipeline
    from app.agent.verifier import Check, Verification

    attach = {"action": "attach_files", "table": "CDs", "field": "Cover", "key_field": "CD-Nummer",
              "files": {"1": "1.bmp"}}
    solution = m.AgentSolution.model_validate({
        "analysis": {"task_type": "data_editing", "title": "t", "summary": "s", "requirements": ["[3] Cover"],
                     "confidence": 0.9, "explanation": "e"},
        "plan": {"steps": [{"id": 1, "title": "Cover", "action": attach, "covers": [1]}], "notes": []}})
    passed = Verification(step_id=1, checks=[Check(name="„1.bmp“ bei CD-Nummer = 1", passed=True)])
    assert any("attach_files" in line and "1.bmp" in line for line in TaskPipeline._data_evidence(solution, [passed]))
    failed = Verification(step_id=1, checks=[Check(name="„1.bmp“ bei CD-Nummer = 1", passed=False)])
    assert TaskPipeline._data_evidence(solution, [failed]) == [], "nicht bestandene Prüfung ist nie ein Beleg"


@pytest.mark.parametrize(("sql", "fields"), [
    ("UPDATE Tracks INNER JOIN CDs ON Tracks.CD = CDs.Titel SET Tracks.[CD-Nummer] = CDs.[CD-Nummer];",
     [("Tracks", "CD-Nummer", "")]),
    ("UPDATE CDs AS c SET c.Cover = 1 WHERE c.Nr < 5", [("CDs", "Cover", "Nr < 5")]),  # Alias aufgelöst
    ("UPDATE Tracks SET Liedtext = Null, [CD-Nummer] = DLookUp('Nr', 'CDs', 'A=''x, y''')", [("Tracks", "CD-Nummer", "")]),
    ("SELECT * FROM Tracks", []),
])
def test_updated_fields_of_update_queries(sql: str, fields: list[tuple[str, str, str]]) -> None:
    from app.agent.verifier import _fills, _updated_fields

    assert _updated_fields(sql) == fields
    assert _fills("=[Preis]*2") and not _fills("=Null") and not _fills("Text"), "„=Null“ leert absichtlich"


def test_external_target_never_counts_as_a_table_of_this_run() -> None:
    from app.agent.orchestrator import _write_target

    assert _write_target(m.RunActionQuery(sql="INSERT INTO Fresh IN 'C:\\Alt.accdb' SELECT * FROM X"), {}) == ""


def test_cascading_tables_never_get_the_fresh_table_exception() -> None:
    from types import SimpleNamespace

    from app.access.models import RelationInfo
    from app.agent.orchestrator import _cascades_from

    schema = SimpleNamespace(relations=[RelationInfo(name="r", table="Neu", foreign_table="Alt", cascade_delete=True)])
    assert _cascades_from("neu", schema) and not _cascades_from("Alt", schema)


def test_operator_steps_are_reported_but_never_requirements() -> None:
    """Fall „Werte ausschließen“: „im Datenblatt kontrollieren“ ist ein Bedienschritt – nie als erledigt gemeldet, aber
    auch kein Grund, die richtige Abfrage als „nicht abgeschlossen“ zu werten."""
    from app.agent.explainer import analysis_markdown

    analysis = m.TaskAnalysis(title="Werte ausschließen", requirements=["Abfrage schließt Salzburg mit 18 Löchern aus"],
                              operator_steps=["Ergebnis in der Datenblattansicht kontrollieren"])
    text = analysis_markdown(analysis)
    assert "nicht ausgeführt" in text and "Datenblattansicht kontrollieren" in text
    assert analysis.requirements == ["Abfrage schließt Salzburg mit 18 Löchern aus"]

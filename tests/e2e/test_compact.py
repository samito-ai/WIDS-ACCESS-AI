"""GUI-Tests des Kompaktmodus: Status, Freigabe ohne Wechsel ins Hauptfenster, Abbruch, Fragenmodus."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.agent.orchestrator import ApprovalDecision
from app.core.context import AppContext
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.tasks.models import CapturedTask
from app.ui.main_window import MainWindow
from app.utils import sampledb
from tests.helpers import ScriptedProvider, solution

pytestmark = [pytest.mark.gui, pytest.mark.access]


@pytest.fixture
def compact(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MainWindow, ScriptedProvider]]:
    ctx = AppContext(Storage(tmp_path / "c.sqlite3"), SecretStore(MemoryBackend()))
    ctx.settings.hotkeys.enabled = False
    provider = ScriptedProvider([])
    ctx.ai.set_provider_for_testing("anthropic", provider)
    ctx.pipeline._environment_probe = lambda: ([], [])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.No)
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.compact.show_raise()  # Hauptfenster bleibt verborgen = Kompaktmodus
    yield window, provider
    window.compact.hide()
    window.close()
    ctx.shutdown()


def test_compact_mode_runs_whole_task_without_main_window(compact: Any, qtbot: Any, tmp_path: Path) -> None:
    window, provider = compact
    db = tmp_path / "kompakt.accdb"
    window.ctx.access.create_database(str(db), sampledb.populate)
    window.ctx.access.disconnect()  # Alt+W soll die Datenbank der Aufgabe selbst verbinden
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryKompakt",
                                                    "sql": "SELECT Nachname FROM Kunden"}}]))
    parents = []
    from app.security import programs

    tables = programs.Program("Kap.02", "DA - Tabellen", ("02*",))
    window.ctx.pipeline._program_lookup = lambda path: tables
    programs.approve(window.ctx.settings.security.program_approvals, tables)  # einmal im Dashboard freigegeben
    window._approval_dialog = lambda request: parents.append(window._dialog_parent()) or ApprovalDecision()
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text="Erstellen Sie die Abfrage qryKompakt mit allen Nachnamen.", method="tecomp",
        source="tecomptraining.exe", db_path=str(db))
    window.compact.btn_solve.click()  # entspricht Alt+W
    qtbot.waitUntil(lambda: "Erfolgreich" in window.compact.status.text(), timeout=60000)
    assert not window.isVisible(), "kein Wechsel ins große Fenster"
    assert parents == [], "ohne Dashboard kein Freigabedialog – die Lernprogramm-Freigabe gilt"
    assert window.ctx.access.path.lower() == str(db).lower(), "mit der Datenbank der Aufgabe verbunden"
    assert window.ctx.access.run(lambda e: e.queries.exists("qryKompakt"))
    log = [window.compact.log.item(i).text() for i in range(window.compact.log.count())]
    assert any("Prüfung bestanden" in line for line in log) and any("Anforderung" in line for line in log)
    assert "kompakt.accdb" in window.compact.db.text()


def test_error_in_compact_mode_opens_no_dialog(compact: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex-Befund: Auch bei offenem Kompaktfenster (Dashboard geschlossen) erscheint kein Fehlerdialog – nur roter
    Punkt, rotes Symbol und der Eintrag im Statusfenster."""
    from app.core.errors import WidsError

    window, _ = compact
    boxes: list[Any] = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: boxes.append(a) or QMessageBox.StandardButton.No)

    def fail() -> CapturedTask:
        raise WidsError("Kein te.comp-Fenster gefunden.")

    window.ctx.reader.from_window = fail  # type: ignore[method-assign]
    window.compact.btn_solve.click()
    qtbot.waitUntil(lambda: not window._busy and window.tray_state == "error", timeout=10000)
    assert boxes == [] and not window.isVisible() and window.overlay.state == "error"
    assert any("te.comp" in window.compact.log.item(i).text() for i in range(window.compact.log.count()))


def test_foreign_window_text_is_not_sent_automatically(compact: Any, qtbot: Any) -> None:
    window, provider = compact
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text="Privater Text aus einem anderen Fenster", method="ocr", source="chrome.exe")
    window.compact.btn_solve.click()
    qtbot.waitUntil(lambda: "nicht gefunden" in window.compact.status.text(), timeout=10000)
    window.compact.btn_screen.click()
    qtbot.waitUntil(lambda: "nicht gesendet" in window.compact.answer.toPlainText(), timeout=10000)
    assert provider.requests == [], "nichts an die KI gesendet"


def test_question_mode_answers_and_copies(compact: Any, qtbot: Any) -> None:
    window, provider = compact
    provider.responses.append("**B** ist richtig: Ein Primärschlüssel identifiziert jeden Datensatz eindeutig.")
    window.compact.question.setText("Welche Aussage zum Primärschlüssel stimmt? A) doppelt erlaubt B) eindeutig")
    window.compact.btn_ask.click()
    qtbot.waitUntil(lambda: "eindeutig" in window.compact.answer.toPlainText(), timeout=10000)
    window.compact.btn_copy.click()
    assert "Primärschlüssel identifiziert" in QApplication.clipboard().text()
    assert "Primärschlüssel stimmt" in provider.requests[0].messages[-1].content

"""GUI-Tests (pytest-qt, offscreen): Navigation, Einstellungen, kompletter Ablauf, Abbruch, Fehlerdialoge."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from app.agent.orchestrator import ApprovalDecision
from app.core.context import AppContext
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.ui.main_window import PAGES, MainWindow
from app.utils import sampledb
from tests.helpers import ScriptedProvider, solution

pytestmark = [pytest.mark.gui, pytest.mark.access]


@pytest.fixture
def app_window(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MainWindow, ScriptedProvider, list[str]]]:
    ctx = AppContext(Storage(tmp_path / "gui.sqlite3"), SecretStore(MemoryBackend()))
    ctx.settings.hotkeys.enabled = False  # keine globalen Kürzel während der Tests registrieren
    provider = ScriptedProvider([])
    ctx.ai.set_provider_for_testing("anthropic", provider)
    ctx.pipeline._environment_probe = lambda: ([], [])
    messages: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: messages.append(a[2]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: messages.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()
    yield window, provider, messages
    window.close()
    ctx.shutdown()


def test_navigation_and_dashboard(app_window: Any) -> None:
    window, _, messages = app_window
    for index, _name in enumerate(PAGES):
        window.nav.setCurrentRow(index)
        assert window.stack.currentIndex() == index
    assert "Nicht verbunden" in window.dash["access"].text()
    assert "Schlüssel hinterlegt" in window.dash["ai"].text()
    assert "Tastenkürzel aus" in window.dash["auto"].text()
    assert messages == []


def test_settings_validation_and_persistence(app_window: Any) -> None:
    window, _, messages = app_window
    keys = window.set_hotkey
    keys["solve"].setKeySequence(QKeySequence("Alt+F4"))
    window.save_settings()
    assert "reserviert" in messages[-1]
    keys["solve"].setKeySequence(QKeySequence("Ctrl+Alt+Q"))
    window.save_settings()
    assert "AltGr" in messages[-1], "Konflikt mit dem @-Zeichen der deutschen Tastatur erkannt"
    keys["solve"].setKeySequence(QKeySequence("Ctrl+Alt+W"))
    keys["analyze"].setKeySequence(QKeySequence("Alt+Ctrl+W"))
    window.save_settings()
    assert "doppelt" in messages[-1]
    keys["analyze"].setKeySequence(QKeySequence("Ctrl+Alt+T"))
    assert len(keys) == 7 and keys["settings"].keySequence().toString() == "Ctrl+Alt+S"
    window.set_theme.setCurrentIndex(window.set_theme.findData("light"))
    window.set_mass.setValue(7)
    assert window.set_diagnostic.isChecked(), "Diagnosemodus standardmäßig an"
    window.set_diagnostic.setChecked(False)
    window.set_key.setText("sk-ant-test-1234567890abcdef")
    window.save_key()
    window.save_settings()
    assert "sk-a" in window.set_key_state.text() and "1234567890" not in window.set_key_state.text()
    saved = window.ctx.storage.load_settings()
    assert (saved.hotkeys.solve, saved.ui.theme, saved.security.mass_change_threshold) == ("Ctrl+Alt+W", "light", 7)
    assert saved.automation.diagnostic_report is False
    assert "#f4f5fb" in window.styleSheet()


def test_full_workflow_through_ui(app_window: Any, qtbot: Any, tmp_path: Path) -> None:
    window, provider, messages = app_window
    window.ctx.access.create_database(str(tmp_path / "gui.accdb"), sampledb.populate)
    window.refresh_explorer()
    qtbot.waitUntil(lambda: window.tree.topLevelItemCount() == 5, timeout=20000)
    assert window.tree.topLevelItem(0).text(0) == "Tabellen (3)"
    window._tree_clicked(window.tree.topLevelItem(0).child(2))
    qtbot.waitUntil(lambda: window.table.rowCount() == 12, timeout=20000)

    provider.responses.append(solution([{"action": {
        "action": "create_query", "name": "qryImst",
        "spec": {"tables": ["Kunden"], "fields": [{"field": "Nachname", "sort": "ASC"}, {"field": "Ort", "criteria": "Imst"}]}}}]))
    requests = []
    window._approval_dialog = lambda request: requests.append(request) or ApprovalDecision(approved=True, practice_confirmed=True)
    window.nav.setCurrentRow(1)
    window.analyze(execute=False)
    assert "Aufgabenstellung" in messages[-1], "ohne Text erscheint ein verständlicher Hinweis"
    window.task_text.setPlainText("Erstellen Sie die Abfrage qryImst mit allen Kunden aus Imst, sortiert nach Nachname.")
    window.btn_analyze.click()
    assert window.btn_cancel.isEnabled() and not window.btn_analyze.isEnabled()
    qtbot.waitUntil(lambda: window._prepared is not None and not window._busy, timeout=30000)
    assert "qryImst" in window.task_view.toPlainText() and window.btn_run.isEnabled()
    window.btn_run.click()
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None, timeout=60000)
    assert requests == [], "kein Freigabedialog – der Benutzer hat die Ausführung selbst gestartet"
    assert "Erfolgreich" in window.task_status.text()
    assert window.ctx.access.run(lambda e: e.queries.run_select("qryImst"))[1] == [["Auer", "Imst"], ["Gruber", "Imst"]]
    assert any("Prüfung bestanden" in window.task_log.item(i).text() for i in range(window.task_log.count()))
    window.nav.setCurrentRow(4)
    assert window.history_list.count() == 1
    window.history_list.setCurrentRow(0)
    assert "qryImst" in window.history_view.toPlainText()
    window.nav.setCurrentRow(0)
    assert "gui.accdb" in window.dash["db"].text() and window.dash_tasks.count() == 1


def test_cancel_during_execution(app_window: Any, qtbot: Any, tmp_path: Path) -> None:
    window, provider, _ = app_window
    window.ctx.access.create_database(str(tmp_path / "cancel.accdb"), sampledb.populate)
    provider.responses.append(solution([
        {"action": {"action": "create_query", "name": f"q{i}", "sql": "SELECT Nachname FROM Kunden"}} for i in (1, 2, 3)]))
    window._approval_dialog = lambda request: ApprovalDecision(approved=True, practice_confirmed=True)
    window.bridge.pipeline_event.connect(lambda e: window.cancel() if e.kind == "step_done" and e.step_id == 1 else None)
    window.task_text.setPlainText("Erstellen Sie drei Abfragen q1, q2 und q3.")
    window.analyze(execute=True)
    qtbot.waitUntil(lambda: "Abgebrochen" in window.task_status.text(), timeout=60000)
    assert window.ctx.access.run(lambda e: [e.queries.exists(f"q{i}") for i in (1, 2, 3)]) == [True, False, False]


def test_rejected_approval_and_chat(app_window: Any, qtbot: Any, tmp_path: Path) -> None:
    window, provider, _ = app_window
    window.ctx.access.create_database(str(tmp_path / "nein.accdb"), sampledb.populate)
    provider.responses += [solution([{"action": {"action": "delete_table", "name": "Artikel"}}]),
                           "Ein **Primärschlüssel** identifiziert jeden Datensatz eindeutig."]
    window._approval_dialog = lambda request: ApprovalDecision(approved=False)
    window.task_text.setPlainText("Löschen Sie die Tabelle Artikel.")
    window.analyze(execute=True)
    qtbot.waitUntil(lambda: "Nicht freigegeben" in window.task_status.text(), timeout=60000)
    assert window.ctx.access.run(lambda e: e.tables.table_exists("Artikel"))
    window.nav.setCurrentRow(2)
    window.chat_input.setPlainText("Was ist ein Primärschlüssel?")
    window.send_chat()
    qtbot.waitUntil(lambda: "eindeutig" in window.chat_view.toPlainText(), timeout=20000)
    assert "STRUKTUR DER AKTUELL GEÖFFNETEN DATENBANK" in provider.requests[-1].system
    assert len(window.ctx.storage.list_chat_messages()) == 2


# --------------------------------------------------------------------------- Ruhemodus, Statuspunkt, Alt+W ohne Freigabe
def test_background_mode_opens_no_dialog_no_toast_and_keeps_red_dot(app_window: Any, qtbot: Any, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Dashboard geschlossen: keine Dialoge, keine Windows-Benachrichtigung, kein Fenster geht auf. Ein löschender
    Plan wird im Hintergrund nie automatisch ausgeführt: angehalten, im Verlauf gespeichert, Statuspunkt rot (bleibt)."""
    from PySide6.QtWidgets import QSystemTrayIcon

    window, provider, messages = app_window
    toasts: list[Any] = []
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda *a, **k: toasts.append(a))
    dialogs: list[Any] = []
    window._approval_dialog = lambda request: dialogs.append(request) or ApprovalDecision(approved=True, practice_confirmed=True)
    window.ctx.access.create_database(str(tmp_path / "ruhe.accdb"), sampledb.populate)
    provider.responses.append(solution([{"action": {"action": "delete_records", "table": "Bestellungen",
                                                    "where": "Menge > 0"}}], task_type="data_editing"))
    before = window.ctx.access.run(lambda e: e.tables.count_records("Bestellungen"))
    window.task_text.setPlainText("Löschen Sie alle Bestellungen mit einer Menge.")
    window.hide()
    assert window.mode() == "background" and not window.bridge.can_ask()
    window.analyze(execute=True)
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None, timeout=60000)
    assert dialogs == [] and toasts == [] and messages == [], "kein Dialog, keine Benachrichtigung"
    assert not window.isVisible() and not window.compact.isVisible(), "kein Fenster öffnet sich von selbst"
    assert window.ctx.access.run(lambda e: e.tables.count_records("Bestellungen")) == before, "nichts gelöscht"
    record = window.ctx.storage.list_tasks(limit=1)[0]
    assert record.status == "blockiert" and "löschende" in (record.error or "")
    assert window.overlay.state == "error" and window.tray_state == "error"
    qtbot.wait(3500)
    assert window.overlay.state == "error", "Rot bleibt stehen, bis die nächste Aufgabe startet"
    window.notify("Testfehler")
    assert toasts == []


def test_background_alt_w_runs_directly_without_any_approval(app_window: Any, qtbot: Any, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Alt+W im Hintergrund: keine Freigabe, keine Rückfrage – beliebiger Dateiname, Ergebnis geprüft, kein Fenster."""
    from app.tasks.models import CapturedTask

    window, provider, _ = app_window
    db = tmp_path / "Irgendein Name (Kopie) 2.accdb"
    window.ctx.access.create_database(str(db), sampledb.populate)
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryDirekt",
                                                    "sql": "SELECT Nachname FROM Kunden"}}]))
    task = CapturedTask(text="Erstellen Sie die Abfrage qryDirekt mit allen Nachnamen.", method="tecomp",
                        source="tecomptraining.exe", db_path=str(db))
    monkeypatch.setattr(window.ctx.reader, "from_window", lambda: task)
    window.hide()
    window.capture_task(then="execute")
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None
                    and bool(window.ctx.storage.list_tasks(limit=1))
                    and window.ctx.storage.list_tasks(limit=1)[0].status not in ("erkannt", "geplant", "läuft"),
                    timeout=60000)
    record = window.ctx.storage.list_tasks(limit=1)[0]
    assert record.status == "erfolgreich", record.error
    assert window.ctx.access.run(lambda e: e.queries.exists("qryDirekt"))
    assert window.overlay.state == "done" and not window.isVisible()


def test_no_approval_system_left_and_old_settings_still_load() -> None:
    """Das 24-Stunden-Freigabesystem ist entfernt; gespeicherte Einstellungen älterer Versionen laden trotzdem."""
    import json

    from app.core.settings import AppSettings
    from app.ui.main_window import MainWindow

    assert not any(hasattr(MainWindow, n) for n in ("approve_all", "revoke_all", "refresh_programs", "remove_trusted"))
    old = AppSettings().model_dump()
    old["security"] |= {"program_approvals": [{"program_id": "*", "caption": "Alle", "approved_at": "x",
                                               "expires_at": "y"}], "program_assignments": {"c:\\a.accdb": "*"},
                        "trusted_databases": ["c:\\a.accdb"]}
    loaded = AppSettings.model_validate_json(json.dumps(old))
    assert not hasattr(loaded.security, "program_approvals") and loaded.security.block_in_exam_mode

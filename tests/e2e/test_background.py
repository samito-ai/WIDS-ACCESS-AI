"""GUI-Tests des Hintergrundmodus: nur Infobereich, kein Fenster, kein Fokuswechsel, Abbruch ohne Weiterarbeit."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QWidget

from app.agent.orchestrator import ApprovalDecision
from app.core.context import AppContext
from app.core.errors import WidsError
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.tasks.models import CapturedTask
from app.ui.main_window import MainWindow
from app.utils import sampledb
from tests.helpers import ScriptedProvider, solution

pytestmark = [pytest.mark.gui]

TASK = "Erstellen Sie die Abfrage qryHintergrund mit allen Nachnamen."


@pytest.fixture
def background(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MainWindow, ScriptedProvider, dict[str, list[Any]]]]:
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    seen: dict[str, list[Any]] = {"activated": [], "boxes": []}
    monkeypatch.setattr(QWidget, "activateWindow", lambda self: seen["activated"].append(self))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: seen["boxes"].append(a) or QMessageBox.StandardButton.No)
    ctx = AppContext(Storage(tmp_path / "b.sqlite3"), SecretStore(MemoryBackend()))
    ctx.settings.hotkeys.enabled = False
    provider = ScriptedProvider([])
    ctx.ai.set_provider_for_testing("anthropic", provider)
    ctx.pipeline._environment_probe = lambda: ([], [])
    window = MainWindow(ctx)  # wie „--background“: nichts wird angezeigt
    qtbot.addWidget(window)
    yield window, provider, seen
    window.quit_app()
    ctx.shutdown()


def visible_windows() -> list[QWidget]:
    """Sichtbare Fenster – ohne die Statusanzeige (kein Taskleisteneintrag, nimmt weder Fokus noch Klicks)."""
    return [w for w in QApplication.topLevelWidgets()
            if w.isVisible() and w.isWindow() and w.objectName() != "statusOverlay"]


def test_overlay_never_takes_focus_or_clicks(background: Any) -> None:
    window, _, _ = background
    overlay = window.overlay
    flags = overlay.windowFlags()
    for flag in (Qt.WindowType.WindowTransparentForInput, Qt.WindowType.WindowDoesNotAcceptFocus,
                 Qt.WindowType.Tool, Qt.WindowType.WindowStaysOnTopHint):
        assert flags & flag
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    overlay.show_state("working")
    assert overlay.isVisible() and overlay.width() <= 2 and overlay.height() <= 2, "höchstens 2 × 2 Pixel"
    assert (overlay.x(), overlay.y()) == (0, 0) or overlay.pos().x() < 5, "oben links"
    assert overlay.findChildren(QWidget) == [], "kein Text, keine weiteren Elemente"
    assert overlay._color.name().lower() == "#ffd400", "gelb = arbeitet"
    assert not overlay._hide_timer.isActive(), "gelb bleibt stehen, solange gearbeitet wird"
    overlay.show_state("done")
    assert overlay._color.name().lower() == "#00c853" and overlay._hide_timer.interval() == 3000
    assert overlay._hide_timer.isActive(), "nach Abschluss nach 3 Sekunden ausblenden"
    overlay.show_state("error")
    assert overlay._color.name().lower() == "#ff1744"
    overlay.show_state("unbekannt")
    assert overlay.state == "" and not overlay.isVisible()


def test_starts_in_tray_without_any_window(background: Any) -> None:
    window, _, _ = background
    assert window.tray is not None and window.tray.isVisible()
    assert window.mode() == "background"
    assert visible_windows() == [], "kein Fenster und damit kein Taskleisteneintrag"
    assert window.tray_state == "idle" and "bereit" in window.tray.toolTip()


@pytest.mark.access
def test_alt_w_solves_exercise_without_window_or_focus_change(background: Any, qtbot: Any, tmp_path: Path) -> None:
    """Alt+W (bewusste Benutzeraktion, keine weitere Freigabe) bearbeitet die Übung vollständig: kein Dialog, kein
    Fenster, kein Fokuswechsel; Statuspunkt gelb → grün."""
    window, provider, seen = background
    db = tmp_path / "hintergrund.accdb"
    window.ctx.access.create_database(str(db), sampledb.populate)
    window.ctx.access.disconnect()
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryHintergrund",
                                                    "sql": "SELECT Nachname FROM Kunden"}}]))
    approvals: list[Any] = []
    window._approval_dialog = lambda request: approvals.append(request) or ApprovalDecision()
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text=TASK, method="tecomp", source="tecomptraining.exe", db_path=str(db))
    states: list[str] = []
    window.bridge.pipeline_event.connect(lambda _: states.append(window.tray_state))
    window._on_hotkey("solve")  # = Alt+W
    assert window.tray_state == "running"
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None and window.tray_state != "running", timeout=60000)
    assert window.tray_state == "ok", window.task_status.text()
    assert window.overlay.state == "done", "„Done“ nach geprüfter Ausführung"
    assert "running" in states, "während der Arbeit zeigt das Symbol „läuft“"
    assert approvals == [], "keine Routinebestätigung – Alt+W genügt"
    assert visible_windows() == [] and seen["activated"] == [] and seen["boxes"] == []
    assert window.ctx.access.run(lambda e: e.queries.exists("qryHintergrund"))
    record = window.ctx.storage.list_tasks(limit=1)[0]
    assert record.status == "erfolgreich" and record.backup_path and Path(record.backup_path).exists()


@pytest.mark.access
def test_review_when_requirements_not_confirmed_and_destructive_stops(background: Any, qtbot: Any, tmp_path: Path) -> None:
    """Nicht bestätigte Anforderung → „Review“ (rot), nie „Done“. Löschender Plan im Hintergrund → angehalten (rot),
    kein Dialog, nichts gelöscht."""
    window, provider, seen = background
    db = tmp_path / "eigene_uebung.accdb"
    window.ctx.access.create_database(str(db), sampledb.populate)
    window.ctx.access.disconnect()
    approvals: list[Any] = []
    asked: list[Any] = []
    window._approval_dialog = lambda request: approvals.append(request) or ApprovalDecision()
    window._exec_dialog = lambda dialog: asked.append(dialog) or QMessageBox.StandardButton.No
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text=TASK, method="tecomp", source="tecomptraining.exe", db_path=str(db))
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryEins", "sql": "SELECT Nachname FROM Kunden"}}],
                                       requirements=["Abfrage qryEins", "Feld Telefon anzeigen"]))
    provider.requirement_responses.append({"unmet": {2: "Kein Feld Telefon."}})
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None and window.tray_state != "running", timeout=60000)
    assert window.tray_state == "review" and window.overlay.state == "error", "nicht bestätigt = rot, nie grün"

    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryZwei", "sql": "SELECT Ort FROM Kunden"}}]))
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None and window.tray_state != "running", timeout=60000)
    assert window.tray_state == "ok" and window.overlay.state == "done", "gemerkte Übungsdatenbank: ohne Freigabe"
    record = window.ctx.storage.list_tasks(limit=1)[0]
    assert record.backup_path and Path(record.backup_path).exists(), "Sicherung trotzdem erstellt"

    provider.responses.append(solution([{"action": {"action": "delete_records", "table": "Kunden"}}],
                                       task_type="data_editing"))
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: not window._busy and window._prepared is None and window.tray_state != "running", timeout=60000)
    record = window.ctx.storage.list_tasks(limit=1)[0]
    assert record.status == "blockiert" and "löschende Schritte" in (record.error or "")
    assert window.overlay.state == "error" and window.ctx.access.run(lambda e: e.queries.exists("qryZwei")), \
        "löschender Schritt im Hintergrund nie automatisch → nichts gelöscht"
    assert approvals == [] and asked == [] and seen["boxes"] == [] and visible_windows() == [], "kein einziger Dialog"


@pytest.mark.access
def test_tecomp_task_without_open_database_never_uses_previous_exercise(background: Any, qtbot: Any, tmp_path: Path) -> None:
    """Workshop/neue Übung in te.comp, aber keine Datenbank geöffnet: die Verbindung zur FRÜHEREN Übung wird getrennt."""
    window, provider, _ = background
    old = tmp_path / "fruehere_uebung.accdb"
    window.ctx.access.create_database(str(old), sampledb.populate)
    assert window.ctx.access.is_connected
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryFalsch", "sql": "SELECT 1"}}]))
    window._approval_dialog = lambda request: pytest.fail("ohne Datenbank darf nichts zur Freigabe kommen")
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text="CD-Sammlung: Importieren Sie die Excel-Tabelle CDs.xls.", method="ocr", source="tecomptraining.exe")
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: not window._busy and window.tray_state != "running", timeout=60000)
    assert not window.ctx.access.is_connected, "keine Verbindung zur früheren Übung mehr"
    assert window.tray_state == "error" and window.overlay.state == "error"


def test_task_page_images_reach_the_analysis(background: Any, qtbot: Any) -> None:
    window, provider, _ = background
    provider.responses.append(solution([]))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text="Erstellen Sie ein Formular wie in der Abbildung.", method="tecomp", source="tecomptraining.exe", images=[png])
    window._on_hotkey("analyze")
    qtbot.waitUntil(lambda: not window._busy and bool(provider.requests), timeout=30000)
    first = provider.requests[0].messages[0]
    assert [img.data for img in first.images] == [png] and "ABBILDUNGEN" in first.content


def test_settings_and_dashboard_hotkeys_open_main_window(background: Any) -> None:
    window, _, _ = background
    window._on_hotkey("settings")
    assert window.isVisible() and window.stack.currentIndex() == 5
    window.hide()
    window._on_hotkey("dashboard")
    assert window.isVisible() and window.stack.currentIndex() == 0
    window.hide()


def test_cancel_during_analysis_never_executes(background: Any, qtbot: Any) -> None:
    window, provider, _ = background
    provider.responses.append(solution([{"action": {"action": "create_query", "name": "qryNie", "sql": "SELECT 1"}}]))
    original = provider.complete

    def complete_and_cancel(request: Any) -> Any:
        window._cancel.set()  # Abbruch (Esc), während die KI noch plant
        return original(request)

    provider.complete = complete_and_cancel  # type: ignore[method-assign]
    window._approval_dialog = lambda request: pytest.fail("nach einem Abbruch darf nichts mehr zur Freigabe kommen")
    window.ctx.reader.from_window = lambda: CapturedTask(  # type: ignore[method-assign]
        text=TASK, method="tecomp", source="tecomptraining.exe")
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: "Abgebrochen" in window.task_status.text(), timeout=30000)
    assert not window._busy and window.tray_state == "idle"


def test_cancel_during_capture_sends_nothing(background: Any, qtbot: Any) -> None:
    window, provider, _ = background

    def capture() -> CapturedTask:
        window._cancel.set()
        return CapturedTask(text=TASK, method="tecomp", source="tecomptraining.exe")

    window.ctx.reader.from_window = capture  # type: ignore[method-assign]
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: "Abgebrochen" in window.task_status.text(), timeout=10000)
    assert provider.requests == [], "nach dem Abbruch keine KI-Anfrage mehr"


def test_error_is_shown_in_tray_and_status_without_dialog(background: Any, qtbot: Any) -> None:
    window, _, seen = background

    def fail() -> CapturedTask:
        raise WidsError("Kein te.comp-Fenster gefunden.")

    window.ctx.reader.from_window = fail  # type: ignore[method-assign]
    window._on_hotkey("solve")
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert window.tray_state == "error" and "te.comp" in window.tray.toolTip()
    assert window.overlay.state == "error"
    assert seen["boxes"] == [] and visible_windows() == []
    log = [window.compact.log.item(i).text() for i in range(window.compact.log.count())]
    assert any("te.comp" in line for line in log), "im Statusfenster abrufbar"


def test_closing_window_keeps_running_and_quit_ends(background: Any) -> None:
    window, _, _ = background
    window.bring_to_front(0, full=True)  # Tray-Menü „Dashboard“
    assert window.isVisible()
    window.close()
    assert not window.isVisible() and window.tray.isVisible(), "Schließen = weiter im Hintergrund"
    assert window._timer.isActive()
    window.quit_app()
    assert not window.tray.isVisible() and not window._timer.isActive()


def test_second_start_command_shows_status(background: Any) -> None:
    window, _, _ = background
    window.handle_command("show")
    assert window.compact.isVisible() and not window.isVisible()
    window.compact.hide()

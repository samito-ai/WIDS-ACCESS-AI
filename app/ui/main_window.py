"""Hauptfenster mit Dashboard, Aufgabe, Assistent, Explorer, Verlauf und Einstellungen."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, __version__
from app.agent.explainer import STATUS_LABELS, analysis_markdown, plan_markdown
from app.agent.orchestrator import ApprovalDecision, ApprovalRequest, PipelineEvent, PreparedTask, TaskOutcome
from app.ai.base import ChatMessage
from app.ai.manager import PROVIDER_LABELS, ConnectionCheck
from app.automation.hotkeys import HotkeyManager, parse
from app.core import autostart
from app.core.context import AppContext
from app.core.errors import HotkeyError, TaskCancelledError, WidsError
from app.core.logging_setup import get_logger
from app.tasks.models import TASK_TYPE_LABELS, CapturedTask, PlanStep, TaskType
from app.ui import theme
from app.ui.compact import CompactWindow, app_icon
from app.ui.overlay import StatusOverlay
from app.vision.capture import TECOMP_PROCESS

log = get_logger(__name__)
PAGES = ["Dashboard", "Aufgabe", "KI-Assistent", "Datenbank", "Verlauf", "Einstellungen"]
HOTKEY_LABELS = {"solve": "Aufgabe lösen", "analyze": "Aufgabe analysieren", "ask": "KI fragen",
                 "status": "Statusfenster", "settings": "Einstellungen öffnen", "dashboard": "Dashboard öffnen",
                 "cancel": "Automatisierung abbrechen"}


# --------------------------------------------------------------------------- Hintergrundarbeit
class _JobSignals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, func: Callable[[], Any], signals: _JobSignals) -> None:
        super().__init__()
        self._func, self._signals = func, signals

    def run(self) -> None:
        try:
            self._signals.done.emit(self._func())
        except Exception as exc:
            self._signals.failed.emit(exc)


class Bridge(QObject):
    """Rückfragen der Pipeline (Arbeits-Thread) an den Benutzer (GUI-Thread)."""

    ask = Signal(str, object, object)
    pipeline_event = Signal(object)  # nicht "event": das würde QObject.event() verdecken
    hotkey = Signal(str)
    #: True nur bei geöffnetem Dashboard (vom GUI-Thread gesetzt) – sonst öffnet WIDS keinen Dialog von selbst
    interactive = False

    def can_ask(self) -> bool:
        return self.interactive

    def _ask(self, kind: str, payload: Any) -> Any:
        holder: dict[str, Any] = {}
        self.ask.emit(kind, payload, holder)  # blockiert, bis der Dialog geschlossen ist
        return holder["result"]

    def approve_plan(self, request: ApprovalRequest) -> ApprovalDecision:
        return self._ask("approve", request)

    def confirm_step(self, step: PlanStep, reason: str) -> bool:
        return bool(self._ask("confirm", (step, reason)))

    def resolve_conflict(self, step: PlanStep, message: str, suggestion: str) -> Any:
        return self._ask("conflict", (step, message, suggestion))

    def on_event(self, event: PipelineEvent) -> None:
        self.pipeline_event.emit(event)


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName="card")
    layout = QVBoxLayout(frame)
    label = QLabel(title, objectName="muted")
    layout.addWidget(label)
    return frame, layout


def button(text: str, slot: Callable[[], Any], name: str = "") -> QPushButton:
    btn = QPushButton(text, objectName=name)
    btn.clicked.connect(slot)
    return btn


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(ctx.settings.ui.window_width, ctx.settings.ui.window_height)
        self._jobs: set[_JobSignals] = set()
        self._cancel = threading.Event()
        self._busy = False
        self._prepared: PreparedTask | None = None
        self._chat: list[ChatMessage] = []
        self._chat_image: bytes | None = None
        self._hotkey_errors: dict[str, str] = {}
        self.tray: QSystemTrayIcon | None = None  # wird nach dem Seitenaufbau angelegt
        self.overlay = StatusOverlay()
        self.destroyed.connect(self.overlay.deleteLater)  # eigenes Fenster ohne Eltern
        self.tray_state = "idle"
        self._quitting = False

        self.bridge = Bridge(self)
        self.bridge.ask.connect(self._on_ask, Qt.ConnectionType.BlockingQueuedConnection)
        self.bridge.pipeline_event.connect(self._on_event)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.hotkeys = HotkeyManager(self.bridge.hotkey.emit)

        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget(objectName="nav")
        self.nav.setFixedWidth(190)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.addItems(PAGES)
        self.stack = QStackedWidget()
        for builder in (self._build_dashboard, self._build_task, self._build_chat, self._build_explorer,
                        self._build_history, self._build_settings):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(22, 18, 22, 18)
            builder(layout)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._show_page)
        outer.addWidget(self.nav)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.setWindowIcon(app_icon())
        self.compact = CompactWindow(self)
        self.bridge.pipeline_event.connect(self.compact.add_event)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip(f"{APP_NAME} – bereit")
            menu = QMenu(self)
            menu.addAction("Status", self.compact.show_raise)
            menu.addAction("Dashboard", lambda: self.bring_to_front(0, full=True))
            menu.addAction("Einstellungen", lambda: self.bring_to_front(5, full=True))
            menu.addSeparator()
            self.tray_cancel = menu.addAction("Automatisierung abbrechen", self.cancel)
            self.tray_cancel.setEnabled(False)
            menu.addSeparator()
            menu.addAction("Beenden", self.quit_app)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.compact.show_raise()
                                        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
        self.apply_theme()
        self.nav.setCurrentRow(0)
        self.register_hotkeys()
        if self._hotkey_errors:  # auch ohne Fenster erkennbar: rotes Symbol + Meldung im Infobereich
            QTimer.singleShot(0, lambda: self.notify("Tastenkürzel-Konflikt: " + " ".join(self._hotkey_errors.values())))
        self._timer = QTimer(self, interval=5000)
        # im Hintergrund nichts abfragen (ressourcenschonend); beim Öffnen wird über _show_page aktualisiert
        self._timer.timeout.connect(lambda: self.refresh_dashboard() if self.isVisible() else None)
        self._timer.start()

    # ------------------------------------------------------------------ Infrastruktur
    def apply_theme(self) -> None:
        self.setStyleSheet(theme.stylesheet(self.ctx.settings.ui.theme))
        self.compact.setStyleSheet(theme.stylesheet(self.ctx.settings.ui.theme))

    def mode(self) -> str:
        """``window`` (Hauptfenster offen), ``compact`` (kleines Statusfenster offen) oder ``background``
        (nur das Symbol im Infobereich – es darf dann nichts von selbst aufgehen oder den Fokus nehmen)."""
        if self.isVisible():
            return "window"
        return "compact" if self.compact.isVisible() else "background"

    def _dialog_parent(self) -> QWidget:
        return self if self.mode() == "window" else self.compact

    def _exec_dialog(self, dialog: QDialog) -> int:
        """Zeigt eine notwendige Rückfrage. Ohne offenes Hauptfenster liegt sie zuoberst und mittig –
        sonst könnte sie unbemerkt hinter te.comp/Access warten und die Automatisierung blockieren."""
        if self.mode() != "window":
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            dialog.show()  # erst nach dem Anzeigen kennt Qt die endgültige Größe
            screen = QApplication.primaryScreen().availableGeometry()
            dialog.move(screen.center() - dialog.rect().center())
        return dialog.exec()

    def set_tray(self, state: str, text: str) -> None:
        """Status im Infobereich und im Overlay (idle, running, ok, review, error).

        „ok“ (→ Done) darf nur nach geprüfter, vollständig bestätigter Ausführung gesetzt werden."""
        self.tray_state = state
        if self.ctx.settings.ui.show_overlay:
            # „review“ (nicht vollständig bestätigt) ist kein Erfolg – deshalb rot, nie grün
            self.overlay.show_state({"running": "working", "ok": "done", "review": "error",
                                     "error": "error"}.get(state, ""))
        if self.tray is not None:
            colors = {"idle": "#6d7dff", "running": "#f0a020", "ok": "#3ecf8e", "review": "#f5d04a", "error": "#ff6b81"}
            self.tray.setIcon(app_icon(colors.get(state, colors["idle"])))
            self.tray.setToolTip(f"{APP_NAME} – {text or 'bereit'}"[:120])
            self.tray_cancel.setEnabled(state == "running")

    def notify(self, message: str) -> None:
        """Meldet einen Fehler: roter Statuspunkt, rotes Infobereich-Symbol (Tooltip) und Protokoll – keine
        Windows-Benachrichtigung, kein Fenster. Details stehen im Verlauf und Diagnosebericht."""
        self.task_status.setText(message)
        self.compact.log.addItem(f"⚠ {message}")
        self.set_tray("error", message)

    def _to_background(self) -> None:
        self.hide()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self.bridge.interactive = True  # Dashboard offen: notwendige Rückfragen dürfen erscheinen

    def hideEvent(self, event: Any) -> None:
        super().hideEvent(event)
        self.bridge.interactive = False

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and self.tray is not None:
            QTimer.singleShot(0, self._to_background)  # minimieren = in den Infobereich

    def handle_command(self, command: str) -> None:
        """Kommando eines zweiten Programmstarts (siehe ``SingleInstance``)."""
        if command == "quit":
            self.quit_app()
        else:
            self.compact.show_raise()

    def quit_app(self) -> None:
        self._quitting = True
        self.compact.hide()
        self.close()

    def run_async(self, func: Callable[[], Any], done: Callable[[Any], None] | None = None,
                  failed: Callable[[Exception], None] | None = None) -> None:
        signals = _JobSignals(self)
        self._jobs.add(signals)

        def finish(handler: Callable[[Any], None] | None, value: Any) -> None:
            self._jobs.discard(signals)
            signals.deleteLater()
            if handler:
                handler(value)

        signals.done.connect(lambda value: finish(done, value))
        signals.failed.connect(lambda exc: finish(failed or self.show_error, exc))
        QThreadPool.globalInstance().start(_Job(func, signals))

    def show_error(self, exc: Exception) -> None:
        if isinstance(exc, WidsError):
            log.warning("%s: %s", type(exc).__name__, exc.detail or exc.user_message)
            message = exc.user_message
        else:
            log.error("Unerwarteter Fehler", exc_info=exc)
            message = f"Unerwarteter Fehler: {type(exc).__name__}: {exc}\n\nDetails im Log: {self.ctx.log_file}"
        dashboard_open = self.mode() == "window"
        self.notify(message.splitlines()[0])
        if not dashboard_open:
            return  # ohne Dashboard nie ein Dialog – roter Punkt, Infobereich-Symbol und Verlauf genügen
        if isinstance(exc, WidsError):
            QMessageBox.warning(self._dialog_parent(), APP_NAME, message)
        else:
            QMessageBox.critical(self._dialog_parent(), APP_NAME, message)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        {0: self.refresh_dashboard, 3: self.refresh_explorer, 4: self.refresh_history}.get(index, lambda: None)()

    def bring_to_front(self, page: int, *, full: bool = False) -> None:
        """Zeigt eine Ansicht. Nur ``full=True`` (ausdrückliche Benutzeraktion) öffnet das Hauptfenster und holt es
        nach vorne. Ohne ``full`` wird höchstens im offenen Hauptfenster die Seite gewechselt – kein neues Fenster,
        kein Fokuswechsel weg von te.comp/Access (Kompaktfenster und Infobereich aktualisieren sich selbst)."""
        if not full and self.mode() != "window":
            return
        if self.nav.currentRow() == page:
            self._show_page(page)  # gleiche Seite: trotzdem aktualisieren
        self.nav.setCurrentRow(page)
        if not full:
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def set_busy(self, busy: bool, text: str = "", state: str = "idle") -> None:
        self._busy = busy
        self.set_tray("running" if busy else state, text)
        for btn in (self.btn_capture, self.btn_image, self.btn_analyze):
            btn.setEnabled(not busy)
        self.btn_run.setEnabled(not busy and self._prepared is not None and not self._prepared.solution.plan.is_empty)
        self.btn_cancel.setEnabled(busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        self.task_status.setText(text)
        if not busy and self._quitting:
            QTimer.singleShot(0, self.close)  # „Beenden“ kam während einer Automatisierung: jetzt sauber beenden

    # ------------------------------------------------------------------ Tastenkürzel
    def register_hotkeys(self) -> None:
        hk = self.ctx.settings.hotkeys
        if hasattr(self, "btn_cancel"):  # Beschriftung zeigt das tatsächlich eingestellte Abbruch-Kürzel
            self.btn_cancel.setText(f"Abbrechen ({hk.cancel})")
        if not hk.enabled:
            self.hotkeys.stop()
            self._hotkey_errors = {}
            return
        # alle Kürzel dauerhaft (einmal registriert, kein Neuregistrieren bei jedem Statuswechsel)
        self._hotkey_errors = self.hotkeys.start({name: getattr(hk, name) for name in HOTKEY_LABELS})

    def _on_hotkey(self, name: str) -> None:
        if name == "cancel":
            self.cancel()
        elif name == "settings":  # ausdrücklich angefordert – öffnet das Hauptfenster
            self.bring_to_front(5, full=True)
        elif name == "dashboard":
            self.bring_to_front(0, full=True)
        elif name == "ask":  # ausdrücklich angefordert – darf ein Fenster zeigen
            if self.mode() == "window":
                self.bring_to_front(2, full=True)
                self.chat_input.setFocus()
            else:
                self.compact.show_raise()
                self.compact.question.setFocus()
        elif name == "status":
            if self.mode() == "window":
                self.bring_to_front(0, full=True)
            else:
                self.compact.show_raise()
        elif not self._busy:
            self.capture_task(then="execute" if name == "solve" else "analyze")

    # ------------------------------------------------------------------ Dashboard
    def _build_dashboard(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("Dashboard", objectName="title"))
        grid = QGridLayout()
        self.dash: dict[str, QLabel] = {}
        for index, (key, title) in enumerate([
            ("access", "Access-Verbindung"), ("db", "Aktive Übungsdatenbank"), ("task", "Letzte erkannte Aufgabe"),
            ("ai", "KI-Anbieter und Modell"), ("auto", "Automatisierung"), ("usage", "KI-Verbrauch (Monat)"),
        ]):
            frame, box = card(title)
            self.dash[key] = QLabel("–", objectName="value", wordWrap=True)
            box.addWidget(self.dash[key])
            grid.addWidget(frame, index // 3, index % 3)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        actions.addWidget(button("Geöffnete Access-Datenbank verbinden", self.attach_running, "primary"))
        actions.addWidget(button("Datenbankdatei öffnen …", self.open_file))
        actions.addWidget(button("Beispieldatenbank erstellen", self.create_sample))
        actions.addWidget(button("Trennen", self.disconnect_db))
        actions.addStretch()
        layout.addLayout(actions)
        frame, box = card("Letzte Aufgaben")
        self.dash_tasks = QListWidget()
        self.dash_tasks.itemDoubleClicked.connect(lambda _: self.nav.setCurrentRow(4))
        box.addWidget(self.dash_tasks)
        self.dash_stats = QLabel("", objectName="muted", wordWrap=True)
        box.addWidget(self.dash_stats)
        layout.addWidget(frame, 1)

    def refresh_dashboard(self) -> None:
        ctx = self.ctx
        status = ctx.access.status()
        self.dash["access"].setText(status.message)
        self.dash["db"].setText(f"{status.name}\n{status.path}" if status.connected else "keine")
        tasks = ctx.storage.list_tasks(limit=8)
        self.dash["task"].setText(tasks[0].title or tasks[0].raw_text[:80] if tasks else "noch keine")
        provider = ctx.settings.ai.provider
        key = "Schlüssel hinterlegt" if ctx.ai.is_configured() else "kein API-Schlüssel"
        self.dash["ai"].setText(f"{PROVIDER_LABELS[provider]}\n{ctx.settings.active_model()} · {key}")
        hk = ctx.settings.hotkeys
        if not hk.enabled:
            hotkey_text = "Tastenkürzel aus"
        elif self._hotkey_errors:
            hotkey_text = "Kürzel-Konflikt: " + "; ".join(self._hotkey_errors.values())
        else:
            hotkey_text = f"{hk.solve} lösen · {hk.cancel} abbrechen · {hk.dashboard} Dashboard · {hk.settings} Einstellungen"
        self.dash["auto"].setText(("läuft …" if self._busy else "bereit") + f"\n{hotkey_text}")
        usage = ctx.ai.usage("month")
        cost = f"{usage.cost_usd:.2f} $" + (" (teils ohne Preis)" if usage.unpriced_requests else "")
        self.dash["usage"].setText(f"{usage.requests} Anfragen · {usage.input_tokens + usage.output_tokens:,} Tokens\n{cost}".replace(",", "."))
        self.dash_tasks.clear()
        for task in tasks:
            label = STATUS_LABELS.get(task.status, task.status)
            self.dash_tasks.addItem(f"{task.created_at[:16].replace('T', ' ')}  ·  {label}  ·  {task.title or task.raw_text[:70]}")
        stats = ctx.storage.task_type_statistics()[:5]
        self.dash_stats.setText("Häufigste Aufgabentypen: " + ", ".join(
            f"{TASK_TYPE_LABELS.get(TaskType(t), t) if t in TaskType._value2member_map_ else t} ({ok}/{n} erfolgreich)"
            for t, n, ok in stats) if stats else "")

    def _connected(self, _: Any = None) -> None:
        self._prepared = None
        self.refresh_dashboard()
        self.refresh_explorer()

    def attach_running(self) -> None:
        self.run_async(self.ctx.access.attach_active, self._connected)

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Access-Datenbank öffnen", "", "Access-Datenbanken (*.accdb *.mdb)")
        if not path:
            return
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                          "Wie soll die Datei geöffnet werden?\n\nEine Übungskopie lässt das Original unverändert.", parent=self)
        copy_btn = box.addButton("Übungskopie (empfohlen)", QMessageBox.ButtonRole.AcceptRole)
        original_btn = box.addButton("Original bearbeiten", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(copy_btn if self.ctx.settings.access.work_on_copy_default else original_btn)
        box.exec()
        if box.clickedButton() in (copy_btn, original_btn):
            use_copy = box.clickedButton() is copy_btn
            self.run_async(lambda: self.ctx.open_database(path, work_on_copy=use_copy), self._connected)

    def create_sample(self) -> None:
        self.run_async(self.ctx.create_sample_database, self._connected)

    def disconnect_db(self) -> None:
        self.run_async(self.ctx.access.disconnect, self._connected)

    # ------------------------------------------------------------------ Aufgabe
    def _build_task(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("Aufgabe erkennen, planen und ausführen", objectName="title"))
        self.task_text = QPlainTextEdit(placeholderText="Aufgabenstellung hier einfügen – oder vom Bildschirm erfassen …")
        self.task_text.setMaximumHeight(150)
        layout.addWidget(self.task_text)
        row = QHBoxLayout()
        self.btn_capture = button("Vom Bildschirm erfassen", lambda: self.capture_task(then="none"))
        self.btn_image = button("Bild importieren …", self.import_image)
        self.btn_analyze = button("Analysieren und planen", lambda: self.analyze(execute=False), "primary")
        self.btn_run = button("Plan ausführen …", self.execute, "primary")
        self.btn_cancel = button(f"Abbrechen ({self.ctx.settings.hotkeys.cancel})", self.cancel, "danger")
        for btn in (self.btn_capture, self.btn_image, self.btn_analyze, self.btn_run, self.btn_cancel):
            row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)
        self.progress = QProgressBar(textVisible=False)
        layout.addWidget(self.progress)
        self.task_status = QLabel("", objectName="muted", wordWrap=True)
        layout.addWidget(self.task_status)
        split = QSplitter()
        self.task_view = QTextBrowser()
        self.task_log = QListWidget()
        split.addWidget(self.task_view)
        split.addWidget(self.task_log)
        split.setSizes([700, 300])
        layout.addWidget(split, 1)
        self.set_busy(False)

    def capture_task(self, then: str) -> None:
        """Erfasst die Aufgabe (Alt+W / Alt+T). Im Hintergrundmodus öffnet sich dabei kein Fenster."""
        if self._busy:
            return
        self._cancel = threading.Event()  # gilt für die ganze Kette Erfassen → Analysieren → Ausführen
        self.set_busy(True, "Aufgabe wird gelesen …")

        def done(task: CapturedTask) -> None:
            self._captured = task
            self.task_text.setPlainText(task.text)
            self.bring_to_front(1)
            hint = " ".join(task.warnings)
            how = {"tecomp": "aus der te.comp-Aufgabenseite", "ocr": f"per OCR aus „{task.source}“"}.get(
                task.method, f"per KI-Bilderkennung aus „{task.source}“")
            self.set_busy(False, f"Aufgabe {how} erfasst. {hint}")
            if self._cancel.is_set():
                self._cancelled()
            elif then == "none":
                return
            elif task.source.lower() != TECOMP_PROCESS:
                # Fremdes Fenster: nichts automatisch an die KI senden – erst prüfen lassen
                self.notify(f"te.comp wurde nicht gefunden – gelesen wurde das Fenster „{task.source}“. "
                            "Bitte den Text in der Ansicht „Aufgabe“ prüfen und dann „Analysieren und planen“ klicken.")
            else:
                self.analyze(execute=then == "execute", task=task, fresh=False)

        def failed(exc: Exception) -> None:
            self.set_busy(False)
            self.bring_to_front(1)
            self.show_error(exc)

        self.run_async(self.ctx.reader.from_window, done, failed)

    def _cancelled(self) -> None:
        """Nach einem Abbruch wird nichts mehr gestartet – auch kein bereits geplanter Folgeschritt."""
        self.set_busy(False, "Abgebrochen – es wurde nichts weiter ausgeführt.")
        self.compact.log.addItem("■ Abgebrochen")

    def import_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Bild mit Aufgabenstellung", "", "Bilder (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        self.set_busy(True, "Bild wird gelesen …")

        def done(task: CapturedTask) -> None:
            self._captured = task
            self.task_text.setPlainText(task.text)
            self.set_busy(False, "Text aus dem Bild erkannt – bitte kurz prüfen.")

        self.run_async(lambda: self.ctx.reader.from_image(path), done, self._task_failed)

    def _task_failed(self, exc: Exception) -> None:
        if isinstance(exc, TaskCancelledError):
            self._cancelled()
            return
        self.set_busy(False)
        self.show_error(exc)

    def analyze(self, execute: bool, task: CapturedTask | None = None, *, fresh: bool = True) -> None:
        if self._busy:
            return
        text = self.task_text.toPlainText().strip()
        captured = task or getattr(self, "_captured", None)
        if captured is None or captured.text.strip() != text:
            # der Benutzer hat den Text geändert – die zugehörige Datenbank bleibt dieselbe
            captured = CapturedTask(text=text, method="manual", db_path=captured.db_path if captured else "")
        if not text:
            self.show_error(WidsError("Bitte zuerst eine Aufgabenstellung eingeben oder erfassen."))
            return
        if fresh:
            self._cancel = threading.Event()
        self._prepared = None
        self.task_log.clear()
        self.set_busy(True, "Die KI analysiert die Aufgabe und erstellt einen Plan …")

        def done(prepared: PreparedTask) -> None:
            self._prepared = prepared
            self._render_prepared(prepared)
            answered = prepared.solution.plan.is_empty
            self.set_busy(False, "Beantwortet – keine Änderungen an der Datenbank nötig." if answered
                          else "Plan erstellt. Mit „Plan ausführen“ freigeben.")
            if self._cancel.is_set():  # Abbruch während der Analyse: der fertige Plan wird NICHT ausgeführt
                self._cancelled()
            elif execute and not answered:
                self.execute(fresh=False)

        def work() -> PreparedTask:
            # te.comp-Übung: automatisch mit dem Access verbinden, in dem te.comp die Übung geöffnet hat
            if captured.db_path and (self.ctx.access.path.lower() != captured.db_path.lower()
                                     or not self.ctx.access.is_alive()):
                self.ctx.access.connect(captured.db_path, prefer_running=True)
            elif not captured.db_path and captured.source.lower() == TECOMP_PROCESS and self.ctx.access.is_connected:
                # te.comp-Aufgabe ohne geöffnete Datenbank: die Verbindung zu einer FRÜHEREN Übung darf nicht
                # verwendet werden – sonst würde die falsche Datei geändert
                self.ctx.access.disconnect()
            return self.ctx.pipeline.prepare(captured, cancel=self._cancel)

        self.run_async(work, done, self._task_failed)

    def _render_prepared(self, prepared: PreparedTask, outcome: TaskOutcome | None = None) -> None:
        if outcome is not None:
            self.task_view.setMarkdown(outcome.report_md)
            return
        parts = [f"# {prepared.solution.analysis.title or 'Aufgabe'}", ""]
        parts += [f"> ⚠️ {w}" for w in prepared.warnings]
        parts += ["", analysis_markdown(prepared.solution.analysis), "", "## Plan", "",
                  plan_markdown(prepared.solution.plan, prepared.validation)]
        self.task_view.setMarkdown("\n".join(parts))

    def execute(self, *, fresh: bool = True) -> None:
        prepared = self._prepared
        if self._busy or prepared is None or prepared.solution.plan.is_empty:
            return
        if fresh:
            self._cancel = threading.Event()
        self.set_busy(True, "Warte auf Freigabe …")
        self.progress.setRange(0, len(prepared.solution.plan.steps))
        self.progress.setValue(0)

        def done(outcome: TaskOutcome) -> None:
            self._prepared = None
            self._render_prepared(prepared, outcome)
            text = STATUS_LABELS.get(outcome.status, outcome.status) + (f" – {outcome.error}" if outcome.error else "")
            # „Done“ nur nach ausgeführter UND vollständig bestätigter Lösung; alles andere ist erkennbar anders
            if outcome.status == "erfolgreich":
                self.set_busy(False, text, state="ok")
            elif outcome.status in ("beantwortet", "abgebrochen", "abgelehnt"):
                self.set_busy(False, text)
            elif outcome.status in ("unklar", "ausgeführt", "teilweise"):
                self.set_busy(False, text, state="review")
                self.compact.log.addItem(f"⚠ {text.splitlines()[0]}")
            else:  # fehlgeschlagen/blockiert
                self.set_busy(False, text)
                self.notify(text.splitlines()[0])
            self.refresh_dashboard()

        self.run_async(lambda: self.ctx.pipeline.execute(prepared, self.bridge, cancel=self._cancel), done, self._task_failed)

    def cancel(self) -> None:
        if self._busy:
            self._cancel.set()
            self.task_status.setText("Abbruch angefordert – der laufende Einzelschritt wird noch sauber beendet …")

    def _on_event(self, event: PipelineEvent) -> None:
        icons = {"step_started": "▶", "step_done": "✓", "step_failed": "✗", "step_skipped": "⏭", "verification": "🔍",
                 "backup": "💾", "log": "•", "status": "■"}
        prefix = f"Schritt {event.step_id}: " if event.step_id else ""
        self.task_log.addItem(f"{icons.get(event.kind, '•')} {prefix}{event.message}")
        self.task_log.scrollToBottom()
        if event.kind == "step_started":
            self.task_status.setText(f"{prefix}{event.message}")
        if event.kind in ("step_done", "step_failed", "step_skipped"):
            self.progress.setValue(self.progress.value() + 1)

    def _on_ask(self, kind: str, payload: Any, holder: dict[str, Any]) -> None:
        """Rückfragen der Pipeline (Freigabe, Löschbestätigung, Namenskonflikt) – nur bei geöffnetem Dashboard, nie
        automatisch bejaht. Ohne Dashboard (Hintergrund) gilt jede Rückfrage als NICHT bestätigt: die Pipeline hält an."""
        if self.mode() != "window":
            holder["result"] = {"approve": ApprovalDecision(), "confirm": False}.get(kind, "skip")
            return
        try:
            if kind == "approve":
                holder["result"] = self._approval_dialog(payload)
            elif kind == "confirm":
                step, reason = payload
                ask = QMessageBox(QMessageBox.Icon.Warning, "Zusätzliche Bestätigung",
                                  f"Schritt {step.id}: {step.title}\n\n{reason}\n\nWirklich ausführen?",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self._dialog_parent())
                ask.setDefaultButton(QMessageBox.StandardButton.No)
                holder["result"] = self._exec_dialog(ask) == QMessageBox.StandardButton.Yes
            else:
                step, message, suggestion = payload
                box = QMessageBox(QMessageBox.Icon.Question, "Namenskonflikt", f"Schritt {step.id}: {message}",
                                  parent=self._dialog_parent())
                choices: dict[Any, str] = {box.addButton("Überspringen", QMessageBox.ButtonRole.AcceptRole): "skip",
                           box.addButton("Ersetzen", QMessageBox.ButtonRole.DestructiveRole): "overwrite",
                           box.addButton("Alles abbrechen", QMessageBox.ButtonRole.RejectRole): "abort"}
                if suggestion:
                    choices[box.addButton(f"Als „{suggestion}“ anlegen", QMessageBox.ButtonRole.ActionRole)] = "rename"
                self._exec_dialog(box)
                holder["result"] = choices.get(box.clickedButton(), "skip")
        except Exception:
            log.exception("Fehler im Rückfragedialog")
            holder["result"] = {"approve": ApprovalDecision(), "confirm": False}.get(kind, "abort")

    def _approval_dialog(self, request: ApprovalRequest) -> ApprovalDecision:
        dialog = QDialog(self._dialog_parent())
        dialog.setWindowTitle("Plan freigeben")
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        kind = "Übungskopie" if request.is_working_copy else "ORIGINALDATEI"
        where = "in der laufenden Access-Instanz geöffnet" if request.attached else "direkt geöffnet"
        layout.addWidget(QLabel(f"<b>Ziel:</b> {request.db_name} ({kind}, {where})<br><span>{request.db_path}</span>", wordWrap=True))
        view = QTextBrowser()
        notes = [f"> ⚠️ {w}" for w in request.warnings]
        view.setMarkdown("\n".join([*notes, "", plan_markdown(request.solution.plan, request.validation)]))
        layout.addWidget(view, 1)
        layout.addWidget(QLabel("Vor der Ausführung wird automatisch eine Sicherungskopie erstellt. "
                                "Löschende Schritte werden einzeln nachgefragt.", objectName="muted", wordWrap=True))
        practice = QCheckBox("Ich bestätige: Das ist eine Übungsdatenbank und keine benotete Prüfung.")
        layout.addWidget(practice)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Freigeben und ausführen", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setObjectName("primary")
        ok.setEnabled(practice.isChecked())
        practice.toggled.connect(ok.setEnabled)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        approved = self._exec_dialog(dialog) == QDialog.DialogCode.Accepted
        return ApprovalDecision(approved=approved, practice_confirmed=practice.isChecked())

    # ------------------------------------------------------------------ KI-Assistent
    def _build_chat(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("KI-Assistent für Access und Datenbanktheorie", objectName="title"))
        self.chat_view = QTextBrowser()
        layout.addWidget(self.chat_view, 1)
        self.chat_input = QPlainTextEdit(placeholderText="Frage stellen (auch Multiple Choice, SQL, Fehlermeldungen) …")
        self.chat_input.setMaximumHeight(90)
        layout.addWidget(self.chat_input)
        row = QHBoxLayout()
        self.chat_send = button("Fragen", self.send_chat, "primary")
        self.chat_shot = QCheckBox("Bildschirmfoto des Aufgabenfensters mitsenden")
        row.addWidget(self.chat_send)
        row.addWidget(self.chat_shot)
        row.addStretch()
        row.addWidget(button("Verlauf leeren", self.clear_chat))
        layout.addLayout(row)
        for message in self.ctx.storage.list_chat_messages(40):
            self._chat.append(ChatMessage(role=message["role"], content=message["content"]))
        self._render_chat()

    def _render_chat(self, pending: str = "") -> None:
        parts = []
        for message in self._chat:
            who = "**Du**" if message.role == "user" else "**WIDS**"
            parts.append(f"{who}\n\n{message.content}\n\n---\n")
        if pending:
            parts.append(f"**Du**\n\n{pending}\n\n---\n\n_WIDS denkt nach …_")
        self.chat_view.setMarkdown("\n".join(parts) or "_Noch keine Fragen gestellt._")
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def send_chat(self) -> None:
        question = self.chat_input.toPlainText().strip()
        if not question:
            return
        with_shot = self.chat_shot.isChecked()
        history = list(self._chat)
        self.chat_send.setEnabled(False)
        self.chat_input.clear()
        self._render_chat(question)

        def work() -> str:
            image = None
            if with_shot:
                import os

                from app.vision import capture

                image = capture.grab(max(capture.find_task_windows(own_pid=os.getpid()),
                                         key=lambda w: (w.rect[2] - w.rect[0]) * (w.rect[3] - w.rect[1])))
            return self.ctx.ask(question, history, image)

        def done(answer: str) -> None:
            self._chat += [ChatMessage(role="user", content=question), ChatMessage(role="assistant", content=answer)]
            self.chat_send.setEnabled(True)
            self._render_chat()

        def failed(exc: Exception) -> None:
            self.chat_send.setEnabled(True)
            self.chat_input.setPlainText(question)
            self._render_chat()
            self.show_error(exc)

        self.run_async(work, done, failed)

    def clear_chat(self) -> None:
        self._chat.clear()
        self.ctx.storage.clear_chat()
        self._render_chat()

    # ------------------------------------------------------------------ Datenbank-Explorer
    def _build_explorer(self, layout: QVBoxLayout) -> None:
        head = QHBoxLayout()
        head.addWidget(QLabel("Datenbank-Explorer", objectName="title"))
        head.addStretch()
        head.addWidget(button("Aktualisieren", self.refresh_explorer))
        layout.addLayout(head)
        self.explorer_info = QLabel("", objectName="muted", wordWrap=True)
        layout.addWidget(self.explorer_info)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Objekt", "Details"])
        self.tree.setColumnWidth(0, 260)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.table = QTableWidget()
        split.addWidget(self.tree)
        split.addWidget(self.table)
        split.setSizes([520, 480])
        layout.addWidget(split, 1)

    def refresh_explorer(self) -> None:
        if not self.ctx.access.is_connected:
            self.tree.clear()
            self.table.clear()
            self.explorer_info.setText("Keine Datenbank verbunden.")
            return
        self.run_async(lambda: self.ctx.access.schema(refresh=True), self._fill_tree,
                       lambda exc: self.explorer_info.setText(str(exc)))

    def _fill_tree(self, schema: Any) -> None:
        self.tree.clear()
        self.explorer_info.setText(f"{schema.path} · {schema.file_format} · " + ", ".join(f"{k}: {v}" for k, v in schema.properties.items()))

        def group(title: str, count: int) -> QTreeWidgetItem:
            return QTreeWidgetItem(self.tree, [f"{title} ({count})"])

        tables = group("Tabellen", len(schema.tables))
        for table in schema.tables:
            node = QTreeWidgetItem(tables, [table.name, f"{table.record_count} Datensätze" + (" · verknüpft" if table.linked else "")])
            node.setData(0, Qt.ItemDataRole.UserRole, table.name)
            for fld in table.fields:
                flags = [f for f, on in (("Primärschlüssel", fld.primary_key), ("Pflichtfeld", fld.required),
                                         (f"Index: {fld.indexed}", fld.indexed != "no" and not fld.primary_key),
                                         (f"Standard: {fld.default_value}", bool(fld.default_value)),
                                         (f"Regel: {fld.validation_rule}", bool(fld.validation_rule))) if on]
                QTreeWidgetItem(node, [fld.name, " · ".join([fld.type_label, *flags])])
            for index in table.indexes:
                kind = "Primär" if index.primary else ("Eindeutig" if index.unique else "Index")
                QTreeWidgetItem(node, [f"⚿ {index.name}", f"{kind}: {', '.join(index.fields)}"])
        relations = group("Beziehungen", len(schema.relations))
        for rel in schema.relations:
            pairs = ", ".join(f"{a} = {b}" for a, b in rel.fields)
            detail = [rel.relation_type, "ref. Integrität" if rel.enforce_integrity else "ohne ref. Integrität"]
            detail += [t for t, on in (("Aktualisierungsweitergabe", rel.cascade_update), ("Löschweitergabe", rel.cascade_delete)) if on]
            QTreeWidgetItem(relations, [f"{rel.table} → {rel.foreign_table}", f"{pairs} · {' · '.join(detail)}"])
        queries = group("Abfragen", len(schema.queries))
        for query in schema.queries:
            node = QTreeWidgetItem(queries, [query.name, query.type_name])
            node.setToolTip(0, query.sql)
            if not query.is_action and not query.parameters:
                node.setData(0, Qt.ItemDataRole.UserRole, query.name)
            QTreeWidgetItem(node, ["SQL", " ".join(query.sql.split())])
        for title, items in (("Formulare", schema.forms), ("Berichte", schema.reports)):
            node = group(title, len(items))
            for item in items:
                QTreeWidgetItem(node, [item.name, item.record_source])
        tables.setExpanded(True)

    def _tree_clicked(self, item: QTreeWidgetItem) -> None:
        source = item.data(0, Qt.ItemDataRole.UserRole)
        if not source:
            return

        def fill(result: tuple[list[str], list[list[Any]]]) -> None:
            columns, rows = result
            self.table.clear()
            self.table.setColumnCount(len(columns))
            self.table.setRowCount(len(rows))
            self.table.setHorizontalHeaderLabels(columns)
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    self.table.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))

        self.run_async(lambda: self.ctx.access.run(lambda e: e.tables.read_rows(source, limit=100)), fill,
                       lambda exc: self.explorer_info.setText(str(exc)))

    # ------------------------------------------------------------------ Verlauf
    def _build_history(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("Verlauf", objectName="title"))
        self.history_search = QLineEdit(placeholderText="Verlauf durchsuchen …")
        self.history_search.textChanged.connect(self.refresh_history)
        layout.addWidget(self.history_search)
        split = QSplitter()
        self.history_list = QListWidget()
        self.history_list.currentItemChanged.connect(self._history_selected)
        self.history_view = QTextBrowser()
        split.addWidget(self.history_list)
        split.addWidget(self.history_view)
        split.setSizes([340, 660])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        row.addWidget(button("Bericht exportieren …", self.export_history))
        row.addWidget(button("Sicherung dieser Aufgabe wiederherstellen …", self.restore_history_backup))
        row.addStretch()
        row.addWidget(button("Eintrag löschen", self.delete_history, "danger"))
        layout.addLayout(row)

    def refresh_history(self) -> None:
        self.history_list.clear()
        for task in self.ctx.storage.list_tasks(search=self.history_search.text().strip()):
            item = QListWidgetItem(f"{task.created_at[:16].replace('T', ' ')} · {STATUS_LABELS.get(task.status, task.status)}\n{task.title or task.raw_text[:60]}")
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            self.history_list.addItem(item)

    def _selected_task(self) -> Any:
        item = self.history_list.currentItem()
        return self.ctx.storage.get_task(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _history_selected(self) -> None:
        task = self._selected_task()
        if task is None:
            self.history_view.clear()
            return
        fallback = f"# {task.title or 'Aufgabe'}\n\n{task.raw_text}\n\n" + (f"> ⛔ {task.error}" if task.error else "")
        self.history_view.setMarkdown(task.report_md or fallback)

    def export_history(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Bericht exportieren", f"WIDS-Aufgabe-{task.id}.md", "Markdown (*.md)")
        if path:
            Path(path).write_text(task.report_md or task.raw_text, encoding="utf-8")

    def restore_history_backup(self) -> None:
        task = self._selected_task()
        if task is None or not task.backup_path:
            self.show_error(WidsError("Zu diesem Eintrag gibt es keine Sicherung."))
            return
        answer = QMessageBox.question(
            self, "Sicherung wiederherstellen",
            f"Die Datenbank\n{task.db_path}\nwird auf den Stand VOR dieser Aufgabe zurückgesetzt. Der jetzige Stand wird "
            "vorher selbst gesichert.\n\nIst die Datei noch in Access geöffnet, bitte Access zuerst schließen.\n\nFortfahren?")
        if answer == QMessageBox.StandardButton.Yes:
            def restored(_: Any) -> None:
                QMessageBox.information(self, APP_NAME, "Sicherung wiederhergestellt.")
                self._connected()

            self.run_async(lambda: self.ctx.restore_backup(task.backup_path), restored)

    def delete_history(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.ctx.storage.delete_task(task.id)
            self.refresh_history()

    # ------------------------------------------------------------------ Einstellungen
    def _build_settings(self, layout: QVBoxLayout) -> None:
        s = self.ctx.settings
        layout.addWidget(QLabel("Einstellungen", objectName="title"))
        columns = QHBoxLayout()
        frame, box = card("KI-Anbieter")
        form = QFormLayout()
        self.set_provider = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self.set_provider.addItem(label, key)
        self.set_provider.setCurrentIndex(self.set_provider.findData(s.ai.provider))
        self.set_model_anthropic = QLineEdit(s.ai.anthropic_model)
        self.set_model_openai = QLineEdit(s.ai.openai_model)
        self.set_auto_model = QCheckBox("Für einfache Fragen automatisch das schnelle Modell verwenden")
        self.set_auto_model.setChecked(s.ai.auto_model_selection)
        self.set_key = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.set_key.setAccessibleName("API-Schlüssel")
        self.set_key.returnPressed.connect(self.save_key)
        self.set_provider.currentIndexChanged.connect(self._show_key_state)
        self.set_cost_limit = QDoubleSpinBox(maximum=10000, decimals=2, value=s.ai.cost_limit_usd, suffix=" $")
        form.addRow("Anbieter", self.set_provider)
        form.addRow("Claude-Modell", self.set_model_anthropic)
        form.addRow("OpenAI-Modell", self.set_model_openai)
        form.addRow("", self.set_auto_model)
        form.addRow("API-Schlüssel", self.set_key)
        form.addRow("Kostenlimit/Monat (0 = aus)", self.set_cost_limit)
        box.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("Schlüssel speichern", self.save_key))
        row.addWidget(button("Schlüssel löschen", self.delete_key))
        row.addWidget(button("Verbindung testen", self.test_connection))
        box.addLayout(row)
        self.set_key_state = QLabel("", objectName="muted", wordWrap=True)
        box.addWidget(self.set_key_state)
        left = QVBoxLayout()
        left.addWidget(frame)
        columns.addLayout(left)

        frame, box = card("Tastenkürzel, Sicherheit und Darstellung")
        form = QFormLayout()
        self.set_hotkeys_on = QCheckBox("Globale Tastenkürzel aktiv")
        self.set_hotkeys_on.setChecked(s.hotkeys.enabled)
        form.addRow("", self.set_hotkeys_on)
        self.set_hotkey: dict[str, QKeySequenceEdit] = {}
        for name, label in HOTKEY_LABELS.items():
            edit = QKeySequenceEdit(QKeySequence(getattr(s.hotkeys, name)))
            edit.setMaximumSequenceLength(1)  # eine Kombination, keine Tastenfolge
            edit.setClearButtonEnabled(True)
            edit.setAccessibleName(f"Tastenkürzel {label}")
            self.set_hotkey[name] = edit
            form.addRow(label, edit)
        self.set_conflict = QComboBox()
        for key, label in (("ask", "Nachfragen"), ("skip", "Überspringen"), ("rename", "Unter neuem Namen anlegen"), ("overwrite", "Ersetzen")):
            self.set_conflict.addItem(label, key)
        self.set_conflict.setCurrentIndex(self.set_conflict.findData(s.automation.conflict_policy))
        self.set_copy = QCheckBox("Dateien standardmäßig als Übungskopie öffnen")
        self.set_copy.setChecked(s.access.work_on_copy_default)
        self.set_backup = QCheckBox("Vor jeder Ausführung automatisch sichern")
        self.set_backup.setChecked(s.security.always_backup)
        self.set_exam = QCheckBox("Automatisierung bei Anzeichen einer Prüfung sperren")
        self.set_exam.setChecked(s.security.block_in_exam_mode)
        self.set_mass = QSpinBox(maximum=100000, value=s.security.mass_change_threshold)
        self.set_vision = QCheckBox("KI-Bilderkennung als Rückfall, wenn die OCR nichts erkennt")
        self.set_vision.setChecked(s.ai.use_vision)
        self.set_theme = QComboBox()
        self.set_theme.addItem("Dunkel", "dark")
        self.set_theme.addItem("Hell", "light")
        self.set_theme.setCurrentIndex(self.set_theme.findData(s.ui.theme))
        self.set_log = QComboBox()
        self.set_log.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.set_log.setCurrentText(s.logging.level)
        form.addRow("Bei Namenskonflikten", self.set_conflict)
        form.addRow("", self.set_copy)
        form.addRow("", self.set_backup)
        form.addRow("", self.set_exam)
        form.addRow("Extra-Bestätigung ab … Datensätzen", self.set_mass)
        form.addRow("", self.set_vision)
        self.set_check_req = QCheckBox("Nach der Ausführung jede Anforderung gegen die Datenbank prüfen (KI)")
        self.set_check_req.setChecked(s.automation.check_requirements)
        form.addRow("", self.set_check_req)
        self.set_diagnostic = QCheckBox("Diagnose im Bericht: Anforderung → Schritte → Prüfung (ohne KI-Anfrage)")
        self.set_diagnostic.setChecked(s.automation.diagnostic_report)
        form.addRow("", self.set_diagnostic)
        self.set_start_mode = QComboBox()
        for key, label in (("window", "Hauptfenster"), ("compact", "Kompaktfenster"), ("background", "Nur Infobereich (Hintergrund)")):
            self.set_start_mode.addItem(label, key)
        self.set_start_mode.setCurrentIndex(self.set_start_mode.findData(s.ui.start_mode))
        form.addRow("Beim Start zeigen", self.set_start_mode)
        self.set_overlay = QCheckBox("Statuspunkt oben links (gelb = arbeitet, grün = erfolgreich, rot = Fehler)")
        self.set_overlay.setChecked(s.ui.show_overlay)
        form.addRow("", self.set_overlay)
        self.set_autostart = QCheckBox("Mit Windows im Hintergrund starten")
        self.set_autostart.setChecked(autostart.get() is not None)
        self.set_autostart.setEnabled(autostart.command() is not None)  # nur die gebaute EXE hat einen festen Pfad
        form.addRow("", self.set_autostart)
        form.addRow("Design", self.set_theme)
        form.addRow("Log-Stufe", self.set_log)
        box.addLayout(form)
        box.addWidget(QLabel(f"Schlüsselablage: {self.ctx.secrets.backend_name}\nLogdatei: {self.ctx.log_file}", objectName="muted", wordWrap=True))
        box.addStretch()
        columns.addWidget(frame)
        layout.addLayout(columns, 1)
        row = QHBoxLayout()
        row.addWidget(button("Einstellungen speichern", self.save_settings, "primary"))
        row.addStretch()
        layout.addLayout(row)
        self._show_key_state()

    def _show_key_state(self) -> None:
        provider = self.set_provider.currentData()
        key = self.ctx.secrets.get_api_key(provider)
        label = PROVIDER_LABELS.get(provider, provider)
        # Ein gespeicherter Schlüssel wird nie ins Eingabefeld geladen – Punkte im Feld wären sonst nicht
        # von einer ungespeicherten Eingabe zu unterscheiden. Der Platzhalter zeigt den Zustand.
        self.set_key.setPlaceholderText(f"gespeichert: {self.ctx.secrets.mask(key)} – zum Ersetzen neuen Schlüssel eingeben"
                                        if key else f"{label}-Schlüssel einfügen")
        self.set_key_state.setText(f"Gespeicherter Schlüssel: {self.ctx.secrets.mask(key)} ({self.ctx.secrets.backend_name})"
                                   if key else "Kein Schlüssel gespeichert.")

    def save_key(self) -> bool:
        """Speichert einen eingegebenen Schlüssel. ``True`` = gespeichert oder nichts eingegeben, ``False`` = Fehler."""
        provider, value = self.set_provider.currentData(), self.set_key.text()
        if not value.strip():
            return True
        try:
            self.ctx.secrets.set_api_key(provider, value)
        except WidsError as exc:
            self.set_key_state.setText(f"Nicht gespeichert: {exc.user_message}")
            log.error("Schlüssel speichern fehlgeschlagen: %s", exc.detail or exc.user_message)
            return False
        self.ctx.ai.reset(provider)
        self.set_key.clear()
        self._show_key_state()
        return True

    def delete_key(self) -> None:
        provider = self.set_provider.currentData()
        self.ctx.secrets.delete_api_key(provider)
        self.ctx.ai.reset(provider)
        self.set_key.clear()
        self._show_key_state()

    def test_connection(self) -> None:
        if not self.save_key():  # eine Eingabe im Feld wird zuerst gespeichert
            return
        provider = self.set_provider.currentData()
        self.set_key_state.setText("Verbindung wird getestet (echte Anfrage an das Modell) …")

        def done(check: ConnectionCheck) -> None:
            self.set_key_state.setText(check.message())
            self.refresh_dashboard()

        def failed(exc: Exception) -> None:
            self.set_key_state.setText(f"Test fehlgeschlagen: {getattr(exc, 'user_message', exc)}")

        self.run_async(lambda: self.ctx.ai.test_connection(provider), done, failed)

    def save_settings(self) -> None:
        s = self.ctx.settings
        try:
            combos = {name: edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
                      for name, edit in self.set_hotkey.items()}
            parsed = [parse(combo) for combo in combos.values()]  # meldet leere, reservierte und unbekannte Kürzel
            if len(set(parsed)) != len(parsed):
                raise HotkeyError("Ein Tastenkürzel ist doppelt vergeben.")
        except HotkeyError as exc:
            self.show_error(exc)
            return
        if not self.save_key():  # ein eingegebener Schlüssel wird mitgespeichert
            return
        for name, combo in combos.items():
            setattr(s.hotkeys, name, combo)
        s.hotkeys.enabled = self.set_hotkeys_on.isChecked()
        s.ai.provider = self.set_provider.currentData()
        s.ai.anthropic_model = self.set_model_anthropic.text().strip() or s.ai.anthropic_model
        s.ai.openai_model = self.set_model_openai.text().strip() or s.ai.openai_model
        s.ai.auto_model_selection = self.set_auto_model.isChecked()
        s.ai.cost_limit_usd = self.set_cost_limit.value()
        s.ai.use_vision = self.set_vision.isChecked()
        s.automation.check_requirements = self.set_check_req.isChecked()
        s.automation.diagnostic_report = self.set_diagnostic.isChecked()
        s.ui.start_mode = self.set_start_mode.currentData()
        s.ui.show_overlay = self.set_overlay.isChecked()
        if self.set_autostart.isEnabled() and self.set_autostart.isChecked() != (autostart.get() is not None):
            try:
                autostart.set_enabled(self.set_autostart.isChecked())
            except OSError as exc:
                self.show_error(WidsError("Autostart konnte nicht geändert werden.", detail=str(exc)))
        s.automation.conflict_policy = self.set_conflict.currentData()
        s.access.work_on_copy_default = self.set_copy.isChecked()
        s.security.always_backup = self.set_backup.isChecked()
        s.security.block_in_exam_mode = self.set_exam.isChecked()
        s.security.mass_change_threshold = self.set_mass.value()
        s.ui.theme = self.set_theme.currentData()
        s.logging.level = self.set_log.currentText()  # type: ignore[assignment]  # Auswahl nur aus den vier Stufen
        self.ctx.save_settings()
        self.apply_theme()
        self.register_hotkeys()
        self.refresh_dashboard()
        if self._hotkey_errors:
            self.show_error(HotkeyError("Gespeichert, aber: " + " ".join(self._hotkey_errors.values())))

    # ------------------------------------------------------------------ Ende
    def closeEvent(self, event: Any) -> None:
        if not self._quitting and self.tray is not None:
            # Fenster schließen ≠ Programm beenden: Tastenkürzel und Infobereich bleiben aktiv
            event.ignore()
            self._to_background()
            return
        if self._busy:
            # Nicht beenden, solange eine Rückfrage aus dem Arbeits-Thread kommen kann (sonst warten beide aufeinander).
            # Stattdessen abbrechen; set_busy(False) beendet danach (siehe dort).
            self._quitting = True
            self.cancel()
            event.ignore()
            return
        self._timer.stop()
        self.hotkeys.stop()
        self.compact.close()
        self.overlay.close()
        if self.tray is not None:
            self.tray.hide()
        s = self.ctx.settings.ui
        s.window_width, s.window_height = self.width(), self.height()
        self.ctx.storage.save_settings(self.ctx.settings)
        QThreadPool.globalInstance().waitForDone(15000)
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

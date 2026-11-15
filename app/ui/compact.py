"""Kompaktmodus: kleines, immer sichtbares Statusfenster mit Schnellaktionen und Fragenmodus."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME

if TYPE_CHECKING:
    from app.agent.orchestrator import PipelineEvent
    from app.ui.main_window import MainWindow

QUESTION_PREFIX = "Beantworte und erkläre kurz diese Access-Frage bzw. Aufgabe (bei Multiple Choice: richtige Antwort(en) nennen):\n"


def app_icon(color: str = "#6d7dff") -> QIcon:
    """Programmsymbol ohne Bilddatei: farbiger Kreis mit „W“ (die Farbe zeigt im Infobereich den Status)."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPixelSize(38)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "W")
    painter.end()
    return QIcon(pixmap)


class CompactWindow(QWidget):
    answer: QTextBrowser

    def __init__(self, main: MainWindow) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.main = main
        self.setWindowTitle(f"{APP_NAME} – kompakt")
        self.setFixedWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.db = QLabel("", objectName="muted", wordWrap=True)
        self.status = QLabel("bereit", objectName="value", wordWrap=True)
        self.progress = QProgressBar(textVisible=False)
        self.log = QListWidget()
        self.log.setMaximumHeight(110)
        layout.addWidget(self.db)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)
        hk = main.ctx.settings.hotkeys
        row = QHBoxLayout()
        self.btn_solve = QPushButton(f"Aufgabe lösen ({hk.solve})", objectName="primary")
        self.btn_solve.clicked.connect(lambda: main.capture_task(then="execute"))
        self.btn_cancel = QPushButton(f"Abbrechen ({hk.cancel})", objectName="danger")
        self.btn_cancel.clicked.connect(main.cancel)
        full = QPushButton("Vollansicht")
        full.clicked.connect(self.open_full)
        row.addWidget(self.btn_solve)
        row.addWidget(self.btn_cancel)
        row.addWidget(full)
        layout.addLayout(row)
        self.question = QLineEdit(placeholderText=f"Access-Frage stellen ({hk.ask}) …")
        self.question.returnPressed.connect(self.ask)
        layout.addWidget(self.question)
        row = QHBoxLayout()
        self.btn_ask = QPushButton("Fragen")
        self.btn_ask.clicked.connect(self.ask)
        self.btn_screen = QPushButton("Frage vom Bildschirm")
        self.btn_screen.clicked.connect(self.ask_from_screen)
        self.btn_copy = QPushButton("Antwort kopieren")
        self.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.answer.toPlainText()))
        for button in (self.btn_ask, self.btn_screen, self.btn_copy):
            row.addWidget(button)
        layout.addLayout(row)
        self.answer = QTextBrowser()
        self.answer.setMaximumHeight(200)
        self.answer.hide()
        layout.addWidget(self.answer)
        self._timer = QTimer(self, interval=400)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        main.destroyed.connect(self.deleteLater)  # eigenes Fenster ohne Eltern – mit dem Hauptfenster aufräumen

    # ------------------------------------------------------------------ Anzeige
    def show_raise(self) -> None:
        if not self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.adjustSize()
            self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)
        self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()

    def open_full(self) -> None:
        self.hide()
        self.main.bring_to_front(1, full=True)  # ausdrücklich angefordert

    def refresh(self) -> None:
        if not self.isVisible():
            return
        main = self.main
        status = main.ctx.access.status()
        self.db.setText(f"Datenbank: {status.name} ({'laufendes Access' if status.mode == 'attached' else 'Datei'})"
                        if status.connected else "Keine Datenbank verbunden – Alt+W verbindet die te.comp-Übung automatisch.")
        self.status.setText(main.task_status.text() or ("läuft …" if main._busy else "bereit"))
        self.progress.setVisible(main.progress.isVisible())
        self.progress.setRange(main.progress.minimum(), main.progress.maximum())
        self.progress.setValue(main.progress.value())
        busy = main._busy
        self.btn_solve.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def add_event(self, event: PipelineEvent) -> None:
        prefix = f"{event.step_id}: " if event.step_id else ""
        self.log.addItem(f"{prefix}{event.message}")
        while self.log.count() > 30:
            self.log.takeItem(0)
        self.log.scrollToBottom()

    # ------------------------------------------------------------------ Fragenmodus
    def _set_answer(self, text: str) -> None:
        self.answer.setMarkdown(text)
        self.answer.show()
        self.adjustSize()
        for button in (self.btn_ask, self.btn_screen):
            button.setEnabled(True)

    def _failed(self, exc: Any) -> None:
        self._set_answer(f"⚠️ {getattr(exc, 'user_message', exc)}")

    def ask(self) -> None:
        question = self.question.text().strip()
        if not question:
            return
        self._start("Die KI antwortet …")
        self.main.run_async(lambda: self.main.ctx.ask(question, []), self._set_answer, self._failed)

    def ask_from_screen(self) -> None:
        self._start("Frage wird vom Bildschirm gelesen …")
        ctx = self.main.ctx

        def work() -> str:
            task = ctx.reader.from_window()
            if task.method == "ocr" and task.source.lower() != "tecomptraining.exe":
                # Fremdes Fenster: Text nur anzeigen, nicht automatisch an die KI senden
                return f"Gelesen aus „{task.source}“ (nicht gesendet – bitte prüfen und oben einfügen):\n\n{task.text}"
            return ctx.ask(QUESTION_PREFIX + task.text, [])

        self.main.run_async(work, self._set_answer, self._failed)

    def _start(self, text: str) -> None:
        for button in (self.btn_ask, self.btn_screen):
            button.setEnabled(False)
        self.answer.setPlainText(text)
        self.answer.show()

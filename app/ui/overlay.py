"""Winziger Statuspunkt oben links (2 × 2 Pixel): gelb = arbeitet, grün = erfolgreich, rot = Fehler.

Kein Text, kein Hintergrund, keine Animation. Nimmt nie den Fokus, lässt Klicks durch und erscheint nicht in der
Taskleiste. Grün verschwindet 3 Sekunden nach dem Abschluss; Rot (Fehler, nicht abgeschlossen, angehalten) bleibt
stehen, bis die nächste Aufgabe startet – sonst würde ein Halt im Hintergrund übersehen. Details stehen im Verlauf.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

#: Zustand → Farbe
STATES: dict[str, str] = {
    "working": "#FFD400",  # gelb
    "done": "#00C853",  # grün
    "error": "#FF1744",  # rot
}
SIZE = 2
HIDE_AFTER_MS = 3000


class StatusOverlay(QWidget):
    def __init__(self) -> None:
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setObjectName("statusOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE, SIZE)
        self.state = ""
        self._color = QColor(STATES["working"])
        self._hide_timer = QTimer(self, singleShot=True, interval=HIDE_AFTER_MS)
        self._hide_timer.timeout.connect(self.clear)

    def show_state(self, state: str) -> None:
        """``working`` und ``error`` bleiben stehen; ``done`` verschwindet nach 3 Sekunden; alles andere blendet aus."""
        if state not in STATES:
            self.clear()
            return
        self.state = state
        self._color = QColor(STATES[state])
        self.setAccessibleName(f"WIDS-Status: {state}")
        primary = QGuiApplication.primaryScreen()
        # bei Bildschirmskalierung (125 %, 150 % …) höchstens 2 × 2 echte Pixel
        side = max(1, int(SIZE / max(primary.devicePixelRatio(), 1.0)))
        self.setFixedSize(side, side)
        screen = primary.geometry()
        self.move(screen.left(), screen.top())
        self.show()
        self.update()
        if state == "done":
            self._hide_timer.start()
        else:
            self._hide_timer.stop()

    def clear(self) -> None:
        self.state = ""
        self._hide_timer.stop()
        self.hide()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        painter.end()

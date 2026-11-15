"""Genau eine laufende Instanz je Windows-Benutzer.

Ein benannter Mutex entscheidet, wer die erste Instanz ist. Über einen lokalen Socket kann ein zweiter
Start der laufenden Instanz ein Kommando schicken (``show`` = Status anzeigen, ``quit`` = sauber beenden)
und endet danach selbst – so konkurrieren nie zwei Instanzen um Tastenkürzel oder die Datenbank.
"""
from __future__ import annotations

import getpass
import hashlib
import os
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app import APP_ID

ERROR_ALREADY_EXISTS = 183


class SingleInstance(QObject):
    command = Signal(str)

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self._name = name or f"{APP_ID}-{getpass.getuser()}"
        if name is None and os.environ.get("WIDS_DATA_DIR"):
            # eigener Datenordner (Tests/Prüfskripte) = eigene Instanz; die normale Instanz bleibt unberührt
            self._name += "-" + hashlib.sha1(os.environ["WIDS_DATA_DIR"].lower().encode()).hexdigest()[:10]
        self._mutex: Any = None
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """``True``, wenn dies die erste Instanz ist (und ab jetzt Kommandos annimmt)."""
        import win32api
        import win32event

        self._mutex = win32event.CreateMutex(None, False, f"Local\\{self._name}")
        if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(self._mutex)
            self._mutex = None
            return False
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(self._name)
        return True

    def send(self, message: str, attempts: int = 20) -> bool:
        """Schickt der laufenden Instanz ein Kommando (wartet kurz, falls sie gerade erst startet)."""
        for _ in range(attempts):
            socket = QLocalSocket()
            socket.connectToServer(self._name)
            if socket.waitForConnected(500):
                socket.write(message.encode("utf-8"))
                # Rückgabewert unzuverlässig (unter Windows meldet der Pipe-Writer den Abschluss erst per Ereignis)
                socket.waitForBytesWritten(2000)
                socket.disconnectFromServer()
                return True
        return False

    def _on_connection(self) -> None:
        assert self._server is not None
        socket = self._server.nextPendingConnection()
        # Synchron lesen (der zweite Start schreibt sofort nach dem Verbinden): keine Python-Slots am Socket,
        # die beim Zerstören noch aufgerufen werden könnten.
        if not socket.bytesAvailable():
            socket.waitForReadyRead(1000)
        message = bytes(socket.readAll().data()).decode("utf-8").strip()
        socket.abort()
        socket.deleteLater()
        if message:
            self.command.emit(message)

    def release(self) -> None:
        import win32api

        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex is not None:
            win32api.CloseHandle(self._mutex)
            self._mutex = None

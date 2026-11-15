"""Thread-sichere Fassade für alle Access-Zugriffe.

COM-Objekte gehören zu dem Thread (Apartment), in dem sie erzeugt wurden. Der
``AccessService`` betreibt deshalb genau einen dedizierten COM-Thread; Oberfläche und
Agenten übergeben Funktionen, die dort mit der aktuellen :class:`AccessEngine` ausgeführt
werden. So bleibt die Qt-Oberfläche auch bei langen Access-Operationen bedienbar.
"""
from __future__ import annotations

import gc
import queue
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, TypeVar

from pydantic import BaseModel

from app.access.comerrors import is_com_error, translate_com_error
from app.access.engine import AccessEngine
from app.access.models import DatabaseSchema
from app.access.session import find_running_database, is_access_installed
from app.core.errors import AccessError, AccessNotRunningError, AccessTimeoutError, WidsError
from app.core.logging_setup import get_logger

log = get_logger(__name__)
T = TypeVar("T")


def _detach_com_references(exc: BaseException) -> None:
    """Entfernt COM-Objekte aus den Tracebacks einer Ausnahme, bevor sie den COM-Thread verlässt.

    Die Frames eines Tracebacks halten lokale Variablen fest – darunter DAO-/Access-Objekte.
    Würde die Ausnahme samt Traceback in einen anderen Thread wandern, gäbe erst dessen Garbage
    Collector diese Objekte frei: im falschen COM-Apartment und womöglich erst, nachdem der
    COM-Thread beendet ist. Das führt zu hängenden Aufrufen. Deshalb werden die Frames hier, im
    richtigen Thread, geleert; der Traceback-Text wird vorher ins Log geschrieben.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        tb = current.__traceback__
        if tb is not None:
            if log.isEnabledFor(10):  # DEBUG
                log.debug("Ausnahme im COM-Thread:\n%s", "".join(traceback.format_exception(current)))
            traceback.clear_frames(tb)
        pending.extend((current.__cause__, current.__context__))


class ComWorker:
    """Ein einzelner Thread mit initialisiertem COM-Apartment und Auftragswarteschlange."""

    def __init__(self, name: str = "wids-com") -> None:
        self._queue: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._started = threading.Event()
        self._thread.start()
        self._started.wait(10)

    @property
    def thread_id(self) -> int | None:
        return self._thread.ident

    def _run(self) -> None:
        import pythoncom

        pythoncom.CoInitialize()
        self._started.set()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                func, future = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(func())
                except BaseException as exc:
                    _detach_com_references(exc)
                    future.set_exception(exc)
                del func, future, item
        finally:
            # Verbliebene COM-Objekte im eigenen Apartment freigeben, bevor es abgebaut wird
            gc.collect()
            pythoncom.CoUninitialize()

    def submit(self, func: Callable[[], T]) -> Future[T]:
        future: Future[T] = Future()
        self._queue.put((func, future))
        return future

    def call(self, func: Callable[[], T], timeout: float | None = None) -> T:
        if threading.get_ident() == self._thread.ident:
            return func()  # bereits im COM-Thread (verschachtelter Aufruf)
        future = self.submit(func)
        try:
            return future.result(timeout)
        except FutureTimeout as exc:
            raise AccessTimeoutError() from exc

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)


class ConnectionStatus(BaseModel):
    connected: bool = False
    path: str = ""
    name: str = ""
    mode: str = ""
    access_installed: bool = False
    running_database: str = ""
    message: str = ""


class AccessService:
    def __init__(self, *, operation_timeout_s: float = 60.0) -> None:
        self._worker = ComWorker()
        self._engine: AccessEngine | None = None
        self._lock = threading.RLock()
        self._schema_cache: DatabaseSchema | None = None
        self.operation_timeout_s = operation_timeout_s

    # ------------------------------------------------------------------ Verbindung
    @property
    def is_connected(self) -> bool:
        return self._engine is not None

    @property
    def path(self) -> str:
        return self._engine.path if self._engine else ""

    @property
    def mode(self) -> str:
        return self._engine.mode if self._engine else ""

    def _call(self, func: Callable[[], T], timeout: float | None = None) -> T:
        limit = self.operation_timeout_s if timeout is None else timeout
        try:
            return self._worker.call(func, limit)
        except AccessTimeoutError:
            self._handle_timeout()
            raise
        except WidsError:
            raise
        except Exception as exc:
            # pywin32 meldet ein verschwundenes Access oft als AttributeError („CurrentDb.TableDefs“) → klar benennen
            if not (isinstance(exc, AttributeError) or is_com_error(exc)):
                raise
            engine = self._engine
            if engine is not None and not self._worker.call(engine.session.is_alive, 10):
                raise AccessError("Die Verbindung zu Access wurde getrennt (Access wurde geschlossen oder ist "
                                  "abgestürzt). Bitte die Übung neu verbinden – die Sicherung bleibt erhalten.",
                                  detail=f"{type(exc).__name__}: {exc}") from exc
            if is_com_error(exc):
                raise translate_com_error(exc) from exc
            raise

    def _handle_timeout(self) -> None:
        """Nach einer Zeitüberschreitung: eigene unsichtbare Instanz beenden, fremde nie anfassen."""
        engine = self._engine
        if engine is None:
            return
        if engine.session.kill_owned_instance():
            log.warning("Zeitüberschreitung: eigene Access-Instanz wurde beendet, Verbindung wird verworfen")
            self._engine = None
            self._schema_cache = None

    def connect(self, path: str, *, prefer_running: bool = True) -> ConnectionStatus:
        def work() -> None:
            self._close_engine()
            self._engine = AccessEngine.open(path, prefer_running=prefer_running)

        with self._lock:
            self._call(work)
            self._schema_cache = None
        return self.status()

    def attach_active(self) -> ConnectionStatus:
        def work() -> None:
            self._close_engine()
            self._engine = AccessEngine.attach_active()

        with self._lock:
            self._call(work)
            self._schema_cache = None
        return self.status()

    def create_database(self, path: str, populate: Callable[[AccessEngine], None] | None = None) -> ConnectionStatus:
        def work() -> None:
            self._close_engine()
            engine = AccessEngine.create(path)
            try:
                if populate is not None:
                    populate(engine)
            except Exception:
                engine.close()
                raise
            self._engine = engine

        with self._lock:
            self._call(work, timeout=max(self.operation_timeout_s, 120))
            self._schema_cache = None
        return self.status()

    def _close_engine(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:
                log.warning("Schließen der Datenbank fehlgeschlagen: %s", exc)

    def disconnect(self) -> None:
        with self._lock:
            try:
                self._worker.call(self._close_engine, 20)
            except WidsError:
                self._handle_timeout()
            self._schema_cache = None

    def shutdown(self) -> None:
        self.disconnect()
        self._worker.stop()

    # ------------------------------------------------------------------ Ausführung
    def run(self, func: Callable[[AccessEngine], T], *, timeout: float | None = None) -> T:
        """Führt ``func(engine)`` im COM-Thread aus."""

        def work() -> T:
            engine = self._engine
            if engine is None:
                raise AccessNotRunningError("Es ist keine Datenbank verbunden.")
            return func(engine)

        return self._call(work, timeout)


    def schema(self, *, refresh: bool = False, detailed_objects: bool = False) -> DatabaseSchema:
        if self._schema_cache is not None and not refresh and not detailed_objects:
            return self._schema_cache
        schema = self.run(lambda e: e.schema(detailed_objects=detailed_objects))
        if not detailed_objects:
            self._schema_cache = schema
        return schema

    def invalidate_schema(self) -> None:
        self._schema_cache = None

    # ------------------------------------------------------------------ Status
    def detect_running_database(self) -> str:
        """Pfad der Datenbank, die gerade in einer laufenden Access-Instanz geöffnet ist (oder '')."""

        def work() -> str:
            found = find_running_database(None)
            return found[1] if found else ""

        try:
            # bewusst ohne _call(): eine Statusabfrage, die hinter einer langen Operation wartet,
            # darf keine Zeitüberschreitungs-Behandlung (Beenden der eigenen Instanz) auslösen
            return self._worker.call(work, 10)
        except WidsError as exc:
            log.debug("Erkennung der laufenden Datenbank fehlgeschlagen: %s", exc)
            return ""

    def is_alive(self) -> bool:
        if self._engine is None:
            return False
        try:
            return self._worker.call(lambda: bool(self._engine and self._engine.session.is_alive()), 5)
        except WidsError:
            return False

    def status(self) -> ConnectionStatus:
        engine = self._engine
        installed = is_access_installed()
        if engine is None:
            return ConnectionStatus(
                access_installed=installed,
                message="Nicht verbunden" if installed else "Microsoft Access ist nicht installiert",
            )
        mode_text = "laufende Access-Instanz" if engine.mode == "attached" else "direkter Dateizugriff (DAO)"
        return ConnectionStatus(
            connected=True,
            path=engine.path,
            name=engine.session.name,
            mode=engine.mode,
            access_installed=installed,
            message=f"Verbunden über {mode_text}",
        )

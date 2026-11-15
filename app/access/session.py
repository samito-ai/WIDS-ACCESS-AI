"""Verbindungsverwaltung zu Access-Datenbanken.

Zwei Betriebsarten:

* **attached** – Die Datenbank ist bereits in einer laufenden Access-Instanz geöffnet
  (typischer te.comp-Ablauf). Es wird ``Application.CurrentDb()`` dieser Instanz verwendet,
  damit Access alle Änderungen sofort sieht.
* **standalone** – Die Datei wird direkt über die DAO-Engine geöffnet; Access selbst muss
  nicht laufen. Für Formulare/Berichte wird bei Bedarf eine unsichtbare Access-Instanz gestartet.

Alle Methoden müssen im selben (COM-initialisierten) Thread aufgerufen werden.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Literal

from app.access import constants as c
from app.access.comerrors import com_guard, is_com_error, translate_com_error
from app.core.errors import (
    AccessError,
    AccessNotInstalledError,
    AccessNotRunningError,
    DatabaseNotFoundError,
)
from app.core.logging_setup import get_logger

log = get_logger(__name__)

SessionMode = Literal["attached", "standalone"]


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def late_bound(obj: Any) -> Any:
    """Lädt den Windows-COM-Client erst beim Zugriff im COM-Thread."""
    from app.access.dispatch import late_bound as dispatch

    return dispatch(obj)


def dao_engine() -> Any:
    """Erzeugt die DAO-Engine (ACE). Wirft ``AccessNotInstalledError``, wenn sie fehlt."""
    last: BaseException | None = None
    for prog_id in ("DAO.DBEngine.120", "DAO.DBEngine.36"):
        try:
            return late_bound(prog_id)
        except Exception as exc:
            last = exc
    raise AccessNotInstalledError(detail=str(last))


def is_access_installed() -> bool:
    """Prüft, ob Access.Application registriert ist (ohne Access zu starten)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Access.Application\\CLSID"):
            return True
    except OSError:
        return False


def running_access_instances() -> list[Any]:
    """Liefert alle über die Running Object Table erreichbaren Access-Instanzen."""
    import pythoncom

    apps: list[Any] = []
    seen: set[int] = set()

    def add(app: Any) -> None:
        try:
            hwnd = int(app.hWndAccessApp())
        except Exception:
            return
        if hwnd not in seen:
            seen.add(hwnd)
            apps.append(app)

    try:
        rot = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        for moniker in rot:
            try:
                display = moniker.GetDisplayName(ctx, None)
            except Exception:
                continue
            if not display.lower().endswith((".accdb", ".mdb", ".accde", ".mde")):
                continue
            try:
                add(late_bound(rot.GetObject(moniker)))
            except Exception:
                continue
    except Exception as exc:
        log.debug("ROT-Aufzählung fehlgeschlagen: %s", exc)
    try:
        clsid = pythoncom.CLSIDFromProgID("Access.Application")
        add(late_bound(pythoncom.GetActiveObject(clsid)))
    except Exception:
        pass
    return apps


def current_database_path(app: Any) -> str | None:
    try:
        full = app.CurrentProject.FullName
    except Exception:
        return None
    return str(full) if full else None


def find_running_database(path: str | None = None) -> tuple[Any, str] | None:
    """Sucht eine laufende Access-Instanz (optional: die eine bestimmte Datei geöffnet hat)."""
    for app in running_access_instances():
        current = current_database_path(app)
        if not current:
            continue
        if path is None or _same_file(current, path):
            return app, current
    return None


class AccessSession:
    """Kapselt eine geöffnete Datenbank samt (optionaler) Access-Anwendung."""

    def __init__(self) -> None:
        self.mode: SessionMode = "standalone"
        self.path: str = ""
        self.read_only: bool = False
        self._engine: Any = None
        self._db: Any = None
        self._app: Any = None
        self._owns_app: bool = False
        self._owned_pid: int | None = None

    # ------------------------------------------------------------------ Öffnen
    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        prefer_running: bool = True,
        read_only: bool = False,
        exclusive: bool = False,
    ) -> AccessSession:
        full = str(Path(path).resolve())
        if not os.path.exists(full):
            raise DatabaseNotFoundError(f"Datei nicht gefunden: {full}")
        session = cls()
        session.path = full
        session.read_only = read_only
        if prefer_running:
            found = find_running_database(full)
            if found is not None:
                app, _ = found
                with com_guard("Verbindung zur laufenden Access-Instanz"):
                    session._app = app
                    session._db = app.CurrentDb()
                    session.mode = "attached"
                log.info("Mit laufender Access-Instanz verbunden: %s", full)
                return session
        session._engine = dao_engine()
        with com_guard(f"Öffnen von {Path(full).name}"):
            session._db = session._engine.OpenDatabase(full, exclusive, read_only)
        session.mode = "standalone"
        log.info("Datenbank direkt über DAO geöffnet: %s", full)
        return session

    @classmethod
    def attach_active(cls) -> AccessSession:
        """Verbindet sich mit der Datenbank der laufenden Access-Instanz."""
        found = find_running_database(None)
        if found is None:
            raise AccessNotRunningError()
        app, current = found
        session = cls()
        session.path = str(Path(current).resolve())
        with com_guard("Verbindung zur laufenden Access-Instanz"):
            session._app = app
            session._db = app.CurrentDb()
            session.mode = "attached"
        return session

    @classmethod
    def create(cls, path: str | os.PathLike[str], *, overwrite: bool = False) -> AccessSession:
        """Erstellt eine neue, leere Datenbank (.accdb oder .mdb)."""
        full = str(Path(path).resolve())
        if os.path.exists(full):
            if not overwrite:
                raise AccessError(f"Die Datei existiert bereits: {full}")
            os.remove(full)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        session = cls()
        session.path = full
        session._engine = dao_engine()
        version = c.DB_VERSION_40 if full.lower().endswith(".mdb") else c.DB_VERSION_120
        with com_guard("Erstellen der Datenbank"):
            # CreateDatabase öffnet exklusiv – das würde Sicherungskopien blockieren; daher neu (geteilt) öffnen
            session._engine.CreateDatabase(full, c.DB_LANG_GENERAL, version).Close()
            session._db = session._engine.OpenDatabase(full, False, False)
        session.mode = "standalone"
        return session

    # ------------------------------------------------------------------ Zugriff
    @property
    def db(self) -> Any:
        if self._db is None:
            raise AccessError("Es ist keine Datenbank geöffnet.")
        return self._db

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def is_open(self) -> bool:
        return self._db is not None

    @property
    def has_application(self) -> bool:
        return self._app is not None

    def refresh_db(self) -> None:
        """Holt in der attached-Betriebsart ein frisches ``CurrentDb``-Objekt."""
        if self.mode == "attached" and self._app is not None:
            with com_guard("Aktualisieren der Datenbankverbindung"):
                self._db = self._app.CurrentDb()

    def is_alive(self) -> bool:
        if self._db is None:
            return False
        try:
            _ = self._db.Name
            return True
        except Exception:
            return False

    def application(self, *, create: bool = True) -> Any:
        """Gibt ``Access.Application`` zurück; startet bei Bedarf eine unsichtbare Instanz.

        In der standalone-Betriebsart wird die DAO-Verbindung dabei auf die ``CurrentDb`` der
        neuen Instanz umgestellt, damit nicht zwei Verbindungen konkurrierende Sperren halten.
        """
        if self._app is not None:
            return self._app
        if not create:
            raise AccessNotRunningError("Für diese Operation wird Microsoft Access benötigt.")
        if not is_access_installed():
            raise AccessNotInstalledError(
                "Für Formulare und Berichte wird eine vollständige Microsoft-Access-Installation benötigt."
            )
        import pythoncom
        import win32process

        # Eigene DAO-Verbindung schließen, damit Access die Datei ohne Konflikte öffnen kann
        if self._db is not None and self.mode == "standalone":
            with contextlib.suppress(Exception):
                self._db.Close()
            self._db = None
        try:
            raw = pythoncom.CoCreateInstance(
                "Access.Application", None, pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch,
            )
            app = late_bound(raw)
        except Exception as exc:
            if is_com_error(exc):
                raise translate_com_error(exc, "Start von Microsoft Access") from exc
            raise
        self._app = app
        self._owns_app = True
        try:
            with contextlib.suppress(Exception):
                app.AutomationSecurity = c.MSO_AUTOMATION_SECURITY_FORCE_DISABLE
            with contextlib.suppress(Exception):
                app.Visible = False
            with contextlib.suppress(Exception):
                _, pid = win32process.GetWindowThreadProcessId(int(app.hWndAccessApp()))
                self._owned_pid = pid
            with com_guard("Öffnen der Datenbank in Access"):
                app.OpenCurrentDatabase(self.path, False)
                self._db = app.CurrentDb()
        except Exception:
            self._shutdown_owned_app()
            raise
        log.info("Unsichtbare Access-Instanz gestartet (PID %s)", self._owned_pid)
        return app

    def refresh_navigation(self) -> None:
        """Aktualisiert den Navigationsbereich der verbundenen Access-Instanz."""
        if self._app is not None:
            with contextlib.suppress(Exception):
                self._app.RefreshDatabaseWindow()

    # ------------------------------------------------------------------ Transaktionen
    def workspace(self) -> Any:
        if self._app is not None:
            return self._app.DBEngine.Workspaces(0)
        if self._engine is None:
            self._engine = dao_engine()
        return self._engine.Workspaces(0)

    @contextlib.contextmanager
    def transaction(self) -> Any:
        """Transaktion über den Standard-Workspace (für Datenänderungen)."""
        ws = self.workspace()
        with com_guard("Transaktion starten"):
            ws.BeginTrans()
        try:
            yield
        except Exception:
            with contextlib.suppress(Exception):
                ws.Rollback()
            raise
        else:
            with com_guard("Transaktion abschließen"):
                ws.CommitTrans()

    # ------------------------------------------------------------------ Schließen
    def _shutdown_owned_app(self) -> None:
        app, self._app = self._app, None
        if app is None or not self._owns_app:
            return
        pid = self._owned_pid
        try:
            with contextlib.suppress(Exception):
                app.CloseCurrentDatabase()
            app.Quit(c.AC_QUIT_SAVE_NONE)
        except Exception as exc:
            log.warning("Access-Instanz ließ sich nicht sauber beenden: %s", exc)
            self.kill_owned_instance()
        else:
            # Warten, bis der Prozess wirklich beendet ist (gibt die Sperrdatei frei)
            if pid:
                try:
                    import psutil

                    psutil.Process(pid).wait(timeout=8)
                except psutil.NoSuchProcess:
                    pass
                except Exception:
                    self.kill_owned_instance()
            self._owned_pid = None
        self._owns_app = False

    def kill_owned_instance(self) -> bool:
        """Beendet ausschließlich die selbst gestartete, unsichtbare Access-Instanz hart."""
        if not self._owned_pid:
            return False
        try:
            import psutil

            proc = psutil.Process(self._owned_pid)
            if proc.name().lower() == "msaccess.exe":
                proc.kill()
                log.warning("Hängende eigene Access-Instanz beendet (PID %s)", self._owned_pid)
                return True
        except Exception as exc:
            log.debug("Beenden der Access-Instanz fehlgeschlagen: %s", exc)
        finally:
            self._owned_pid = None
        return False

    def close(self) -> None:
        db, self._db = self._db, None
        if self.mode == "standalone" and db is not None and not self._owns_app:
            with contextlib.suppress(Exception):
                db.Close()
        del db
        if self._owns_app:
            self._shutdown_owned_app()
        else:
            self._app = None
        self._engine = None

    def __enter__(self) -> AccessSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

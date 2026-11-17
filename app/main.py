"""Programmstart von WIDS ACCESS AI."""
from __future__ import annotations

import sys
from types import TracebackType


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import APP_NAME
    from app.core.context import AppContext
    from app.ui.main_window import MainWindow

    selftest = "--selftest" in sys.argv
    if selftest:
        # Für den Build-Test: prüft, dass alle nativen Module im Paket enthalten sind
        try:
            import openai  # noqa: F401
            import win32event  # noqa: F401  # Einzelinstanz (Mutex)
            import winrt.windows.graphics.imaging
            import winrt.windows.media.ocr
            import winrt.windows.storage.streams  # noqa: F401
            from PySide6 import QtNetwork  # noqa: F401  # Einzelinstanz (lokaler Socket)
        except Exception:
            return 2
        # Schlüsselablage in der gepackten EXE: Speichern, Lesen, Löschen mit eigenem Testeintrag
        from app import APP_ID
        from app.security.secrets import CredentialManagerBackend

        probe = CredentialManagerBackend(prefix=f"{APP_ID}-SELFTEST")
        probe.set("probe", "sk-ant-selftest-0123456789")
        stored = probe.get("probe")
        probe.delete("probe")
        if stored != "sk-ant-selftest-0123456789" or probe.get("probe") is not None:
            return 3
    qt = QApplication(sys.argv)
    qt.setApplicationName(APP_NAME)
    qt.setQuitOnLastWindowClosed(False)  # Infobereich: Beenden nur über „Beenden“ im Menü oder --quit
    instance = None
    if not selftest:
        from app.core.single_instance import SingleInstance

        instance = SingleInstance()
        if not instance.acquire():
            # Es läuft schon eine Instanz: ihr nur Bescheid geben (Status zeigen / beenden), selbst sofort enden
            instance.send("quit" if "--quit" in sys.argv else "show")
            return 0
        if "--quit" in sys.argv:
            instance.release()
            return 0  # nichts zu beenden
    ctx = AppContext()
    if selftest:
        ctx.settings.hotkeys.enabled = False  # der Selbsttest darf keine globalen Kürzel belegen
    window = MainWindow(ctx)
    start_mode = "background" if "--background" in sys.argv else ctx.settings.ui.start_mode
    if start_mode == "background" and window.tray is None:
        start_mode = "compact"  # ohne Infobereich gäbe es sonst keinen Weg zurück
    if instance is not None:
        from app.core import updater

        _install_crash_report()
        instance.command.connect(window.handle_command)
        _refresh_autostart()
        # nach einem Update: die vorherige Version erst löschen, wenn die neue eine Minute stabil läuft
        QTimer.singleShot(60_000, updater.cleanup_previous)
        # erst hier: nur die einzige laufende Instanz darf „läuft“-Einträge eines Absturzes abschließen
        interrupted = ctx.storage.mark_interrupted_tasks()
        if interrupted:
            window.notify(f"{len(interrupted)} Aufgabe(n) wurden beim letzten Beenden unterbrochen – Details und "
                          "Sicherung im Verlauf.")
    if selftest:
        # Für den Build-Test: Fenster aufbauen, Access-/DAO-Verfügbarkeit prüfen, sofort beenden
        from app.access.session import dao_engine

        dao_engine()
        # qt.quit statt window.close: das Fenster wird nie angezeigt, close würde die Schleife nicht beenden
        QTimer.singleShot(300, qt.quit)
    elif start_mode == "background" and ctx.settings.ui.first_run_done:
        pass  # nur das Symbol im Infobereich – kein Fenster, kein Fokuswechsel
    elif start_mode == "compact" and ctx.settings.ui.first_run_done:
        window.compact.show_raise()
    else:
        window.show()
        if not ctx.settings.ui.first_run_done:
            ctx.settings.ui.first_run_done = True
            ctx.storage.save_settings(ctx.settings)
            window.nav.setCurrentRow(5)
            QMessageBox.information(
                window, APP_NAME,
                "Willkommen!\n\n1. Hier in den Einstellungen einen API-Schlüssel speichern und „Verbindung testen“.\n"
                "2. Im Dashboard die geöffnete Access-Datenbank verbinden (oder eine Beispieldatenbank erstellen).\n"
                "3. In te.comp eine Übung öffnen und Alt+W drücken.",
            )
    code = qt.exec()
    ctx.shutdown()
    if instance is not None:
        instance.release()
    if window.restart_after_quit:  # installiertes Update: erst jetzt (Einzelinstanz freigegeben) neu starten
        from app.core import updater

        updater.restart()
    return 0 if selftest else code


def _install_crash_report() -> None:
    """Unerwartete Ausnahmen (Absturz eines Ablaufs) → anonymisierter Diagnosebericht, danach Standardbehandlung."""
    previous = sys.excepthook

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        try:
            import traceback

            from app.core import diagnostics

            if diagnostics.classify(exc) is not None:
                diagnostics.write_report("Programmabsturz/unerwartete Ausnahme", "Programm",
                                         "".join(traceback.format_exception(exc_type, exc, tb))[-4000:],
                                         outcome="nicht abgefangen")
        except Exception:
            pass  # der Bericht darf die Fehlerbehandlung nie verhindern
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def _refresh_autostart() -> None:
    """Wurde die EXE verschoben, zeigt ein bestehender Autostart-Eintrag auf den neuen Pfad."""
    from app.core import autostart

    current, wanted = autostart.get(), autostart.command()
    if current is not None and wanted is not None and current != wanted:
        try:
            autostart.set_enabled(True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())

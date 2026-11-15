"""Prüft die gebaute EXE über ihre Oberfläche: Schlüssel eingeben → „Verbindung testen“ → beenden → neu starten
→ gespeicherten Schlüssel erneut verwenden.

Aufruf (Schlüssel nur über die Umgebung, er wird nie ausgegeben):
    $env:ANTHROPIC_API_KEY="…"; python scripts\\check_exe_key.py dist\\WIDS-Access-AI.exe

Achtung: Speichert den Schlüssel im echten Eintrag der Windows-Anmeldeinformationsverwaltung
(„WIDS-Access-AI/api-key-anthropic“) – genau so, wie es ein Benutzer in der EXE tun würde.
Einstellungen und Verlauf landen in einem temporären Ordner.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

import psutil
from pywinauto import Desktop

TITLE = re.compile(r"^WIDS ACCESS AI \d")


def hidden(text: str) -> str:
    return re.sub(r"sk-ant\S*|sk-a\S*", "sk-…", text)


def wait_for(predicate, timeout: float, what: str):  # type: ignore[no-untyped-def]
    end = time.time() + timeout
    while time.time() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    raise TimeoutError(f"Zeitüberschreitung: {what}")


def find(window, title: str, control_type: str):  # type: ignore[no-untyped-def]
    return wait_for(lambda: next((c for c in window.descendants(control_type=control_type)
                                  if title in (c.window_text(), c.element_info.name)), None),
                    15, title)


def texts(window) -> list[str]:  # type: ignore[no-untyped-def]
    return [t.window_text() for t in window.descendants(control_type="Text")]


def status(window) -> str:  # type: ignore[no-untyped-def]
    for text in texts(window):
        if text.startswith(("Anthropic Claude verbunden", "Test fehlgeschlagen", "Nicht gespeichert",
                            "Gespeicherter Schlüssel", "Kein Schlüssel", "Verbindung wird getestet")):
            return text
    return ""


def start(exe: str, env: dict[str, str]):  # type: ignore[no-untyped-def]
    """Startet die EXE und liefert ihr Hauptfenster – andere, schon laufende WIDS-Instanzen bleiben unberührt."""
    launcher = subprocess.Popen([exe], env=env)
    desktop = Desktop(backend="uia")

    def ours():  # type: ignore[no-untyped-def]
        family = {launcher.pid} | {c.pid for c in psutil.Process(launcher.pid).children(recursive=True)}
        return next((w for w in desktop.windows() if TITLE.match(w.window_text()) and w.process_id() in family), None)

    window = wait_for(ours, 90, "Hauptfenster")
    window.launcher = launcher
    return window


def stop(window) -> None:  # type: ignore[no-untyped-def]
    window.close()
    window.launcher.wait(30)  # die PyInstaller-EXE endet erst, wenn das Programm selbst beendet ist


def test_connection(window) -> str:  # type: ignore[no-untyped-def]
    find(window, "Verbindung testen", "Button").click_input()
    return wait_for(lambda: (s := status(window)) and not s.startswith("Verbindung wird") and
                    not s.startswith("Gespeicherter") and s, 90, "Ergebnis des Verbindungstests")


def main() -> int:
    exe = os.path.abspath(sys.argv[1])
    key = os.environ.pop("ANTHROPIC_API_KEY")  # die EXE bekommt den Schlüssel NUR über die Oberfläche
    env = {**os.environ, "WIDS_DATA_DIR": tempfile.mkdtemp(prefix="wids-exe-check-")}

    window = start(exe, env)
    pid = window.process_id()
    # der Willkommensdialog ist ein Kindfenster des Hauptfensters
    ok_button = wait_for(lambda: next((b for b in window.descendants(control_type="Button") if b.window_text() == "OK"), None),
                         30, "Willkommensdialog")
    ok_button.click_input()
    field = find(window, "API-Schlüssel", "Edit")
    field.click_input()
    field.type_keys(key, with_spaces=False, set_foreground=True)
    first = test_connection(window)  # ohne vorher „Schlüssel speichern“ – der frühere Fehlerfall
    print("1. Start, Schlüssel eingegeben, Verbindung testen:", hidden(first))
    stop(window)
    print("   Programm vollständig beendet:", not psutil.pid_exists(pid))

    window = start(exe, env)
    find(window, "Einstellungen", "ListItem").click_input()
    loaded = wait_for(lambda: status(window), 15, "Schlüsselstatus")
    print("2. Neustart, Einstellungen:", hidden(loaded))
    second = test_connection(window)
    print("   Verbindung testen mit gespeichertem Schlüssel:", hidden(second))
    stop(window)
    ok = first.startswith("Anthropic Claude verbunden") and loaded.startswith("Gespeicherter Schlüssel") \
        and second.startswith("Anthropic Claude verbunden")
    print("ERGEBNIS:", "OK" if ok else "FEHLER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

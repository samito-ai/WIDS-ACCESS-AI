"""Einzelinstanz (Mutex + lokaler Socket) und Autostart-Eintrag (HKCU\\…\\Run)."""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.core import autostart
from app.core.single_instance import SingleInstance

ROOT = Path(__file__).resolve().parents[2]
SECOND_START = """
import sys
from PySide6.QtCore import QCoreApplication
from app.core.single_instance import SingleInstance
app = QCoreApplication([])
other = SingleInstance(sys.argv[1])
sys.exit(3 if other.acquire() else (0 if other.send("quit") else 4))
"""


@pytest.mark.gui
def test_second_instance_is_refused_and_forwards_command(qtbot: Any) -> None:
    name = f"WIDS-test-{uuid.uuid4().hex[:8]}"
    first = SingleInstance(name)
    try:
        assert first.acquire()
        with qtbot.waitSignal(first.command, timeout=30000) as blocker:
            # zweiter Programmstart als eigener Prozess – wie ein Doppelklick auf die EXE
            second = subprocess.Popen([sys.executable, "-c", SECOND_START, name], cwd=ROOT)
        assert second.wait(30) == 0, "zweiter Start erkennt die laufende Instanz und endet"
        assert blocker.args == ["quit"]
    finally:
        first.release()
    third = SingleInstance(name)
    assert third.acquire(), "nach dem Beenden ist der Start wieder frei"
    third.release()


@pytest.mark.gui
def test_send_without_running_instance_gives_up(qapp: Any) -> None:
    assert not SingleInstance(f"WIDS-test-{uuid.uuid4().hex[:8]}").send("show", attempts=1)


def test_autostart_entry_is_written_and_removed() -> None:
    name = f"WIDS-Access-AI-TEST-{uuid.uuid4().hex[:8]}"
    try:
        autostart.set_enabled(True, name=name, cmd=r'"C:\nicht\vorhanden.exe" --background')
        assert autostart.get(name) == r'"C:\nicht\vorhanden.exe" --background'
    finally:
        autostart.set_enabled(False, name=name)
    assert autostart.get(name) is None
    autostart.set_enabled(False, name=name)  # erneutes Entfernen ist harmlos


def test_autostart_needs_built_exe() -> None:
    assert autostart.command() is None, "aus dem Quellcode gestartet gibt es keinen festen Programmpfad"
    with pytest.raises(OSError):
        autostart.set_enabled(True, name="WIDS-Access-AI-TEST-never")


def test_autostart_works_when_the_run_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""GitHub-Runner 16.11.: auf frischen Windows-Profilen fehlt HKCU\…\Run – Autostart muss ihn anlegen.
    Getestet mit einem eigenen, temporären Schlüssel (der echte Autostart bleibt unberührt)."""
    import winreg

    base = rf"Software\WIDS-Access-AI-Test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(autostart, "RUN_KEY", base + r"\Run")
    try:
        autostart.set_enabled(False, name="X")  # fehlender Schlüssel: Entfernen ist harmlos
        autostart.set_enabled(True, name="X", cmd=r'"C:\Test\wids.exe" --background')
        assert autostart.get("X") == r'"C:\Test\wids.exe" --background'
    finally:
        for key in (base + r"\Run", base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass

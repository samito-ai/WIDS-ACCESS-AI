"""Start mit Windows: Eintrag unter HKCU\\…\\Run (nur für den angemeldeten Benutzer, ohne Adminrechte)."""
from __future__ import annotations

import sys
import winreg

from app import APP_ID

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> str | None:
    """Befehl für den Autostart – nur für die gebaute EXE (ein Start aus dem Quellcode hat keinen festen Pfad)."""
    return f'"{sys.executable}" --background' if getattr(sys, "frozen", False) else None


def get(name: str = APP_ID) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def set_enabled(enabled: bool, *, name: str = APP_ID, cmd: str | None = None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            value = cmd or command()
            if value is None:
                raise OSError("Autostart ist nur mit der gebauten EXE möglich.")
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass

"""Globale Tastenkürzel über die Windows-Funktion RegisterHotKey."""
from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

from app.core.errors import HotkeyError
from app.core.logging_setup import get_logger

log = get_logger(__name__)

MOD = {"alt": 0x1, "ctrl": 0x2, "strg": 0x2, "shift": 0x4, "umschalt": 0x4, "win": 0x8, "meta": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_KEYS = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "leertaste": 0x20, "tab": 0x09, "enter": 0x0D,
         **{f"f{n}": 0x6F + n for n in range(1, 13)}}
#: Kombinationen, die Windows/Office selbst braucht – werden abgelehnt
RESERVED = {"alt+f4", "alt+tab", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+s", "ctrl+a", "win+l", "win+d",
            "ctrl+alt+entf", "alt+esc", "ctrl+esc"}


#: Tasten, die auf der deutschen Tastatur mit AltGr (= Strg+Alt) ein Zeichen erzeugen
ALTGR_KEYS = set("qem237890")


def parse(combo: str) -> tuple[int, int]:
    """``"Alt+W"`` -> (Modifikatoren, virtueller Tastencode). Wirft ``HotkeyError``."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("Das Tastenkürzel ist leer.")
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod not in MOD:
            raise HotkeyError(f"Unbekannte Zusatztaste „{mod}“ in „{combo}“.")
        modifiers |= MOD[mod]
    # Vergleich in kanonischer Form: Qt liefert „Meta“ für Win und „Escape“; Reihenfolge und Doppelte egal
    canon = "+".join([n for b, n in ((2, "ctrl"), (1, "alt"), (4, "shift"), (8, "win")) if modifiers & b]
                     + [{"escape": "esc", "del": "entf"}.get(key, key)])
    if canon in RESERVED:
        raise HotkeyError(f"„{combo}“ ist von Windows reserviert.")
    if key in _KEYS:
        if not modifiers and not key.startswith("f"):  # Esc/Tab/Enter/Leertaste allein fehlen sonst in jedem Programm
            raise HotkeyError(f"„{combo}“ braucht eine Zusatztaste (Alt, Strg, Umschalt oder Win).")
        return modifiers, _KEYS[key]
    if len(key) == 1 and key.isalnum():
        if not modifiers:
            raise HotkeyError(f"„{combo}“ braucht eine Zusatztaste (Alt, Strg, Umschalt oder Win).")
        if modifiers == 0x3 and key in ALTGR_KEYS:
            raise HotkeyError(f"„{combo}“ entspricht AltGr+{key.upper()} – damit tippt man auf der deutschen Tastatur "
                              "ein Sonderzeichen (z. B. @, €, {). Bitte eine andere Taste wählen.")
        return modifiers, ord(key.upper())
    raise HotkeyError(f"Unbekannte Taste „{key}“ in „{combo}“.")


class HotkeyManager:
    """Registriert Kürzel in einem eigenen Thread mit Nachrichtenschleife.

    ``callback(name)`` wird aus diesem Thread aufgerufen – für Qt also ein Signal auslösen.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.failed: dict[str, str] = {}

    def start(self, bindings: dict[str, str]) -> dict[str, str]:
        """Registriert ``{name: kombination}``; liefert ``{name: Fehlermeldung}`` für abgelehnte Kürzel."""
        self.stop()
        self.failed = {}
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(dict(bindings), ready), name="wids-hotkeys", daemon=True)
        self._thread.start()
        ready.wait(5)
        return dict(self.failed)

    def _run(self, bindings: dict[str, str], ready: threading.Event) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        names: dict[int, str] = {}
        seen: dict[tuple[int, int], str] = {}
        for index, (name, combo) in enumerate(bindings.items(), start=1):
            try:
                modifiers, key = parse(combo)
                if (modifiers, key) in seen:
                    raise HotkeyError(f"„{combo}“ ist bereits für „{seen[(modifiers, key)]}“ vergeben.")
                if not user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, key):
                    raise HotkeyError(f"„{combo}“ wird bereits von einem anderen Programm verwendet.")
                names[index], seen[(modifiers, key)] = name, name
            except HotkeyError as exc:
                self.failed[name] = exc.user_message
        ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY and message.wParam in names:
                try:
                    self._callback(names[message.wParam])
                except Exception:
                    log.exception("Fehler im Hotkey-Callback")
        for index in names:
            user32.UnregisterHotKey(None, index)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None

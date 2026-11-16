"""WIDS ACCESS AI - KI-Assistent für Microsoft Access und te.comp-Übungen."""

APP_NAME = "WIDS ACCESS AI"
APP_ID = "WIDS-Access-AI"
__version__ = "1.0.0b2"


def _private_win32com_cache() -> None:
    """Eigener, leerer win32com-Typcache für WIDS.

    Andere Programme legen in ``%TEMP%\\gen_py`` statische Typklassen für Access an
    (``gencache.EnsureDispatch``). pywin32 würde sie sonst automatisch verwenden – dort fehlen z. B.
    ``Control.Caption``, und Formulare/Berichte lassen sich nicht mehr erstellen. WIDS bindet COM immer
    dynamisch und erzeugt selbst nie statische Klassen; der eigene Ordner bleibt deshalb leer.
    Muss laufen, bevor ``win32com.client`` importiert wird (``app`` wird immer zuerst importiert).
    """
    try:
        import os
        import sys
        import tempfile

        import win32com

        path = os.path.join(tempfile.gettempdir(), f"{APP_ID}-gen_py", f"{sys.version_info[0]}.{sys.version_info[1]}")
        os.makedirs(path, exist_ok=True)
        win32com.__gen_path__ = path
        gen_py = sys.modules.get("win32com.gen_py")
        if gen_py is not None:
            gen_py.__path__ = [path]
    except Exception:  # ohne pywin32 (z. B. Werkzeuge, die nur Modelle laden) ist nichts zu tun
        pass


_private_win32com_cache()

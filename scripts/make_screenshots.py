"""Screenshots für die README erzeugen – unsichtbar (offscreen), mit leerem Testdatenordner und ohne API-Schlüssel,
damit keine persönlichen Daten (Verlauf, Pfade, Schlüssel) auf den Bildern landen.

    python scripts\\make_screenshots.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["WIDS_DATA_DIR"] = tempfile.mkdtemp(prefix="wids-screens-")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.core.context import AppContext
    from app.security.secrets import MemoryBackend, SecretStore
    from app.storage.database import Storage
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ctx = AppContext(Storage(Path(os.environ["WIDS_DATA_DIR"]) / "screens.sqlite3"), SecretStore(MemoryBackend()))
    ctx.settings.hotkeys.enabled = False
    from app.vision import tecomp

    tecomp.open_access_database = lambda: ""  # keine echten Fenster
    from PySide6.QtCore import Qt

    window = MainWindow(ctx)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # echte Schriften, aber kein sichtbares Fenster
    window.resize(1280, 820)
    window.show()
    from PySide6.QtWidgets import QLabel

    for label in window.findChildren(QLabel):  # Logpfad enthält den Windows-Benutzernamen – im Bild neutralisieren
        if "Logdatei:" in label.text():
            label.setText(label.text().split("Logdatei:")[0] + "Logdatei: %LOCALAPPDATA%\\WIDS-Access-AI\\logs\\wids.log")
    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    for index, name in ((0, "dashboard"), (1, "aufgabe"), (5, "einstellungen")):
        window.nav.setCurrentRow(index)
        app.processEvents()
        window.grab().save(str(out / f"{name}.png"))
        print("gespeichert:", out / f"{name}.png")
    window.quit_app()
    ctx.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

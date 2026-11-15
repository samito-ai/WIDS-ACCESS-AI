"""Erstellt ``dist/WIDS-Access-AI.exe`` mit PyInstaller.

Aufruf:  python build.py
Die Zwischendateien liegen außerhalb des Projektordners (OneDrive soll sie nicht synchronisieren).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = Path(tempfile.gettempdir()) / "WIDS-Access-AI-build"
EXE = ROOT / "dist" / "WIDS-Access-AI.exe"


def main() -> int:
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "WIDS-Access-AI",
        "--distpath", str(ROOT / "dist"), "--workpath", str(WORK), "--specpath", str(WORK),
        "--paths", str(ROOT),
        "--collect-submodules", "winrt",      # Windows-OCR (native Erweiterungsmodule)
        "--hidden-import", "win32timezone",   # von pywin32 für Datumswerte nachgeladen
        "--exclude-module", "tkinter", "--exclude-module", "pytest",
        str(ROOT / "app" / "main.py"),
    ])
    # Selbsttest der fertigen EXE in einem leeren Datenverzeichnis (berührt keine Benutzerdaten)
    env = {**os.environ, "WIDS_DATA_DIR": tempfile.mkdtemp(prefix="wids-selftest-")}
    code = subprocess.run([str(EXE), "--selftest"], env=env, timeout=180, check=False).returncode
    print(f"{EXE} ({EXE.stat().st_size / 1_000_000:.0f} MB) – Selbsttest: {'OK' if code == 0 else f'FEHLER (Code {code})'}")
    return code


if __name__ == "__main__":
    sys.exit(main())

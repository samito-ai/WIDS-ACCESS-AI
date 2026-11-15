"""Excel-Datei nach einem Export nachbearbeiten (te.comp 0205/U4: Textumbruch aus, optimale Spaltenbreite).

Formatiert wird in einer EIGENEN, unsichtbaren Excel-Instanz (``DispatchEx``) – eine vom Benutzer geöffnete
Excel-Sitzung wird nie verwendet. Die Prüfung liest das Ergebnis unabhängig davon direkt aus der xlsx-Datei.
"""
from __future__ import annotations

import re
import threading
import zipfile
from pathlib import Path
from typing import Any

from app.core.errors import AccessError
from app.core.logging_setup import get_logger

log = get_logger(__name__)


def format_workbook(path: Path, *, wrap_text: bool | None = None, autofit: bool = False) -> None:
    """Textumbruch der benutzten Zellen setzen und/oder Spaltenbreiten optimal anpassen (alle Blätter).
    Läuft in einem eigenen Thread mit eigener COM-Initialisierung: ein CoUninitialize im Access-Thread
    trennt sonst die Access-Verbindung (gemessen: „CurrentDb.TableDefs“ danach unbekannt)."""
    errors: list[BaseException] = []
    pids: list[tuple[int, float]] = []  # (PID, Startzeit) – schützt vor wiederverwendeten PIDs

    def work() -> None:
        try:
            _format(path, wrap_text, autofit, pids)
        except BaseException as exc:  # im Aufrufer-Thread erneut auslösen
            errors.append(exc)

    worker = threading.Thread(target=work, name="WIDS-Excel", daemon=True)
    worker.start()
    worker.join(timeout=180)
    if worker.is_alive():
        _kill_own_excel(pids)
        raise AccessError(f"Excel reagiert nicht – „{path.name}“ wurde nicht formatiert.")
    if errors:
        raise errors[0]
    log.info("Excel-Datei formatiert: %s", path.name)


def _kill_own_excel(pids: list[tuple[int, float]]) -> None:
    """Nur die selbst gestartete, hängende Excel-Instanz beenden (nie eine Excel-Sitzung des Benutzers)."""
    import psutil

    for pid, started in pids:
        try:
            proc = psutil.Process(pid)
            if proc.name().lower() == "excel.exe" and proc.create_time() == started:
                proc.kill()
                log.warning("Hängende eigene Excel-Instanz beendet (PID %s)", pid)
        except Exception as exc:
            log.debug("Excel-Instanz %s nicht beendet: %s", pid, exc)


def _format(path: Path, wrap_text: bool | None, autofit: bool, pids: list[tuple[int, float]]) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        try:
            xl: Any = win32com.client.DispatchEx("Excel.Application")
        except Exception as exc:
            raise AccessError("Excel ist nicht installiert – die Excel-Formatierung ist nicht möglich.") from exc
        try:
            import psutil
            import win32process

            pid = int(win32process.GetWindowThreadProcessId(int(xl.Hwnd))[1])  # eigene Instanz (DispatchEx)
            pids.append((pid, psutil.Process(pid).create_time()))
            xl.Visible = False
            xl.DisplayAlerts = False
            book = xl.Workbooks.Open(str(path))
            try:
                for index in range(1, int(book.Worksheets.Count) + 1):
                    used = book.Worksheets(index).UsedRange
                    if wrap_text is not None:
                        used.WrapText = wrap_text
                    if autofit:
                        used.Columns.AutoFit()
                    used = None
                book.Save()
            finally:
                book.Close(False)
                book = None
        except AccessError:
            raise
        except Exception as exc:
            raise AccessError(f"Excel-Formatierung von „{path.name}“ fehlgeschlagen: {exc}") from exc
        finally:
            xl.Quit()
            xl = None
    finally:
        pythoncom.CoUninitialize()


def workbook_layout(path: Path) -> dict[str, Any]:
    """Erstes Blatt direkt aus der xlsx-Datei: Zellen mit Textumbruch, Spaltenbreiten, längster Text je Spalte."""
    from app.access import importer

    with zipfile.ZipFile(path) as archive:
        sheet_name = sorted(n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[0]
        sheet = archive.read(sheet_name).decode("utf-8")
        styles = archive.read("xl/styles.xml").decode("utf-8") if "xl/styles.xml" in archive.namelist() else ""
    cell_xfs = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>", styles, re.S)
    xfs = re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", cell_xfs.group(1), re.S) if cell_xfs else []
    wrapping = {i for i, xf in enumerate(xfs) if re.search(r'wrapText="(1|true)"', xf)}
    wrapped = sum(1 for s in re.findall(r'<c\b[^>]*\bs="(\d+)"', sheet) if int(s) in wrapping)
    widths: dict[int, float] = {}
    for attrs in re.findall(r"<col\b([^>]*)/>", sheet):
        lo, hi, width = (re.search(rf'\b{k}="([\d.]+)"', attrs) for k in ("min", "max", "width"))
        if lo and hi and width:
            for col in range(int(lo.group(1)), int(hi.group(1)) + 1):
                widths[col] = float(width.group(1))
    longest: dict[int, int] = {}  # nur Text zählt; Zahlen/Datum zeigt Excel formatiert an
    for row in importer._read_xlsx(path, None):  # roh: leere Spalten bleiben, Spaltennummern stimmen
        for col, value in enumerate(row, start=1):
            if isinstance(value, str) and value:
                longest[col] = max(longest.get(col, 0), len(value))
            elif value not in (None, ""):
                longest.setdefault(col, 0)
    return {"wrapped_cells": wrapped, "widths": widths, "longest_text": longest}

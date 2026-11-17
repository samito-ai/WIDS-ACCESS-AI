"""te.comp-training: Aufgabentext der geöffneten Access-Übung.

te.comp öffnet jede Access-Übung als ``<Kapitel><Übung>_<Nummer>.accdb`` (z. B. ``04024_12345678.accdb``
= Kapitel 0402, Übung 4) und zeigt dazu die Aufgabenseite ``access\\cbt\\0402\\intro\\U4\\task.html`` aus
dem Installationsordner an. Diese Seite ist eine normale, lesbare HTML-Datei – derselbe Text, den
te.comp im Fenster zeigt. Das ist zuverlässiger als OCR. Andere Dateinamen (z. B. Selbsttests) werden
bewusst nicht zugeordnet; dafür gibt es den OCR-Weg.
"""
from __future__ import annotations

import html
import re
import threading
import time
from pathlib import Path

from app.core.logging_setup import get_logger
from app.vision.capture import TECOMP_PROCESS, visible_windows

log = get_logger(__name__)

# Kapitel immer 4-stellig, Übung 1- oder 2-stellig (Kapitel 0403 hat U1…U11)
EXERCISE_FILE = re.compile(r"(?<![\w-])(\d{4})(\d{1,2})_\d+\.(?:accdb|mdb)\b", re.IGNORECASE)
#: Übung zu einer Bildschirminfo: „0501S2_…accdb“ = Kapitel 0501, Bildschirminfo SI-02 (screeninfo\SI-02a.htm)
SCREENINFO_FILE = re.compile(r"(?<![\w-])(\d{4})S(\d{1,2})_\d+\.(?:accdb|mdb)\b", re.IGNORECASE)
DEFAULT_INSTALL = Path(r"C:\Program Files (x86)\te.comp\te.comp-training")
_NAVIGATION = {"Überblick", "Zu Access"}  # Navigationsbeschriftungen der Aufgabenseite


def install_dir() -> Path | None:
    import psutil

    for proc in psutil.process_iter(["name", "exe"]):
        if (proc.info["name"] or "").lower() == TECOMP_PROCESS and proc.info["exe"]:
            return Path(proc.info["exe"]).parent
    return DEFAULT_INSTALL if DEFAULT_INSTALL.exists() else None


def read_page(page: Path) -> str:
    """te.comp-Seiten sind teils UTF-8, teils Windows-1252 (Bildschirminfos) – Umlaute bleiben so erhalten."""
    raw = page.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def screeninfo_page(db_name: str, base: Path | None = None) -> Path | None:
    """Bildschirminfo zu einer SI-Übungsdatei (oder ``None``) – erklärt, WO und WIE die Aufgabe gemeint ist."""
    match = SCREENINFO_FILE.search(db_name)
    base = base or (install_dir() if match else None)
    if match is None or base is None:
        return None
    folder = base / "access" / "cbt" / match.group(1) / "screeninfo"
    number = int(match.group(2))
    return next((p for p in (folder / f"SI-{number:02d}a.htm", folder / f"SI-{number:02d}.htm") if p.exists()), None)


def task_page(db_name: str, base: Path | None = None) -> Path | None:
    """Aufgabenseite zu einem te.comp-Übungsdateinamen (oder ``None``)."""
    match = EXERCISE_FILE.search(db_name)
    base = base or install_dir()
    if match is None or base is None:
        return None
    page = base / "access" / "cbt" / match.group(1) / "intro" / f"U{match.group(2)}" / "task.html"
    return page if page.exists() else None


#: te.comp hängt an Workshop-Dateien eine Nummer an: „CD-Sammlung_12345678.accdb“ = Vorlage „CD-Sammlung.accdb“
_SESSION_SUFFIX = re.compile(r"_\d+$")


def workshop_for(db_path: str, base: Path | None = None) -> Path | None:
    """Ordner des te.comp-Workshops zu einer Datenbankdatei – nur bei EINDEUTIGER Zuordnung, sonst ``None``.

    Gesucht wird ``access\\workshops\\<Name>``, dessen Ordnername oder Vorlagendatei (``<Name>.accdb``) dem Dateinamen
    ohne angehängte Nummer entspricht. Mehrdeutig (z. B. zwei Workshops mit derselben Vorlage) = keine Zuordnung.
    """
    if not db_path:
        return None
    base = base or install_dir()
    root = base / "access" / "workshops" if base is not None else None
    stem = _SESSION_SUFFIX.sub("", Path(db_path).stem).strip().lower()
    if root is None or not root.is_dir() or not stem:
        return None
    hits = [d for d in root.iterdir() if d.is_dir() and (
        d.name.lower() == stem or any(f.stem.lower() == stem for f in d.glob("*.accdb")))]
    return hits[0] if len(hits) == 1 else None


def workshop_context(folder: Path, limit: int = 1500) -> str:
    """Kurzbeschreibung des Workshops aus seinen Dateien (Einführung und Aufgabe) – Hintergrund, nicht der aktuelle
    Arbeitsschritt (den zeigt nur das te.comp-Fenster)."""
    parts = []
    for name in ("intro.html", "task.html"):
        page = folder / "intro" / name
        if page.is_file():
            parts.append(html_to_text(page.read_text(encoding="utf-8", errors="replace")))
    return "\n".join(p for p in parts if p)[:limit]


#: te.comp schreibt die geforderten Objektnamen in dieser Farbe („Namen in blauer Schrift“)
_BLUE_NAME = re.compile(r'(?is)<span[^>]*color\s*:\s*#4f85e4[^>]*>(.*?)</span>')


def blue_names(raw: str) -> list[str]:
    """Die in te.comp blau geschriebenen Objektnamen der Aufgabenseite (Reihenfolge wie im Text, ohne Doppelte)."""
    names: list[str] = []
    for fragment in _BLUE_NAME.findall(re.sub(r"(?is)<!--.*?-->", " ", raw)):
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xad", "").split()).strip(" :.,")
        if name and name not in names:
            names.append(name)
    return names


def html_to_text(raw: str) -> str:
    """Aufgabentext der Seite. Blau geschriebene Objektnamen werden am Ende zusätzlich ausdrücklich aufgelistet,
    weil die Farbe als Text verloren geht („speichern Sie unter dem Namen in blauer Schrift“)."""
    names = blue_names(raw)
    text = re.sub(r"(?is)<!--.*?-->|<(script|style)\b.*?</\1>", " ", raw)  # Kommentare = veraltete Hinweise
    text = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr)>", "\n", text)
    # Inline-Auszeichnung ohne Leerzeichen entfernen: aus "<span><b>Spielerstatus</b></span>" wird "Spielerstatus"
    text = re.sub(r"(?i)</?(span|a|font|i|em|u)\b[^>]*>", "", text)
    # Die geforderten Objektnamen stehen fett („in blauer Schrift“) – ohne Anführungszeichen als „Name“ markieren
    text = re.sub(r'(?is)(?<!["„])<(b|strong)\b[^>]*>([^<>]+?)</\1>(?!["“])',
                  lambda m: m.group(2) if m.group(2).strip().endswith(":") else f"„{m.group(2).strip()}“", text)
    text = re.sub(r"(?i)</?(b|strong)\b[^>]*>", "", text)
    # BOM und weiche Trennstriche (&shy;) entfernen – sonst stecken sie mitten in Objektnamen
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("﻿", "").replace("\xad", "")
    lines = (" ".join(line.split()) for line in text.splitlines())
    result = "\n".join(line for line in lines if line and line not in _NAVIGATION)
    if names:
        result += "\nObjektnamen in blauer Schrift (exakt so verwenden): " + ", ".join(f"„{n}“" for n in names)
    return result


def task_images(page: Path, limit: int = 3) -> list[bytes]:
    """Abbildungen der Aufgabenseite (gewünschtes Ergebnis, Formularlayout, Datenauszug) als PNG.

    Das ist Übungsmaterial aus dem te.comp-Installationsordner – kein Bildschirmfoto. Symbole (icon*) und sehr kleine
    Grafiken werden übersprungen; große Bilder werden verkleinert (spart Tokens, reicht zum Lesen).
    """
    import io

    from PIL import Image

    raw = read_page(page)
    root = page.parent.resolve()
    result: list[bytes] = []
    seen: set[Path] = set()
    for src in re.findall(r'(?i)<(?:img\b[^>]*?\bsrc|a\b[^>]*?\bhref)\s*=\s*"([^"]+\.(?:jpe?g|png|gif|bmp))"', raw):
        path = (page.parent / html.unescape(src).replace("/", "\\")).resolve()
        if path in seen or not path.is_file() or root not in path.parents or path.name.lower().startswith("icon"):
            continue
        seen.add(path)
        if path.stat().st_size < 8_000:
            continue
        try:
            with Image.open(path) as image:
                image.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="PNG", optimize=True)
        except OSError:
            continue
        result.append(buffer.getvalue())
        if len(result) >= limit:
            break
    return result


#: Ergebnis der Erreichbarkeitsprüfung je Ordner: (Zeitpunkt, erreichbar)
_REACHABLE: dict[str, tuple[float, bool]] = {}
REACHABLE_TIMEOUT_S = 1.5
REACHABLE_CACHE_S = 60.0


def reachable(folder: Path) -> bool:
    """``is_dir`` mit Zeitlimit und kurzem Cache: ein getrenntes oder langsames Netzlaufwerk (z. B. W:) blockiert
    Windows sonst bis zu einer Minute. Nicht rechtzeitig geantwortet = nicht erreichbar."""
    key, now = str(folder).lower(), time.monotonic()
    cached = _REACHABLE.get(key)
    if cached is not None and now - cached[0] < REACHABLE_CACHE_S:
        return cached[1]
    answer: list[bool] = []
    worker = threading.Thread(target=lambda: answer.append(folder.is_dir()), daemon=True)
    worker.start()
    worker.join(REACHABLE_TIMEOUT_S)
    ok = bool(answer and answer[0])
    _REACHABLE[key] = (now, ok)
    return ok


def workdirs() -> list[Path]:
    """Arbeitsordner von te.comp (Quelldateien von Workshops, z. B. ``…\\workdirs\\<Datum>\\access\\<Nr>\\WS01``)
    – neuester Tag zuerst – und das te.comp-Arbeitslaufwerk W:, falls verbunden."""
    base = Path.home() / "te.comp" / "te.comp-training" / "workdirs"
    dirs: list[Path] = []
    if base.is_dir():
        for day in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True)[:2]:
            dirs += sorted((d for d in day.glob("access/*/*") if d.is_dir() and not d.name.endswith(".backups")),
                           key=lambda d: d.stat().st_mtime, reverse=True)
    work_drive = Path("W:\\")
    if reachable(work_drive):
        dirs.append(work_drive)
    return dirs


#: Datenbankpfad im Access-Fenstertitel – Laufwerksbuchstabe ODER Netzwerkpfad (\\Server\Freigabe\…)
_TITLE_PATH = re.compile(r"((?:[A-Za-z]:|\\\\[^\\/:*?\"<>|\r\n]+\\[^\\/:*?\"<>|\r\n]+)\\[^\r\n]*?\.(?:accdb|mdb))\b",
                         re.IGNORECASE)


def database_in_title(title: str) -> str:
    """Datenbankpfad aus einem Access-Fenstertitel (oder "")."""
    found = _TITLE_PATH.search(title)
    return found.group(1) if found else ""


def _front_access() -> tuple[str, str] | None:
    """(Datenbankpfad, Fenstertitel) des vordersten Access-Fensters mit geöffneter Datenbank."""
    for window in visible_windows():
        if window.process.lower() != "msaccess.exe":
            continue
        if path := database_in_title(window.title):
            return path, window.title
        # Access kürzt lange Pfade (tiefe Ordner, OneDrive, Netzlaufwerke) im Titel: dann genau diese Instanz fragen
        if window.cls == "OMain" and (path := _path_of_access_window(window.hwnd)):
            return path, window.title
    return None


def _path_of_access_window(hwnd: int, timeout: float = 5.0) -> str:
    """Pfad der Datenbank in der Access-Instanz mit diesem Hauptfenster – über die Running Object Table, in einem
    eigenen COM-Thread (der aufrufende Thread hat COM ggf. nicht initialisiert)."""

    found: list[str] = []

    def lookup() -> None:
        from app.access.session import current_database_path, running_access_instances

        for app in running_access_instances():
            try:
                if int(app.hWndAccessApp()) == hwnd:
                    found.append(current_database_path(app) or "")
                    return
            except Exception:
                continue

    def work() -> None:
        import pythoncom

        pythoncom.CoInitialize()
        try:
            lookup()  # COM-Objekte sind nach Rückkehr freigegeben – erst dann CoUninitialize
        except Exception as exc:
            log.debug("Access-Pfad über COM nicht ermittelbar: %s", exc)
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=work, name="wids-access-pfad", daemon=True)
    thread.start()
    thread.join(timeout)
    return found[0] if found else ""


def open_access_database() -> str:
    """Pfad der Datenbank im vordersten Access-Fenster – egal wie die Datei heißt."""
    front = _front_access()
    return front[0] if front else ""


def in_tecomp_workdir(db_path: str) -> bool:
    """te.comp legt seine Übungsdateien direkt in %USERPROFILE%\\te.comp\\te.comp-training ab."""
    import os

    workdir = Path.home() / "te.comp" / "te.comp-training"
    return bool(db_path) and os.path.normcase(os.path.abspath(Path(db_path).parent)) == os.path.normcase(str(workdir))


_EXERCISE_NAME = re.compile(r"\d{4}\d{1,2}_\d+\.(?:accdb|mdb)", re.IGNORECASE)


def open_exercise() -> tuple[str, str] | None:
    """(Datenbankpfad, Fenstertitel) der te.comp-Übung im VORDERSTEN Access-Fenster – nur wenn ihr Dateiname exakt
    dem Übungsmuster entspricht und sie direkt im te.comp-Arbeitsordner liegt. Eine eigene Datei, die zufällig so
    heißt (``C:\\Eigene\\02011_7.accdb``, ``Kopie 02011_7.accdb``), bekommt nie den te.comp-Aufgabentext; liegt ein
    Workshop oder eine andere Datenbank vorne, gilt diese."""
    front = _front_access()
    if front and in_tecomp_workdir(front[0]) and _EXERCISE_NAME.fullmatch(Path(front[0]).name):
        return front
    return None

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def workshop_context(folder: Path, limit: int = 4000) -> str:
    """Hintergrund des Workshops aus seinen lesbaren Dateien: Einführung, Aufgabe und die Hilfeseiten („Weitere Infos“,
    „Lösungstipp“ der Teilaufgaben) – nicht die Teilaufgaben selbst (die zeigt nur die te.comp-Aufgabenleiste)."""
    parts = []
    intro = folder / "intro"
    for page in [intro / "intro.html", intro / "task.html", *sorted(intro.glob("task-*.html"))]:
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


def is_tecomp_exercise(db_path: str) -> bool:
    """Echte te.comp-Übung: liegt im te.comp-Arbeitsordner UND trägt einen Übungs-/Bildschirminfo-Namen oder gehört zu
    einem Workshop – eine beliebige eigene Datei in diesem Ordner zählt nicht."""
    if not in_tecomp_workdir(db_path):
        return False
    name = Path(db_path).name
    return bool(_EXERCISE_NAME.fullmatch(name) or SCREENINFO_FILE.fullmatch(name) or workshop_for(db_path))


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


# ------------------------------------------------------------------ Workshop: alle Teilaufgaben auf einmal
#: In der te.comp-Aufgabenleiste ist jede Teilaufgabe eines Workshops ein eigenes Fenster dieser Klasse. Lesbar (per
#: OCR) sind nur die ~3 Karten im sichtbaren Ausschnitt; die übrigen erreicht te.comp über die Pfeile links und rechts
#: der Leiste. Die Schrittliste selbst liegt nur verschlüsselt vor (WS.epp) – sie wird bewusst nicht entschlüsselt.
STEP_CLASS = "TInstructionTab"
_FINAL_BUTTONS = {"Prüfen", "Nächste Aufgabe", "Abschließen"}
#: „1. Die CD-Tabelle: …“ – davor darf OCR ein Kästchen-Symbol als „o“ lesen
_STEP_NUMBER = re.compile(r"^[^\d]{0,4}?(\d{1,2})\.\s+")


@dataclass
class WorkshopSteps:
    steps: list[str]  # „1. …“, „2. …“ in te.comp-Reihenfolge
    notes: list[str]  # Karten ohne Nummer VOR der ersten Teilaufgabe (Ausgangssituation, „Informieren Sie sich“)
    complete: bool
    problem: str = ""
    restored: bool = True  # Aufgabenleiste wieder an der Ausgangsposition


def parse_steps(cards: list[str]) -> WorkshopSteps:
    """Karten der Aufgabenleiste → nummerierte Teilaufgaben und Hinweise. Hinweise stehen nur VOR der ersten
    Teilaufgabe; eine Karte ohne erkennbare Nummer danach ist eine Teilaufgabe, deren Nummer die Texterkennung
    verloren hat. Vollständig nur, wenn jede Karte lesbar war und die Nummern lückenlos bei 1 beginnen."""
    steps: list[str] = []
    notes: list[str] = []
    numbers: list[int] = []
    problem = ""
    for raw in cards:
        # Silbentrennung am Zeilenende wieder zusammenfügen: „Sparvereins-⏎bewegungen“ → „Sparvereinsbewegungen“
        text = " ".join(re.sub(r"(\w)-[ \t]*\r?\n\s*(?=[a-zäöüß])", r"\1", raw).split())
        if not text:
            problem = problem or "Mindestens eine Teilaufgabe war nicht lesbar."
        elif match := _STEP_NUMBER.match(text):
            numbers.append(int(match.group(1)))
            steps.append(f"{match.group(1)}. {text[match.end():]}")
        elif steps:  # Nummer von der Texterkennung verloren – die Karte ist die nächste Teilaufgabe
            numbers.append(numbers[-1] + 1)
            steps.append(f"{numbers[-1]}. {text}")
        else:
            notes.append(text)
    if not steps:
        problem = problem or "In der te.comp-Aufgabenleiste wurden keine nummerierten Teilaufgaben gefunden."
    elif numbers != list(range(1, len(numbers) + 1)):
        problem = problem or f"Teilaufgaben nicht lückenlos erkannt (gelesen: {', '.join(map(str, numbers))})."
    return WorkshopSteps(steps=steps, notes=notes, complete=not problem, problem=problem)


def _children(hwnd: int) -> list[int]:
    import win32gui

    found: list[int] = []

    def collect(child: int, _: object) -> bool:
        found.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, collect, None)
    return found


class _StepStrip:
    """Die echte te.comp-Aufgabenleiste. Geblättert wird mit te.comps eigenen Pfeilen – als Fensternachricht an genau
    diese zwei Schaltflächen: keine Mausbewegung, kein Fokuswechsel, und nie „Prüfen“, „Nächste Aufgabe“ oder
    „Abschließen“ (die Pfeile werden über ihre Lage direkt neben der Leiste bestimmt, nicht über ihre Beschriftung)."""

    def __init__(self, main_hwnd: int) -> None:
        import win32gui

        self._gui = win32gui
        children = _children(main_hwnd)
        tabs = sorted((c for c in children if win32gui.GetClassName(c) == STEP_CLASS),
                      key=lambda c: win32gui.GetWindowRect(c)[0])
        if not tabs:
            raise LookupError("keine Aufgabenleiste")
        self._tabs = tabs
        self._cards = [t for t in tabs if not self._has_final_buttons(t)]
        self.left_edge, _, self.right_edge, _ = win32gui.GetWindowRect(win32gui.GetParent(tabs[0]))
        _, top, _, bottom = win32gui.GetWindowRect(win32gui.GetParent(tabs[0]))
        # Pfeile: sichtbare, unbeschriftete, schmale Schaltflächen auf Höhe der Leiste, direkt an ihrem Rand. Alle Maße
        # relativ zur Leistenhöhe: je nach Bildschirmskalierung (und DPI-Modus des Prozesses) sind Pixel verschieden groß
        unit = max(bottom - top, 1)
        arrows = [c for c in children if win32gui.GetClassName(c) == "TAdvGlowButton" and not win32gui.GetWindowText(c)
                  and win32gui.IsWindowVisible(c) and self._width(c) <= 0.25 * unit
                  and abs(win32gui.GetWindowRect(c)[1] - top) <= 0.12 * unit
                  and abs(win32gui.GetWindowRect(c)[3] - bottom) <= 0.12 * unit]
        before = [a for a in arrows if 0 <= self.left_edge - win32gui.GetWindowRect(a)[2] <= 0.3 * unit]
        after = [a for a in arrows if 0 <= win32gui.GetWindowRect(a)[0] - self.right_edge <= 0.3 * unit]
        if not self._cards or len(before) != 1 or len(after) != 1:  # nicht eindeutig: lieber gar nicht klicken
            raise LookupError("Pfeile an der Aufgabenleiste nicht eindeutig")
        self._arrow = {-1: before[0], +1: after[0]}

    def _width(self, hwnd: int) -> int:
        left, _, right, _ = self._gui.GetWindowRect(hwnd)
        return int(right - left)

    def _height(self, hwnd: int) -> int:
        _, top, _, bottom = self._gui.GetWindowRect(hwnd)
        return int(bottom - top)

    def _has_final_buttons(self, tab: int) -> bool:
        return bool(_FINAL_BUTTONS & {self._gui.GetWindowText(child) for child in _children(tab)})

    @property
    def count(self) -> int:
        return len(self._cards)

    def offset(self) -> int:
        return int(self._gui.GetWindowRect(self._tabs[0])[0])

    def at_start(self) -> bool:
        return self.offset() >= self.left_edge - 1

    def at_end(self) -> bool:
        return bool(self._gui.GetWindowRect(self._cards[-1])[2] <= self.right_edge + 1)

    def visible_cards(self) -> dict[int, str]:
        from app.vision import capture

        texts: dict[int, str] = {}
        for index, tab in enumerate(self._cards):
            rect = tuple(self._gui.GetWindowRect(tab))
            if rect[0] >= self.left_edge - 1 and rect[2] <= self.right_edge + 1:
                info = capture.WindowInfo(tab, "", STEP_CLASS, TECOMP_PROCESS, rect)  # type: ignore[arg-type]
                texts[index] = capture.ocr(capture.grab(info))
        return texts

    def click(self, direction: int) -> None:
        import win32api
        import win32con

        arrow = self._arrow[direction]
        point = win32api.MAKELONG(self._width(arrow) // 2, self._height(arrow) // 2)
        for message, flags in ((win32con.WM_MOUSEMOVE, 0), (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON),
                               (win32con.WM_LBUTTONUP, 0)):
            self._gui.PostMessage(arrow, message, flags, point)


def _move(strip: Any, direction: int, wait: float, sleep: Callable[[float], None]) -> bool:
    """Ein Pfeilklick; ``True``, sobald sich die Leiste bewegt hat und wieder stillsteht."""
    before = strip.offset()
    strip.click(direction)
    waited = 0.0
    while strip.offset() == before:
        if waited >= wait:
            return False
        sleep(0.1)
        waited += 0.1
    last = strip.offset()
    for _ in range(int(wait / 0.15)):  # eine eventuelle Gleitbewegung abwarten
        sleep(0.15)
        if strip.offset() == last:
            break
        last = strip.offset()
    return True


def read_strip(strip: Any, wait: float = 3.0,
               sleep: Callable[[float], None] = time.sleep) -> tuple[dict[int, str], bool, str]:
    """Liest jede Karte der Leiste: zuerst ganz nach links, dann Schritt für Schritt nach rechts. Danach steht die Leiste
    wieder genau dort, wo der Benutzer sie hatte. Ergebnis: (Index → Text, wiederhergestellt?, Problem)."""
    start = strip.offset()
    cards: dict[int, str] = {}
    problem = ""
    limit = strip.count + 3
    try:
        for _ in range(limit):
            if strip.at_start() or not _move(strip, -1, wait, sleep):
                break
        for _ in range(limit):
            cards.update({i: t for i, t in strip.visible_cards().items() if i not in cards})
            if strip.at_end() or len(cards) >= strip.count:
                break
            if not _move(strip, +1, wait, sleep):
                problem = "Die te.comp-Aufgabenleiste ließ sich nicht weiterblättern."
                break
    finally:
        for _ in range(2 * limit):
            if strip.offset() == start:
                break
            if not _move(strip, -1 if strip.offset() < start else +1, wait, sleep):
                break
    if len(cards) < strip.count:
        problem = problem or f"Nur {len(cards)} von {strip.count} Teilaufgaben-Karten lesbar."
    return cards, strip.offset() == start, problem


def workshop_steps(wait: float = 3.0) -> WorkshopSteps | None:
    """Alle Teilaufgaben des in te.comp geöffneten Workshops – ``None``, wenn keine te.comp-Aufgabenleiste da ist."""
    main = next((w for w in visible_windows() if w.process.lower() == TECOMP_PROCESS and w.cls == "TForm_Main"), None)
    if main is None:
        return None
    try:
        strip = _StepStrip(main.hwnd)
    except LookupError as exc:
        log.info("Workshop-Aufgabenleiste nicht erkannt: %s", exc)
        return None
    cards, restored, problem = read_strip(strip, wait)
    if not restored:
        log.warning("te.comp-Aufgabenleiste steht nach dem Lesen nicht mehr an der Ausgangsposition")
    result = parse_steps([cards.get(i, "") for i in range(strip.count)])
    result.restored = restored
    if problem:
        result.complete, result.problem = False, problem
    log.info("Workshop gelesen: %d Teilaufgaben, vollständig=%s", len(result.steps), result.complete)
    return result

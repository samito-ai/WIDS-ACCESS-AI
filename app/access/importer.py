"""Import externer Daten (Excel, Text mit Trennzeichen oder fester Breite) – nur lesend, ohne Zusatzpakete.

Die Datei wird hier in Zeilen zerlegt; das Einfügen übernimmt ``TableOps.import_rows`` (DAO, eine Transaktion,
Typumwandlung über ``to_dao``). Excel-Dateien (.xlsx) sind ZIP-Archive mit XML und werden direkt gelesen.
Quelldateien werden nur an bekannten Orten gesucht: vollständiger Pfad, te.comp-Übungsordner der geöffneten
Übung oder der Ordner der Datenbank.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.access.constants import AccessType
from app.access.specs import FieldSpec
from app.core.errors import AccessError, ObjectNotFoundError

ALLOWED_SUFFIXES = (".txt", ".csv", ".tab", ".xlsx", ".xls")
#: Lernunterlagen, deren Text (gekürzt) als Quelle für Fragen dient – nie importiert, nie verändert
MATERIAL_SUFFIXES = (".docx", ".md")
#: vom Benutzer freigegebene Ordner (Einstellungen → Lernordner); nur lesend, siehe ``set_user_folders``
_USER_FOLDERS: list[Path] = []
#: nie als Quelle: System-, Programm- und Anmeldedaten-Ordner
_FORBIDDEN = ("\\windows", "\\program files", "\\programdata", "\\appdata\\roaming\\microsoft",
              "\\appdata\\local\\microsoft", "\\.ssh", "\\.aws", "\\.azure")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_EXCEL_EPOCH = dt.datetime(1899, 12, 30)
_DATE_FORMAT_IDS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}


# --------------------------------------------------------------------------- Quelldatei finden
def folder_problem(folder: str) -> str:
    """Grund, warum ein Ordner nicht als Lernordner freigegeben werden darf (leer = zulässig)."""
    path = Path(folder.strip().strip('"'))
    if not path.is_absolute():
        return f"„{folder}“: bitte einen vollständigen Pfad angeben"
    lowered = str(path).lower().rstrip("\\")
    if len(lowered) <= 3:
        return f"„{folder}“: ein ganzes Laufwerk wird nicht freigegeben – bitte einen Ordner wählen"
    if any(part in lowered + "\\" for part in _FORBIDDEN):
        return f"„{folder}“: System-, Programm- oder Anmeldedaten-Ordner sind ausgeschlossen"
    return ""


def set_user_folders(folders: list[str]) -> None:
    """Freigegebene Lernordner übernehmen (nur zulässige; Erreichbarkeit wird bei jeder Suche mit Zeitlimit geprüft)."""
    _USER_FOLDERS[:] = [Path(f.strip().strip('"')) for f in folders if f.strip() and not folder_problem(f)]


def search_dirs(db_path: str) -> list[Path]:
    """Orte, an denen Quelldateien einer Übung liegen (te.comp-Übungsordner zuerst, dann der Ordner der Datenbank,
    dann die vom Benutzer freigegebenen Lernordner – ein nicht erreichbares Netzlaufwerk blockiert nicht)."""
    from app.vision.tecomp import reachable

    own = [Path(db_path).parent] if db_path and Path(db_path).parent.is_dir() else []
    return tecomp_dirs(db_path) + own + [d for d in _USER_FOLDERS if d not in own and reachable(d)]


def tecomp_dirs(db_path: str) -> list[Path]:
    """te.comp-Quellordner: Dateien der geöffneten Übung, dann die Arbeitsordner (Workshops, Laufwerk W:)."""
    from app.vision import tecomp

    dirs: list[Path] = []
    # te.comp-Quellen nur für te.comp-Dateien im Arbeitsordner – nie für eine eigene, zufällig gleich benannte Datei
    in_workdir = bool(db_path) and tecomp.in_tecomp_workdir(db_path)
    match = tecomp.EXERCISE_FILE.search(Path(db_path).name) if in_workdir else None
    base = tecomp.install_dir() if match else None
    if match and base is not None:
        chapter = base / "access" / "cbt" / match.group(1) / "files"
        dirs += [chapter / f"U{match.group(2)}", chapter]
    # nur te.comp-Dateien im Arbeitsordner – eine eigene, zufällig gleichnamige Datei bekommt keine Workshop-Quellen
    workshop = tecomp.workshop_for(db_path) if in_workdir and not match else None
    if workshop is not None:  # Quelldateien eines Workshops (z. B. CDs.xls) liegen im Workshop-Ordner
        dirs.append(workshop)
    work = tecomp.workdirs()  # bereits auf Erreichbarkeit geprüft (Netzlaufwerk mit Zeitlimit)
    return [d for d in dirs if d.is_dir()] + work


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".bmp")


def _entries(folder: Path) -> list[Path]:
    """Dateien eines Quellordners – ein inzwischen getrenntes Laufwerk (z. B. W:) liefert nichts statt eines Fehlers."""
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def image_files(db_path: str) -> list[Path]:
    """Bilddateien im Übungsordner (für Anlagefelder), erster Fundort je Dateiname gewinnt."""
    seen: dict[str, Path] = {}
    for folder in search_dirs(db_path):
        for path in _entries(folder):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                seen.setdefault(path.name.lower(), path)
    return list(seen.values())


def resolve_source(name: str, db_path: str) -> Path:
    """Findet die Quelldatei; ``name`` darf ohne Endung sein („Filmcrew1“)."""
    candidate = Path(name)
    if candidate.is_absolute():
        if candidate.is_file() and candidate.suffix.lower() in ALLOWED_SUFFIXES:
            return candidate
        raise ObjectNotFoundError("Datei", name)
    wanted = candidate.name.lower()
    for folder in search_dirs(db_path):
        for path in _entries(folder):
            if (path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
                    and wanted in (path.name.lower(), path.stem.lower()) and _inside(path, folder)):
                return path
    raise ObjectNotFoundError("Datei", name)


def _inside(path: Path, folder: Path) -> bool:
    """Liegt die Datei – nach Auflösen von Verknüpfungen/Junctions – wirklich im Ordner und außerhalb gesperrter Orte?
    Gilt für die vom Benutzer freigegebenen Lernordner; te.comp- und Datenbankordner sind feste, bekannte Orte."""
    if folder not in _USER_FOLDERS:
        return True
    try:
        real, root = path.resolve(), folder.resolve()
    except OSError:
        return False
    return real.is_relative_to(root) and not folder_problem(str(real.parent))


def mentioned_sources(task_text: str, db_path: str, suffixes: tuple[str, ...] = ALLOWED_SUFFIXES) -> list[Path]:
    """Quelldateien der Übung, die in der Aufgabenstellung genannt werden."""
    text = task_text.lower()
    found: list[Path] = []
    for folder in search_dirs(db_path):  # bekannte Orte, nur namentlich genannte Dateien – keine Laufwerkssuche
        for path in _entries(folder):
            if path.is_file() and path.suffix.lower() in suffixes and _inside(path, folder) and re.search(
                rf"(?<![\w]){re.escape(path.stem.lower())}(?![\w])", text) and path not in found:
                found.append(path)
    return found


# --------------------------------------------------------------------------- Lesen
def read_rows(path: Path, *, delimiter: str | None = None, widths: list[int] | None = None,
              sheet: str | None = None, has_header: bool = True) -> list[list[Any]]:
    """Zeilen der Datei. Excel liefert typisierte Werte (Zahl, Datum, Ja/Nein), Textdateien Zeichenketten."""
    if path.suffix.lower() == ".xlsx":
        rows = _read_xlsx(path, sheet)
    elif path.suffix.lower() == ".xls":
        rows = _read_xls(path, sheet)
    else:
        text = _decode(path.read_bytes())
        lines = [line for line in text.splitlines() if line.strip()]
        if widths:
            rows = [_split_fixed(line, widths) for line in lines]
        else:
            sep = delimiter or detect_delimiter(lines)
            rows = [list(r) for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=sep, quotechar='"')]
            if has_header:
                rows = _merge_overflow(rows, sep)
    rows = [[v.strip() if isinstance(v, str) else v for v in row] for row in rows if any(v not in ("", None) for v in row)]
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # Spalten, die überall leer sind (z. B. durch ein „;“ am Zeilenende), fallen weg
    keep = [i for i in range(width) if any(r[i] not in ("", None) for r in rows)]
    return [[r[i] for i in keep] for r in rows]


def _merge_overflow(rows: list[list[str]], sep: str) -> list[list[str]]:
    """Mit Kopfzeile: Ein Trennzeichen ohne Anführungszeichen im letzten Text (z. B. „Inhalt“ in te.comp-Filme.txt)
    ergibt mehr Werte als Spaltennamen. Die überzähligen Werte gehören zur letzten Spalte."""
    width = len(_trim(rows[0])) if rows else 0
    if not width:
        return rows
    return [[*row[:width - 1], sep.join(v for v in row[width - 1:] if v)] if len(_trim(row)) > width else row
            for row in rows]


def _trim(row: list[str]) -> list[str]:
    end = len(row)
    while end and not row[end - 1].strip():
        end -= 1
    return row[:end]


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def detect_delimiter(lines: list[str]) -> str:
    sample = lines[:20]
    counts = {sep: min((line.count(sep) for line in sample), default=0) for sep in ("\t", ";", ",", "|")}
    best = max(counts, key=lambda sep: counts[sep])
    return best if counts[best] > 0 else "\t"


def _split_fixed(line: str, widths: list[int]) -> list[str]:
    parts, pos = [], 0
    for width in widths:
        parts.append(line[pos:pos + width])
        pos += width
    return parts


def _read_xlsx(path: Path, sheet: str | None) -> list[list[Any]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
                    shared.append("".join(t.text or "" for t in item.iter(f"{_NS}t")))
            date_styles = _date_styles(archive.read("xl/styles.xml")) if "xl/styles.xml" in names else set()
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            epoch = dt.datetime(1904, 1, 1) if any((p.get("date1904") or "").lower() in ("1", "true")
                                                   for p in workbook.iter(f"{_NS}workbookPr")) else _EXCEL_EPOCH
            sheets = [(s.get("name") or "", s.get(f"{_REL}id") or "") for s in workbook.iter(f"{_NS}sheet")]
            if not sheets:
                raise AccessError(f"„{path.name}“ enthält kein Tabellenblatt.")
            chosen = next((s for s in sheets if sheet and s[0].lower() == sheet.lower()), sheets[0])
            if sheet and chosen[0].lower() != sheet.lower():
                raise ObjectNotFoundError("Tabellenblatt", sheet)
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target = next(r.get("Target") or "" for r in rels if r.get("Id") == chosen[1])
            xml = archive.read(target.lstrip("/") if target.startswith("/") else f"xl/{target}")
    except (zipfile.BadZipFile, KeyError, StopIteration, ElementTree.ParseError) as exc:
        raise AccessError(f"„{path.name}“ ist keine lesbare Excel-Datei (.xlsx).", detail=str(exc)) from exc
    rows: list[list[Any]] = []
    for row in ElementTree.fromstring(xml).iter(f"{_NS}row"):
        cells: dict[int, Any] = {}
        for cell in row.iter(f"{_NS}c"):
            ref = cell.get("r")
            column = _column_index(ref) if ref else (max(cells) + 1 if cells else 0)  # r ist laut OOXML optional
            kind, value = cell.get("t"), cell.find(f"{_NS}v")
            text = value.text if value is not None and value.text is not None else ""
            if kind == "s":
                cells[column] = shared[int(text)] if text else ""
            elif kind == "inlineStr":
                cells[column] = "".join(t.text or "" for t in cell.iter(f"{_NS}t"))
            elif kind == "b":
                cells[column] = text == "1"
            elif kind == "d":  # ISO-8601-Datum (z. B. aus LibreOffice)
                cells[column] = dt.datetime.fromisoformat(text) if text else ""
            elif kind in ("str", "e"):
                cells[column] = text
            elif text:
                number = float(text)
                if int(cell.get("s") or 0) in date_styles:
                    cells[column] = epoch + dt.timedelta(days=number)
                else:
                    cells[column] = int(number) if number.is_integer() else number
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _read_xls(path: Path, sheet: str | None) -> list[list[Any]]:
    """Altes Excel-Format (.xls, z. B. te.comp-Workshop „CD-Sammlung“) über den Excel-Treiber der Access-Engine."""
    import pythoncom

    from app.access.session import dao_engine

    pythoncom.CoInitialize()  # auch aus Hintergrund-Threads (Planprüfung) aufrufbar
    db = None
    try:
        db = dao_engine().OpenDatabase(str(path), False, True, "Excel 8.0;HDR=NO;IMEX=1")
        sheets = [str(t.Name) for t in db.TableDefs if str(t.Name).endswith(("$", "$'"))]
        if not sheets:
            raise AccessError(f"„{path.name}“ enthält kein Tabellenblatt.")
        name = sheets[0]
        if sheet:
            name = next((s for s in sheets if s.strip("'").rstrip("$").lower() == sheet.lower()), "")
            if not name:
                raise ObjectNotFoundError("Tabellenblatt", sheet)
        rs = db.OpenRecordset(f"SELECT * FROM [{name}]", 4)  # dbOpenSnapshot
        rows: list[list[Any]] = []
        try:
            count = int(rs.Fields.Count)
            while not rs.EOF:
                row: list[Any] = []
                for i in range(count):
                    value = rs.Fields(i).Value
                    if isinstance(value, dt.datetime):  # pywintypes-Datum → normales datetime
                        value = dt.datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
                    elif isinstance(value, float) and value.is_integer():
                        value = int(value)
                    row.append("" if value is None else value)
                rows.append(row)
                rs.MoveNext()
        finally:
            rs.Close()
        return rows
    except (AccessError, ObjectNotFoundError):
        raise
    except Exception as exc:
        raise AccessError(f"„{path.name}“ ist keine lesbare Excel-Datei (.xls).", detail=str(exc)) from exc
    finally:
        if db is not None:
            db.Close()


def _column_index(ref: str) -> int:
    index = 0
    for char in re.match(r"[A-Z]*", ref.upper()).group(0):  # type: ignore[union-attr]
        index = index * 26 + ord(char) - 64
    return max(index - 1, 0)


def _date_styles(xml: bytes) -> set[int]:
    root = ElementTree.fromstring(xml)
    custom = {int(f.get("numFmtId") or 0): f.get("formatCode") or "" for f in root.iter(f"{_NS}numFmt")}

    def is_date(fmt_id: int) -> bool:
        if fmt_id in _DATE_FORMAT_IDS:
            return True
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fmt_id, "")).lower()
        return bool(re.search(r"[dy]|m{3,}", code)) or ("m" in code and ("d" in code or "y" in code))

    xfs = root.find(f"{_NS}cellXfs")
    return {i for i, xf in enumerate(xfs if xfs is not None else []) if is_date(int(xf.get("numFmtId") or 0))}


# --------------------------------------------------------------------------- Felder ableiten
def clean_field_name(header: Any, index: int) -> str:
    """Feldname aus einer Kopfzeile – ohne die in Access verbotenen Zeichen ``. ! ` [ ]``."""
    name = re.sub(r"[.!`\[\]]", "_", str(header or "").strip()).strip(" _")
    return name[:64] or f"Feld{index + 1}"


def infer_type(values: list[Any]) -> AccessType:
    from app.access.values import to_dao

    filled = [v for v in values if v not in ("", None)]
    if not filled:
        return AccessType.SHORT_TEXT
    if all(isinstance(v, bool) for v in filled):
        return AccessType.YES_NO
    if all(isinstance(v, dt.datetime) for v in filled):
        return AccessType.DATETIME
    if all(isinstance(v, int) and not isinstance(v, bool) for v in filled):
        return AccessType.LONG if all(abs(v) < 2**31 for v in filled) else AccessType.DOUBLE
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in filled):
        return AccessType.DOUBLE
    texts = [str(v).strip() for v in filled]

    def parses(access_type: AccessType) -> bool:
        try:
            return all(to_dao(t, access_type) is not None for t in texts)
        except Exception:
            return False

    if all(t.lower() in ("ja", "nein", "wahr", "falsch", "true", "false") for t in texts):
        return AccessType.YES_NO
    # führende Nullen (PLZ, Codes) bleiben Text
    if all(re.fullmatch(r"-?[€\s\d.,]+", t) for t in texts) and not any(re.match(r"0\d", t) for t in texts):
        if parses(AccessType.LONG):
            return AccessType.LONG if all(abs(int(to_dao(t, AccessType.LONG))) < 2**31 for t in texts) else AccessType.DOUBLE
        if parses(AccessType.DOUBLE):
            return AccessType.CURRENCY if any("€" in t for t in texts) else AccessType.DOUBLE
    if parses(AccessType.DATETIME):
        return AccessType.DATETIME
    return AccessType.LONG_TEXT if max(len(t) for t in texts) > 255 else AccessType.SHORT_TEXT


#: Freitextspalten (Notizen, Anmerkungen …) sind in Access „Langer Text“ – auch wenn die Beispielwerte kurz sind
_NOTES_COLUMN = re.compile(r"(?i)(anmerkung|bemerkung|notiz|kommentar|beschreibung|hinweis|liedtext|memo)")


def infer_fields(header: list[Any] | None, data: list[list[Any]]) -> list[FieldSpec]:
    width = max((len(r) for r in data), default=len(header or []))
    names: list[str] = []
    for i in range(width):
        name = clean_field_name(header[i] if header and i < len(header) else "", i)
        while name.lower() in (n.lower() for n in names):
            name += "_"
        names.append(name)
    specs = []
    for i, name in enumerate(names):
        kind = infer_type([r[i] if i < len(r) else "" for r in data])
        if kind == AccessType.SHORT_TEXT and _NOTES_COLUMN.search(name):
            kind = AccessType.LONG_TEXT
        specs.append(FieldSpec(name=name, type=kind))
    return specs


def excel_serial_to_datetime(value: Any) -> Any:
    """Zahlen aus Textdateien bleiben Zahlen; nur echte Excel-Seriennummern werden zum Datum."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _EXCEL_EPOCH + dt.timedelta(days=float(value))
    return value


# --------------------------------------------------------------------------- Vorschau für die KI
def material_text(path: Path, limit: int = 4000) -> str:
    """Text einer Lernunterlage (Word .docx bzw. Markdown), gekürzt – nur lesend, ohne Zusatzpakete."""
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo("word/document.xml").file_size > 5_000_000:  # Schutz vor „ZIP-Bomben“
                raise AccessError(f"Lernunterlage „{path.name}“ ist zu groß.")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p")]
        text = "\n".join(p for p in paragraphs if p.strip())
    else:
        with path.open("rb") as handle:
            text = _decode(handle.read(limit * 4))
    return text[:limit] + (" …(gekürzt)" if len(text) > limit else "")


def preview(path: Path, max_rows: int = 6, whole_up_to: int = 30) -> str:
    """Kurzbeschreibung einer Quelldatei: Format, Trennzeichen und die ersten Zeilen (gekürzt).
    Kleine Dateien (bis ``whole_up_to`` Zeilen, z. B. eine Liste für ein Nachschlagefeld) erscheinen vollständig."""
    def cell(value: Any) -> str:
        return value.strftime("%d.%m.%Y") if isinstance(value, dt.datetime) else str(value)[:40]

    if path.suffix.lower() in (".xlsx", ".xls"):
        rows = _read_xlsx(path, None) if path.suffix.lower() == ".xlsx" else _read_xls(path, None)
        kind, shown_rows = "Excel-Arbeitsmappe (erstes Tabellenblatt)", ["    " + " | ".join(cell(v) for v in r) for r in rows]
    else:
        lines = [line for line in _decode(path.read_bytes()).splitlines() if line.strip()]
        sep = detect_delimiter(lines)
        if all(sep not in line for line in lines[:10]):
            # ohne Trennzeichen: eine einfache Liste oder feste Breite – Zeilen mit Rand, damit Spalten zählbar sind
            kind, shown_rows = "Textdatei ohne Trennzeichen (Liste oder feste Breite)", [f"    |{line[:160]}|" for line in lines]
        else:
            names = {"\t": "Tabulator", ";": "Semikolon", ",": "Komma", "|": "senkrechter Strich"}
            kind = f"Textdatei, Trennzeichen {names[sep]}"
            shown_rows = ["    " + " | ".join(cell(v) for v in r) for r in read_rows(path, delimiter=sep)]
    whole = len(shown_rows) <= whole_up_to
    head = "Alle Zeilen" if whole else "Erste Zeilen"
    return f"- {path.name}: {kind}, {len(shown_rows)} Zeilen. {head}:\n" + "\n".join(shown_rows[:whole_up_to if whole else max_rows])

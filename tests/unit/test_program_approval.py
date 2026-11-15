"""24-Stunden-Freigabe je te.comp-Lernprogramm: eindeutige Zuordnung, Ablauf, Widerruf, dauerhafte Speicherung,
und der eingeschränkte Prüfungsschutz (echte Prüfungsumgebungen statt des Wortes „Schularbeit“)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.settings import AppSettings
from app.security import programs
from app.security.policy import SafetyPolicy
from app.storage.database import Storage

KAP = """<root><global><caption>{caption}</caption><appType>access</appType></global>
<filter>{filters}</filter></root>"""


@pytest.fixture
def tecomp_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Nachgebaute te.comp-Installation (Paketdefinitionen wie im Original) und Arbeitsordner."""
    install, workdir = tmp_path / "install", tmp_path / "te.comp" / "te.comp-training"
    packages = install / "access" / "packages"
    packages.mkdir(parents=True)
    packages.joinpath("Kap.02.xml").write_text(KAP.format(caption="DA - Tabellen", filters="<c>02*</c>"), encoding="utf-8")
    packages.joinpath("Kap.04.xml").write_text(KAP.format(caption="DA - Formulare", filters="<c>03*</c><c>WS15</c>"),
                                               encoding="utf-8")
    packages.joinpath("Kap.05.xml").write_text("<root>kaputt", encoding="utf-8")  # unlesbare Datei stört nicht
    for chapter, number in (("0201", 1), ("0203", 2), ("0301", 2)):
        page = install / "access" / "cbt" / chapter / "intro" / f"U{number}" / "task.html"
        page.parent.mkdir(parents=True)
        page.write_text("<p>Aufgabe</p>", encoding="utf-8")
    workdir.mkdir(parents=True)
    return install, workdir


def test_programs_are_read_from_tecomp_packages(tecomp_dirs: tuple[Path, Path]) -> None:
    install, _ = tecomp_dirs
    assert programs.load_programs(install) == [
        programs.Program("Kap.02", "DA - Tabellen", ("02*",)), programs.Program("Kap.04", "DA - Formulare", ("03*", "WS15"))]
    assert programs.load_programs(install / "fehlt") == []


def test_exercise_files_are_assigned_only_when_unambiguous(tecomp_dirs: tuple[Path, Path]) -> None:
    install, workdir = tecomp_dirs

    def lookup(path: Path, **kw: object) -> str | None:
        program = programs.program_for(str(path), install=install, workdir=workdir, **kw)  # type: ignore[arg-type]
        return program.id if program else None

    # mehrere Übungsdateien desselben Lernprogramms → dieselbe Freigabe
    assert lookup(workdir / "02011_12345678.accdb") == lookup(workdir / "02032_1.accdb") == "Kap.02"
    assert lookup(workdir / "03012_7.accdb") == "Kap.04", "Kapitel 03 gehört laut te.comp zum Paket Kap.04"
    # nie erfasst: fremder Ordner, fremder Name, Übung ohne Aufgabenseite, Selbsttest/Zertifikat, Mehrdeutigkeit
    assert lookup(workdir.parent / "02011_1.accdb") is None
    assert lookup(Path.home() / "Downloads" / "02011_1.accdb") is None
    assert lookup(workdir / "Schularbeit_02011_1.accdb") is None
    assert lookup(workdir / "02011_1.accdb.bak") is None
    assert lookup(workdir / "02019_1.accdb") is None, "U9 gibt es nicht"
    assert lookup(workdir / "Rechentest.accdb") is None
    both = [programs.Program("A", "A", ("02*",)), programs.Program("B", "B", ("0*",))]
    assert lookup(workdir / "02011_1.accdb", programs=both) is None, "mehrdeutig → keine Freigabe"
    assert programs.program_for("", install=install, workdir=workdir) is None


def test_approval_lasts_24_hours_and_can_be_revoked() -> None:
    tables = programs.Program("Kap.02", "DA - Tabellen", ("02*",))
    approvals: list = []
    start = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    entry = programs.approve(approvals, tables, now=start)
    assert entry.expires_at == (start + timedelta(hours=24)).isoformat()
    assert programs.active_approval(approvals, "Kap.02", start + timedelta(hours=23, minutes=59)) is entry
    assert programs.active_approval(approvals, "Kap.02", start + timedelta(hours=24, minutes=1)) is None, "abgelaufen"
    assert programs.active_approval(approvals, "Kap.04", start) is None, "anderes Lernprogramm braucht eigene Freigabe"
    assert programs.revoke(approvals, "Kap.02", now=start) and programs.active_approval(approvals, "Kap.02", start) is None
    assert approvals == [entry] and entry.revoked, "Widerruf bleibt nachvollziehbar gespeichert"
    programs.approve(approvals, tables, now=start + timedelta(days=40))  # alte Einträge (> 30 Tage) werden aufgeräumt
    assert len(approvals) == 1 and approvals[0].approved_at.startswith("2026-12-24")


def test_approval_is_stored_permanently(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "s.sqlite3")
    settings = AppSettings()
    programs.approve(settings.security.program_approvals, programs.Program("Kap.02", "DA - Tabellen", ("02*",)))
    storage.save_settings(settings)
    loaded = Storage(tmp_path / "s.sqlite3").load_settings().security.program_approvals
    assert [(a.program_id, a.caption) for a in loaded] == [("Kap.02", "DA - Tabellen")] and loaded[0].active()
    storage.close()


def test_exam_protection_detects_exam_environments_not_the_word_schularbeit() -> None:
    policy = SafetyPolicy(AppSettings().security)
    assert not policy.check_environment(["Access - Schularbeit_Abfragen : Datenbank- C:\\Schule\\Schularbeit.accdb"], []).blocked
    assert not policy.check_environment([], ["msaccess.exe", "tecomptraining.exe"]).blocked
    assert policy.check_environment([], ["Next-Exam-Student.exe"]).blocked, "Next Exam (Prüfungsumgebung der Schule)"
    assert policy.check_environment(["Next-Exam – Schülerfenster"], []).blocked
    assert policy.check_environment([], ["SafeExamBrowser.exe"]).blocked
    assert policy.check_environment([], ["SafeExamBrowser.Client.exe"]).blocked
    # Live-Befund 14.11.: der dauerhaft laufende SEB-Windows-Dienst ist keine laufende Prüfung
    assert not policy.check_environment([], ["SafeExamBrowser.Service.exe", "msaccess.exe"]).blocked
    assert policy.check_environment(["te.comp Zertifizierung"], []).blocked, "te.comp-Prüfungsmodus bleibt gesperrt"
    from app.vision import capture

    windows = [capture.WindowInfo(1, "Next Exam – Mathematik 1A", "Chrome_WidgetWin_1", "chrome.exe", (0, 0, 1, 1)),
               capture.WindowInfo(2, "Prüfung bestanden? – Google Suche", "Chrome_WidgetWin_1", "chrome.exe", (0, 0, 1, 1)),
               capture.WindowInfo(3, "Access - Schularbeit_1 : Datenbank", "OMain", "MSACCESS.EXE", (0, 0, 1, 1))]
    original = capture.visible_windows
    capture.visible_windows = lambda: windows  # type: ignore[assignment]
    try:
        titles, _ = capture.environment()
    finally:
        capture.visible_windows = original  # type: ignore[assignment]
    assert titles == ["Next Exam – Mathematik 1A", "Access - Schularbeit_1 : Datenbank"], "Browser-Prüfung erkannt"
    assert policy.check_environment(titles, []).blocked
    stored = AppSettings().security.model_copy(update={"exam_keywords": ["schularbeit", "prüfung"]})  # alte Einstellung
    assert not SafetyPolicy(stored).check_environment(["Schularbeit Access"], []).blocked
    assert SafetyPolicy(stored).check_environment(["Prüfung Access"], []).blocked


# --------------------------------------------------------------------------- unabhängig vom Dateinamensschema
@pytest.fixture
def with_workshops(tecomp_dirs: tuple[Path, Path]) -> tuple[Path, Path]:
    install, workdir = tecomp_dirs
    for folder, template in (("CD-Sammlung", "CD-Sammlung"), ("Datendesign Rechnungen", "Rechnungen"),
                             ("Fahrtenbuch", "Fahrtenbuch"), ("Fahrtenbuch-Zeitübernahme", "Fahrtenbuch")):
        root = install / "access" / "workshops" / folder
        (root / "intro").mkdir(parents=True)
        (root / f"{template}.accdb").write_bytes(b"")
        (root / "intro" / "intro.html").write_text(f"<p>WS {folder}</p><p>Aufgabe: Tabellen erstellen</p>",
                                                    encoding="utf-8")
    (install / "access" / "workshops" / "Leer").mkdir()  # ohne Vorlage: kein Workshop
    return install, workdir


def test_workshops_are_assigned_without_a_numbering_scheme(with_workshops: tuple[Path, Path]) -> None:
    from app.vision import tecomp

    install, workdir = with_workshops
    known = programs.load_programs(install)
    assert [p.caption for p in known if p.is_workshop] == ["Workshop CD-Sammlung", "Workshop Datendesign Rechnungen",
                                                           "Workshop Fahrtenbuch", "Workshop Fahrtenbuch-Zeitübernahme"]

    def lookup(name: str, folder: Path | None = None) -> str | None:
        found = programs.program_for(str((folder or workdir) / name), install=install, workdir=workdir, programs=known)
        return found.id if found else None

    assert lookup("CD-Sammlung_12345678.accdb") == "WS:CD-Sammlung", "te.comp-Workshopdatei mit angehängter Nummer"
    assert lookup("Rechnungen_1.accdb") == "WS:Datendesign Rechnungen", "über die Vorlage, nicht den Ordnernamen"
    assert lookup("Fahrtenbuch_5.accdb") is None, "zwei Workshops mit derselben Vorlage → mehrdeutig → Dashboard"
    assert lookup("CD-Sammlung_1.accdb", workdir.parent) is None, "außerhalb des te.comp-Arbeitsordners nie automatisch"
    assert lookup("Datenbank1.accdb") is None and lookup("Golfclub.accdb") is None
    assert tecomp.workshop_for(str(workdir / "CD-Sammlung_9.accdb"), install).name == "CD-Sammlung"
    assert "Tabellen erstellen" in tecomp.workshop_context(install / "access" / "workshops" / "CD-Sammlung")


def test_any_file_can_be_assigned_once_in_the_dashboard(with_workshops: tuple[Path, Path], tmp_path: Path) -> None:
    """Beliebiger Name/Ort: einmalige, gespeicherte Zuordnung je Dateipfad – nie für eine andere Datei gleichen Namens."""
    install, workdir = with_workshops
    known = programs.load_programs(install)
    tables = next(p for p in known if p.id == "Kap.02")
    own = tmp_path / "Schule" / "Datenbank1.accdb"
    same_name_elsewhere = tmp_path / "Downloads" / "Datenbank1.accdb"
    assignments: dict[str, str] = {}

    def lookup(path: Path) -> str | None:
        found = programs.program_for(str(path), install=install, workdir=workdir, programs=known, assignments=assignments)
        return found.id if found else None

    assert lookup(own) is None
    programs.assign(assignments, str(own), tables)
    assert lookup(own) == "Kap.02" and lookup(Path(str(own).upper())) == "Kap.02", "Windows: Groß-/Kleinschreibung egal"
    assert lookup(same_name_elsewhere) is None, "gleicher Name, andere Datei → keine Zuordnung"
    programs.assign(assignments, str(workdir / "Fahrtenbuch_5.accdb"), next(p for p in known if p.id == "WS:Fahrtenbuch"))
    assert lookup(workdir / "Fahrtenbuch_5.accdb") == "WS:Fahrtenbuch", "mehrdeutiger Workshop einmal gewählt"
    assert programs.unassign(assignments, str(own)) and lookup(own) is None


def test_workshop_source_files_are_found(with_workshops: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.access import importer
    from app.vision import tecomp

    install, workdir = with_workshops
    monkeypatch.setattr(tecomp, "install_dir", lambda: install)
    monkeypatch.setattr(tecomp, "workdirs", lambda: [])
    monkeypatch.setattr(tecomp, "in_tecomp_workdir", lambda path: Path(path).parent == workdir)
    folder = install / "access" / "workshops" / "CD-Sammlung"
    assert folder in importer.search_dirs(str(workdir / "CD-Sammlung_12345678.accdb"))
    elsewhere = workdir.parent / "Eigene" / "CD-Sammlung_1.accdb"
    assert folder not in importer.search_dirs(str(elsewhere)), "gleichnamige eigene Datei bekommt keine Workshop-Quellen"


def test_real_tecomp_workshops_are_recognized() -> None:
    """Gegen die echte te.comp-Installation (nur lesend): Workshops sind Freigabe-Einheiten, CD-Sammlung eindeutig."""
    from app.vision import tecomp

    install = tecomp.install_dir()
    if install is None or not (install / "access" / "workshops").is_dir():
        pytest.skip("te.comp-training ist nicht installiert")
    known = programs.load_programs(install)
    assert sum(p.is_workshop for p in known) >= 10 and sum(not p.is_workshop for p in known) >= 10
    found = programs.program_for(str(programs.default_workdir() / "CD-Sammlung_12345678.accdb"), install=install,
                                 programs=known)
    assert found is not None and found.id == "WS:CD-Sammlung"
    assert tecomp.workshop_for("Fahrtenbuch_1.accdb", install) is None, "zwei Workshops mit derselben Vorlage"

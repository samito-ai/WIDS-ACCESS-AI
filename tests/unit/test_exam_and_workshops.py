"""Prüfungsschutz (echte Prüfungsumgebungen statt des Wortes „Schularbeit“) und Workshop-Erkennung unabhängig vom
Dateinamensschema. (Das frühere 24-Stunden-Freigabesystem ist entfernt – Alt+W ist die bewusste Benutzeraktion.)"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings import AppSettings
from app.security.policy import SafetyPolicy
from app.vision import tecomp


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


@pytest.fixture
def workshops(tmp_path: Path) -> tuple[Path, Path]:
    install, workdir = tmp_path / "install", tmp_path / "te.comp" / "te.comp-training"
    workdir.mkdir(parents=True)
    for folder, template in (("CD-Sammlung", "CD-Sammlung"), ("Datendesign Rechnungen", "Rechnungen"),
                             ("Fahrtenbuch", "Fahrtenbuch"), ("Fahrtenbuch-Zeitübernahme", "Fahrtenbuch")):
        root = install / "access" / "workshops" / folder
        (root / "intro").mkdir(parents=True)
        (root / f"{template}.accdb").write_bytes(b"")
        (root / "intro" / "intro.html").write_text(f"<p>WS {folder}</p><p>Aufgabe: Tabellen erstellen</p>",
                                                    encoding="utf-8")
    return install, workdir


def test_workshops_are_recognized_without_a_numbering_scheme(workshops: tuple[Path, Path]) -> None:
    install, workdir = workshops
    assert tecomp.workshop_for(str(workdir / "CD-Sammlung_12345678.accdb"), install).name == "CD-Sammlung"
    assert tecomp.workshop_for(str(workdir / "Rechnungen_1.accdb"), install).name == "Datendesign Rechnungen"
    assert tecomp.workshop_for(str(workdir / "Fahrtenbuch_5.accdb"), install) is None, "mehrdeutig → kein Kontext"
    assert tecomp.workshop_for(str(workdir / "Datenbank1.accdb"), install) is None
    assert "Tabellen erstellen" in tecomp.workshop_context(install / "access" / "workshops" / "CD-Sammlung")


def test_workshop_source_files_are_found(workshops: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.access import importer

    install, workdir = workshops
    monkeypatch.setattr(tecomp, "install_dir", lambda: install)
    monkeypatch.setattr(tecomp, "workdirs", lambda: [])
    monkeypatch.setattr(tecomp, "in_tecomp_workdir", lambda path: Path(path).parent == workdir)
    folder = install / "access" / "workshops" / "CD-Sammlung"
    assert folder in importer.search_dirs(str(workdir / "CD-Sammlung_12345678.accdb"))
    elsewhere = workdir.parent / "Eigene" / "CD-Sammlung_1.accdb"
    assert folder not in importer.search_dirs(str(elsewhere)), "gleichnamige eigene Datei bekommt keine Workshop-Quellen"


def test_real_tecomp_workshops_are_recognized() -> None:
    """Gegen die echte te.comp-Installation (nur lesend)."""
    install = tecomp.install_dir()
    if install is None or not (install / "access" / "workshops").is_dir():
        pytest.skip("te.comp-training ist nicht installiert")
    assert tecomp.workshop_for("CD-Sammlung_12345678.accdb", install).name == "CD-Sammlung"
    assert tecomp.workshop_for("Fahrtenbuch_1.accdb", install) is None, "zwei Workshops mit derselben Vorlage"

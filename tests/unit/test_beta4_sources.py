"""Beta 4: freigegebene Lernordner – nur lesend, nur namentlich genannte Dateien, keine System-/Anmeldeordner."""
from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.access import importer
from app.agent.orchestrator import TaskPipeline


@pytest.fixture(autouse=True)
def _reset_folders() -> Iterator[None]:
    yield
    importer.set_user_folders([])


def _docx(path: Path, *paragraphs: str) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def test_forbidden_and_unsafe_folders_are_rejected(tmp_path: Path) -> None:
    assert importer.folder_problem(str(tmp_path)) == ""
    assert "vollständigen Pfad" in importer.folder_problem("Unterlagen")
    assert "ganzes Laufwerk" in importer.folder_problem("D:\\")
    for bad in (r"C:\Windows\System32", r"C:\Program Files\Office", r"C:\Users\x\AppData\Roaming\Microsoft\Credentials",
                r"C:\Users\x\.ssh"):
        assert "ausgeschlossen" in importer.folder_problem(bad), bad
    importer.set_user_folders([str(tmp_path), r"C:\Windows"])
    assert [tmp_path] == importer._USER_FOLDERS, "unzulässige Ordner werden nie übernommen"


def test_only_named_files_from_user_folders_are_used(tmp_path: Path) -> None:
    folder = tmp_path / "Unterlagen"
    folder.mkdir()
    (folder / "Preisliste.csv").write_text("Artikel;Preis\nStift;1,20\n", encoding="utf-8")
    (folder / "Geheim.csv").write_text("x;y\n1;2\n", encoding="utf-8")
    _docx(folder / "Normalformen.docx", "Die 1. Normalform verlangt atomare Werte.", "Die 2. Normalform …")
    db = tmp_path / "anders" / "Uebung.accdb"
    db.parent.mkdir()
    task = "Importieren Sie die Preisliste. Beantworten Sie die Frage mithilfe der Unterlage Normalformen."
    assert importer.mentioned_sources(task, str(db)) == [], "ohne Freigabe wird dort nichts gesucht"
    importer.set_user_folders([str(folder)])
    assert [p.name for p in importer.mentioned_sources(task, str(db))] == ["Preisliste.csv"], "nur Genanntes"
    assert importer.resolve_source("Preisliste", str(db)) == folder / "Preisliste.csv"
    context = TaskPipeline._source_files(task, str(db))
    assert "Preisliste.csv" in context and "Geheim" not in context
    assert "LERNUNTERLAGEN" in context and "atomare Werte" in context, "Word-Unterlage als belegbare Quelle"


def test_unreachable_user_folder_does_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.vision import tecomp

    importer.set_user_folders([r"\\nicht-erreichbar\freigabe\unterlagen"])
    monkeypatch.setattr(tecomp, "reachable", lambda folder: False)
    assert importer.mentioned_sources("Importieren Sie Daten.csv", str(tmp_path / "x.accdb")) == []


def test_links_out_of_the_learning_folder_and_oversized_files_are_refused(tmp_path: Path) -> None:
    """Codex: eine Verknüpfung im freigegebenen Ordner darf nicht auf Dateien außerhalb zeigen; große Unterlagen werden
    nicht vollständig gelesen."""
    import os

    outside = tmp_path / "geheim"
    outside.mkdir()
    (outside / "credentials.md").write_text("aws_secret=XYZ", encoding="utf-8")
    folder = tmp_path / "Unterlagen"
    folder.mkdir()
    try:
        os.symlink(outside / "credentials.md", folder / "Unterlage.md")
    except OSError:
        pytest.skip("Symbolische Verknüpfungen brauchen hier Entwicklermodus/Adminrechte")
    importer.set_user_folders([str(folder)])
    db = tmp_path / "x" / "u.accdb"
    db.parent.mkdir()
    assert importer.mentioned_sources("Siehe Unterlage.", str(db), importer.MATERIAL_SUFFIXES) == []


def test_material_reading_is_bounded(tmp_path: Path) -> None:
    from app.core.errors import AccessError

    big = tmp_path / "gross.md"
    big.write_text("x" * 100_000, encoding="utf-8")
    assert len(importer.material_text(big)) < 4100
    bomb = tmp_path / "bombe.docx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document>" + " " * 6_000_000 + "</w:document>")
    with pytest.raises(AccessError, match="zu groß"):
        importer.material_text(bomb)


def test_containment_uses_the_resolved_link_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "Unterlagen"
    folder.mkdir()
    link = folder / "Unterlage.md"
    link.write_text("x", encoding="utf-8")
    importer.set_user_folders([str(folder)])
    assert importer._inside(link, folder)
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: Path(r"C:\Users\x\.aws\credentials.md")
                        if self == link else real_resolve(self, strict))
    assert not importer._inside(link, folder), "Linkziel außerhalb bzw. in gesperrtem Ordner"

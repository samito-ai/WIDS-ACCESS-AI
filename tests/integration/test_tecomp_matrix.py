"""Testmatrix über ALLE te.comp-Access-Aufgabenseiten der installierten Lernprogramme (nur lesend).

Jede Seite muss als Aufgabentext lesbar sein, blaue Objektnamen müssen exakt im Text stehen, Abbildungen werden
begrenzt mitgegeben, und keine reguläre Übung darf vom Prüfungsschutz als benotete Prüfung gewertet werden.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.security.policy import SafetyPolicy
from app.vision import tecomp

pytestmark = [pytest.mark.tecomp]


def _pages() -> list[Path]:
    base = tecomp.install_dir()
    if base is None:
        return []
    return sorted((base / "access" / "cbt").glob("*/intro/U*/task.html"))


PAGES = _pages()


@pytest.mark.skipif(not PAGES, reason="te.comp-training nicht installiert")
def test_all_chapters_are_covered() -> None:
    chapters = {p.parts[-4][:2] for p in PAGES}
    assert {"01", "02", "03", "04", "05", "06"} <= chapters, chapters
    assert len(PAGES) >= 150


@pytest.mark.parametrize("page", PAGES, ids=lambda p: f"{p.parts[-4]}-{p.parts[-2]}")
def test_task_page_is_readable_and_safe(page: Path) -> None:
    raw = page.read_text(encoding="utf-8", errors="replace")
    text = tecomp.html_to_text(raw)
    assert len(text) > 20 and "<" not in text, text[:200]
    for name in tecomp.blue_names(raw):
        assert f"„{name}“" in text, f"blauer Name „{name}“ fehlt im Aufgabentext"
    images = tecomp.task_images(page)
    assert len(images) <= 3 and all(img[:8] == b"\x89PNG\r\n\x1a\n" for img in images)
    # Übungen sind keine Prüfungen: der Aufgabentext darf keine Prüfungswarnung auslösen
    assert not SafetyPolicy.task_text_warnings(text), SafetyPolicy.task_text_warnings(text)

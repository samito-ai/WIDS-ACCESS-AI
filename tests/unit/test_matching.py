"""Eindeutige Zuordnung über Textwerte und tolerante Dateisuche – nie raten."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access import importer
from app.access.matching import match_where, normalize, plan_key_mapping

CDS = [(1, ("Al Jarreau", "Look to the Rainbow")), (2, ("George Benson", "White Rabbit")),
       (3, ("Archie Shepp / Jasper van't Hof", "Mama Rose")), (4, ("Queen", "Made in Heaven")),
       (5, ("Sting", "Soul Cages"))]


def test_normalize_ignores_case_spaces_and_punctuation_but_keeps_umlauts() -> None:
    assert normalize("Al Jarreau - Look to the RAINBOW!") == "aljarreaulooktotherainbow"
    assert normalize("Größe  zählt") == normalize("GRÖSSE zählt") == "grössezählt"  # casefold: ß = ss


@pytest.mark.parametrize(("source", "key", "kind"), [
    ("Al Jarreau - Look to the rainbow", 1, "exact"),       # zusammengesetzt, andere Schreibweise
    ("Archie Shepp - Mama Rose", 3, "exact"),               # Teil steckt im Feld, Titel genau
    ("Soul Cages", 5, "exact"),                             # genau ein einzelnes Feld
    ("George Benson - White Rabatt", 2, "similar"),         # Tippfehler, eindeutig bester Kandidat
])
def test_unique_candidates_are_mapped(source: str, key: int, kind: str) -> None:
    mapping = plan_key_mapping([(source,)], CDS)
    found = mapping.exact if kind == "exact" else {s: k for s, (k, _) in mapping.similar.items()}
    assert found == {(source,): key} and not mapping.unresolved


@pytest.mark.parametrize("source", ["Queen - Made in Heaven", "Unbekannte Band - Nichts", "", "Queen"])
def test_ambiguous_or_unknown_values_are_never_guessed(source: str) -> None:
    duplicates = [*CDS, (6, ("Queen", "Made in Heaven")), (7, ("Queen", "Innuendo"))]
    mapping = plan_key_mapping([(source,)], duplicates)
    assert mapping.keys == {} and (source,) in mapping.unresolved


def test_match_where_quotes_values_and_handles_null() -> None:
    assert match_where(["CD", "Nr"], ["O'Brien", None]) == "[CD] = \"O'Brien\" AND [Nr] Is Null"


def test_file_name_with_small_spelling_difference_is_found_only_when_unique(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer, "search_dirs", lambda db_path: [tmp_path])
    (tmp_path / "LetterPerfekt.txt").write_text("x", encoding="utf-8")
    assert importer.resolve_source("LetterPerfect.txt", str(tmp_path / "db.accdb")).name == "LetterPerfekt.txt"
    with pytest.raises(Exception, match="Datei"):
        importer.resolve_source("Liedtext.txt", str(tmp_path / "db.accdb"))  # zu unähnlich: nicht gefunden
    (tmp_path / "LetterPerfekd.txt").write_text("y", encoding="utf-8")
    with pytest.raises(Exception, match="Datei"):
        importer.resolve_source("LetterPerfect.txt", str(tmp_path / "db.accdb"))  # zwei ähnliche: lieber keine


def test_text_file_is_decoded_and_line_endings_normalized(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_bytes("Größe\nzählt\r\n\r\n".encode("cp1252"))
    assert importer.read_text_file(path) == "Größe\r\nzählt"


def test_a_part_inside_another_name_is_never_a_match() -> None:
    """Codex: „Ann - Song“ darf nicht zu Interpret „Joanne“ passen (Teil nur als Anfang eines Felds, ab 4 Zeichen)."""
    mapping = plan_key_mapping([("Ann - Song",)], [(1, ("Joanne", "Song")), (2, ("Bob", "Other"))])
    assert mapping.keys == {}


def test_numbers_in_file_names_must_match_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer, "search_dirs", lambda db_path: [tmp_path])
    (tmp_path / "Kunden2026.csv").write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="Datei"):
        importer.resolve_source("Kunden2025.csv", str(tmp_path / "db.accdb"))  # anderer Jahrgang: nie automatisch


def test_similarity_alone_is_never_enough() -> None:
    """Auftrag: „Unscharfe Ähnlichkeit allein darf nicht automatisch zu einer … Datenänderung führen.“"""
    assert plan_key_mapping([("White Rabatt",)], CDS).keys == {}  # kein genau passender Teil
    assert plan_key_mapping([("George Benson - White Rabatt",)], CDS).keys == {("George Benson - White Rabatt",): 2}


def test_separate_source_columns_count_as_separate_parts() -> None:
    """Codex (3. Runde): Interpret und Titel in zwei Quellspalten – genauer Interpret stützt den ähnlichen Titel."""
    assert plan_key_mapping([("George Benson", "White Rabatt")], CDS).keys == {("George Benson", "White Rabatt"): 2}


def test_time_default_keeps_its_time() -> None:
    from app.access.constants import AccessType
    from app.access.values import default_expression

    assert default_expression("12:03:43 AM", AccessType.DATETIME) == "#12/30/1899 00:03:43#"
    assert default_expression("15.03.2026", AccessType.DATETIME) == "#03/15/2026#"

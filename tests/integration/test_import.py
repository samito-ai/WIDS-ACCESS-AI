"""Import aus Excel- und Textdateien (te.comp Kapitel 02, 09, 10): Lesen, Typableitung, Einfügen per DAO."""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from app.access import importer
from app.access.constants import AccessType
from app.access.engine import AccessEngine
from app.access.specs import FieldSpec
from app.core.errors import AccessError, ObjectNotFoundError
from app.tasks import models as m

TECOMP_FILES = Path(r"C:\Program Files (x86)\te.comp\te.comp-training\access\cbt")


def write_xlsx(path: Path) -> Path:
    """Minimale echte .xlsx (wie von Excel gespeichert): Text, Zahl, Dezimalzahl, Datum, Ja/Nein."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    files = {
        "xl/workbook.xml": f'<workbook {ns} {rel}><sheets><sheet name="Begriffe" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>',
        "xl/sharedStrings.xml": f"<sst {ns}><si><t>Begriff</t></si><si><t>Anzahl</t></si><si><t>Preis</t></si>"
                                "<si><t>Seit</t></si><si><t>Aktiv</t></si><si><t>Birdie</t></si><si><r><t>Ea</t></r><r><t>gle</t></r></si></sst>",
        "xl/styles.xml": f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": f"<worksheet {ns}><sheetData>"
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>5</v></c><c r="B2"><v>3</v></c><c r="C2"><v>1.234</v></c>'
            '<c r="D2" s="1"><v>45658</v></c><c r="E2" t="b"><v>1</v></c></row>'
            '<row r="3"><c r="A3" t="s"><v>6</v></c><c r="B3"><v>12</v></c><c r="C3"><v>99</v></c>'
            '<c r="E3" t="b"><v>0</v></c></row>'
            "</sheetData></worksheet>",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, xml in files.items():
            archive.writestr(name, xml)
    return path


# --------------------------------------------------------------------------- Lesen (ohne Access)
def test_reads_xlsx_with_types(tmp_path: Path) -> None:
    rows = importer.read_rows(write_xlsx(tmp_path / "Golf.xlsx"))
    assert rows[0] == ["Begriff", "Anzahl", "Preis", "Seit", "Aktiv"]
    assert rows[1] == ["Birdie", 3, 1.234, dt.datetime(2025, 1, 1), True]
    assert rows[2] == ["Eagle", 12, 99, "", False], "fehlende Zelle bleibt leer, Rich-Text wird zusammengesetzt"
    fields = importer.infer_fields(rows[0], rows[1:])
    assert [f.type for f in fields] == [AccessType.SHORT_TEXT, AccessType.LONG, AccessType.DOUBLE,
                                        AccessType.DATETIME, AccessType.YES_NO]
    with pytest.raises(ObjectNotFoundError):
        importer.read_rows(tmp_path / "Golf.xlsx", sheet="Gibtsnicht")


def test_reads_delimited_and_fixed_width_text(tmp_path: Path) -> None:
    semi = tmp_path / "Rechnungen.txt"
    semi.write_bytes('Nr;Kunde;Datum;Versand;Text;\n9000;"ERNSH";1.9.2006 00:00:00;€ 0,00;"a; b";\n'
                     '9001;"VI""NET";2.9.2006 00:00:00;€ 16,19;c;\n'.encode("cp1252"))
    rows = importer.read_rows(semi)
    assert rows[0] == ["Nr", "Kunde", "Datum", "Versand", "Text"], "leere Spalte durch „;“ am Zeilenende entfällt"
    assert rows[1] == ["9000", "ERNSH", "1.9.2006 00:00:00", "€ 0,00", "a; b"]
    assert rows[2][1] == 'VI"NET'
    assert [f.type for f in importer.infer_fields(rows[0], rows[1:])] == [
        AccessType.LONG, AccessType.SHORT_TEXT, AccessType.DATETIME, AccessType.CURRENCY, AccessType.SHORT_TEXT]
    tab = tmp_path / "Crew.txt"
    tab.write_text("ID\tVorname\tPLZ\tWalk\n1\tAmy\t06460\tNein\n2\tÉmile\t6020\tJa\n", encoding="utf-8")
    rows = importer.read_rows(tab)
    assert rows[2][1] == "Émile" and importer.detect_delimiter(tab.read_text("utf-8").splitlines()) == "\t"
    assert [f.type for f in importer.infer_fields(rows[0], rows[1:])] == [
        AccessType.LONG, AccessType.SHORT_TEXT, AccessType.SHORT_TEXT, AccessType.YES_NO], "führende Null bleibt Text"
    loose = tmp_path / "Filme.txt"  # wie te.comp: „;“ im Inhalt ohne Anführungszeichen, leere Endfelder
    loose.write_text("A;B;Inhalt;\n1;x;kurz;\n2;;lang; mit Semikolon\n3;y;;\n", encoding="utf-8")
    assert importer.read_rows(loose) == [["A", "B", "Inhalt"], ["1", "x", "kurz"], ["2", "", "lang; mit Semikolon"],
                                         ["3", "y", ""]]
    assert len(importer.read_rows(loose, has_header=False)[2]) == 4, "ohne Kopfzeile wird nichts zusammengelegt"
    fixed = tmp_path / "Crew2.txt"
    fixed.write_text("11         Julie      w1 \n12         Pier       w0 \n", encoding="cp1252")
    assert importer.read_rows(fixed, widths=[11, 11, 1, 1]) == [["11", "Julie", "w", "1"], ["12", "Pier", "w", "0"]]


def test_finds_sources_only_in_known_places(tmp_path: Path) -> None:
    db = tmp_path / "uebung.accdb"
    db.write_bytes(b"")
    (tmp_path / "Filmstudios.txt").write_text("Warner\nFox\n", encoding="utf-8")
    assert importer.resolve_source("Filmstudios", str(db)).name == "Filmstudios.txt"
    assert importer.resolve_source("filmstudios.TXT", str(db)).name == "Filmstudios.txt"
    with pytest.raises(ObjectNotFoundError):
        importer.resolve_source("Unbekannt.txt", str(db))
    with pytest.raises(ObjectNotFoundError):
        importer.resolve_source(str(tmp_path / "uebung.accdb"), str(db)), "nur Text- und Excel-Dateien"
    with pytest.raises(ValueError):
        m.ImportData(file="x.txt", table="T", column_widths=[0])


@pytest.mark.tecomp
@pytest.mark.skipif(not TECOMP_FILES.exists(), reason="te.comp nicht installiert")
def test_real_tecomp_source_files() -> None:
    crew1 = importer.read_rows(TECOMP_FILES / "1002" / "files" / "Filmcrew1.txt")
    assert crew1[0] == ["FilmSetID", "Vorname", "Nachname", "Geschlecht", "WalkOfFame"] and len(crew1) > 10
    assert importer.infer_fields(crew1[0], crew1[1:])[4].type == AccessType.YES_NO
    films = importer.read_rows(TECOMP_FILES / "1002" / "files" / "Filme.txt")
    assert len(films[0]) == 7 and all(len(r) == 7 for r in films), "Inhalt mit „;“ in Anführungszeichen bleibt eine Spalte"
    assert importer.infer_fields(films[0], films[1:])[6].type == AccessType.LONG_TEXT
    golf = importer.read_rows(TECOMP_FILES / "0206" / "files" / "U2" / "Golfbegriffe.xlsx")
    assert len(golf) > 5 and all(isinstance(v, str) for v in golf[0])
    preview = importer.preview(TECOMP_FILES / "1002" / "files" / "Filmcrew2.txt")
    assert "feste Breite" in preview and "|11" in preview and "Erste Zeilen" in preview
    studios = importer.preview(TECOMP_FILES / "0905" / "files" / "U4" / "Filmstudios.txt")
    assert "Alle Zeilen" in studios and "|Warner Home|" in studios, "kurze Liste vollständig (für Nachschlagefelder)"
    assert "Tabulator" in importer.preview(TECOMP_FILES / "1002" / "files" / "Filmcrew1.txt")
    cds = importer.read_rows(TECOMP_FILES.parent / "workshops" / "CD-Sammlung" / "CDs.xls")  # altes Excel-Format
    assert cds[0][:3] == ["CD-Nummer", "Sortierbegriff", "Interpret"] and len(cds) > 50
    assert [f.type for f in importer.infer_fields(cds[0], cds[1:])][6] == AccessType.YES_NO, "WAHR/FALSCH → Ja/Nein"
    assert "Excel-Arbeitsmappe" in importer.preview(TECOMP_FILES.parent / "workshops" / "CD-Sammlung" / "CDs.xls")
    db = str(Path.home() / "te.comp" / "te.comp-training" / "10021_1.accdb")  # Übung 1002/U1 – nur der Name zählt
    assert [p.name for p in importer.mentioned_sources("Import Filmcrew1: Öffnen Sie die Textdatei Filmcrew1", db)] == ["Filmcrew1.txt"]


# --------------------------------------------------------------------------- Import in echtes Access
@pytest.mark.access
def test_import_into_new_and_existing_tables(engine: AccessEngine, tmp_path: Path) -> None:
    tables = engine.tables
    crew = tmp_path / "Crew.txt"
    crew.write_text("ID\tVorname\tNachname\tWalk\n1\tAmy\tAdams\tNein\n2\tJulie\tAndrews\tJa\n", encoding="utf-8")
    count, created = tables.import_rows("Crew", importer.read_rows(crew), primary_key=["ID"])
    assert (count, created) == (2, True)
    schema = engine.schema()
    assert schema.table("Crew").primary_key == ["ID"]
    assert [f.type for f in schema.table("Crew").fields] == [AccessType.LONG, AccessType.SHORT_TEXT,
                                                             AccessType.SHORT_TEXT, AccessType.YES_NO]
    _, rows = tables.read_sql("SELECT Vorname, Walk FROM Crew ORDER BY ID")
    assert rows == [["Amy", False], ["Julie", True]]

    fixed = tmp_path / "Crew2.txt"
    fixed.write_text("3          Pier       Angeli     w0 \n", encoding="cp1252")
    tables.add_field("Crew", FieldSpec(name="Geschlecht", size=1))
    data = importer.read_rows(fixed, widths=[11, 11, 11, 1, 1])
    order = [FieldSpec(name=n) for n in ("ID", "Vorname", "Nachname", "Geschlecht", "Walk")]
    assert tables.import_rows("Crew", data, has_header=False, fields=order) == (1, False)
    _, rows = tables.read_sql("SELECT Nachname, Geschlecht, Walk FROM Crew WHERE ID=3")
    assert rows == [["Angeli", "w", False]]

    count, created = tables.import_rows("Golf", importer.read_rows(write_xlsx(tmp_path / "Golf.xlsx")), add_id="ID")
    assert (count, created) == (2, True) and engine.schema().table("Golf").primary_key == ["ID"]
    _, rows = tables.read_sql("SELECT Begriff, Preis, Seit FROM Golf ORDER BY ID")
    assert rows[0][:2] == ["Birdie", 1.234] and rows[0][2].startswith("01.01.2025")

    workshop_xls = TECOMP_FILES.parent / "workshops" / "CD-Sammlung" / "CDs.xls"
    if workshop_xls.exists():  # te.comp-Workshop „CD-Sammlung“ (altes .xls-Format)
        count, created = tables.import_rows("CDs", importer.read_rows(workshop_xls), add_id="ID")
        assert created and count == len(importer.read_rows(workshop_xls)) - 1
        assert engine.schema().table("CDs").field("Doppel-CD").type == AccessType.YES_NO

    bad = tmp_path / "Bad.txt"
    bad.write_text("Zahl\n1\nkeine Zahl\n", encoding="utf-8")
    with pytest.raises(AccessError):
        tables.import_rows("Bad", importer.read_rows(bad), fields=[FieldSpec(name="Zahl", type=AccessType.LONG)])
    assert not tables.table_exists("Bad"), "fehlgeschlagener Import hinterlässt keine leere Tabelle"

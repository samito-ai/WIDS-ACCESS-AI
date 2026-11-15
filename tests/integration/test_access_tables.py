"""Integrationstests: Tabellen-, Feld-, Index- und Datensatzoperationen gegen echtes DAO."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access.constants import AccessType
from app.access.engine import AccessEngine
from app.access.specs import FieldPropertyChanges, FieldSpec, LookupSpec
from app.core.errors import (
    ApprovalRequiredError,
    DataIntegrityError,
    InvalidNameError,
    ObjectExistsError,
    ObjectNotFoundError,
    SqlSyntaxError,
)

pytestmark = pytest.mark.access


def test_sample_database_structure(engine: AccessEngine) -> None:
    schema = engine.schema()
    assert [t.name for t in schema.tables] == ["Artikel", "Bestellungen", "Kunden"]
    kunden = schema.table("Kunden")
    assert kunden is not None
    assert kunden.primary_key == ["KundenNr"]
    assert kunden.record_count == 12
    assert kunden.field("KundenNr").type == AccessType.AUTONUMBER
    assert kunden.field("Nachname").required is True
    assert kunden.field("Nachname").size == 50
    assert kunden.field("Umsatz").type == AccessType.CURRENCY
    assert kunden.field("Newsletter").type == AccessType.YES_NO
    assert kunden.field("Geburtsdatum").type == AccessType.DATETIME


def test_create_table_with_fields_and_primary_key(empty_engine: AccessEngine) -> None:
    """Test A: Kundentabelle mit mehreren Feldern und Primärschlüssel."""
    empty_engine.tables.create_table(
        "tblKunden",
        [
            FieldSpec(name="KundenID", type="AutoWert", primary_key=True),
            FieldSpec(name="Nachname", type="Kurzer Text", size=40, required=True, description="Familienname"),
            FieldSpec(name="Ort", type="text", size=30, default_value="Imst"),
            FieldSpec(name="Kreditlimit", type="Währung", validation_rule=">=0", validation_text="Nicht negativ"),
            FieldSpec(name="Aktiv", type="Ja/Nein", default_value="Ja"),
            FieldSpec(name="Eintritt", type="Datum/Uhrzeit", format="Short Date"),
            FieldSpec(name="Email", type="text", size=80, indexed="unique"),
        ],
        description="Kundenstamm",
    )
    table = empty_engine.schema().table("tblKunden")
    assert table is not None
    assert table.primary_key == ["KundenID"]
    assert table.description == "Kundenstamm"
    nachname = table.field("Nachname")
    assert (nachname.type, nachname.size, nachname.required) == (AccessType.SHORT_TEXT, 40, True)
    assert nachname.description == "Familienname"
    assert table.field("Ort").default_value == '"Imst"'
    assert table.field("Kreditlimit").validation_rule == ">=0"
    assert table.field("Aktiv").default_value == "True"
    assert table.field("Eintritt").format == "Short Date"
    assert table.field("Email").indexed == "unique"
    assert table.field("KundenID").primary_key is True


def test_create_table_conflicts_and_invalid_names(engine: AccessEngine) -> None:
    with pytest.raises(ObjectExistsError):
        engine.tables.create_table("Kunden", [FieldSpec(name="ID", type="long")])
    with pytest.raises(InvalidNameError):
        engine.tables.create_table("Neu.Tabelle", [FieldSpec(name="ID", type="long")])
    with pytest.raises(InvalidNameError):
        FieldSpec(name="Feld[1]", type="long")
    with pytest.raises(ObjectNotFoundError):
        engine.tables.create_table("X", [FieldSpec(name="A", type="long")], primary_key=["GibtEsNicht"])
    assert not engine.tables.table_exists("X")


def test_rename_copy_delete_table(engine: AccessEngine) -> None:
    copied = engine.tables.copy_table("Kunden", "Kunden_Kopie")
    assert copied == 12
    copy = engine.schema().table("Kunden_Kopie")
    assert copy.primary_key == ["KundenNr"]
    assert copy.record_count == 12
    assert copy.field("Nachname").required is True

    engine.tables.copy_table("Artikel", "Artikel_Struktur", with_data=False)
    assert engine.tables.count_records("Artikel_Struktur") == 0

    engine.tables.rename_table("Kunden_Kopie", "Kunden_Archiv")
    assert engine.tables.table_exists("Kunden_Archiv")
    assert not engine.tables.table_exists("Kunden_Kopie")
    with pytest.raises(ObjectExistsError):
        engine.tables.rename_table("Kunden_Archiv", "Artikel")

    engine.tables.delete_table("Kunden_Archiv")
    assert not engine.tables.table_exists("Kunden_Archiv")
    with pytest.raises(ObjectNotFoundError):
        engine.tables.delete_table("Kunden_Archiv")


def test_field_operations(engine: AccessEngine) -> None:
    tables = engine.tables
    tables.add_field("Kunden", FieldSpec(name="Telefon", type="text", size=20, caption="Tel."))
    with pytest.raises(ObjectExistsError):
        tables.add_field("Kunden", FieldSpec(name="Telefon", type="text"))
    tables.rename_field("Kunden", "Telefon", "Telefonnummer")
    field = engine.schema().table("Kunden").field("Telefonnummer")
    assert field is not None and field.size == 20 and field.caption == "Tel."

    tables.set_field_properties(
        "Kunden",
        "Telefonnummer",
        FieldPropertyChanges(size=30, required=False, default_value="unbekannt", description="Festnetz oder mobil",
                             indexed="duplicates"),
    )
    field = engine.schema().table("Kunden").field("Telefonnummer")
    assert field.size == 30
    assert field.default_value == '"unbekannt"'
    assert field.description == "Festnetz oder mobil"
    assert field.indexed == "duplicates"

    tables.remove_field("Kunden", "Telefonnummer")
    assert engine.schema().table("Kunden").field("Telefonnummer") is None
    with pytest.raises(ObjectNotFoundError):
        tables.remove_field("Kunden", "Telefonnummer")
    with pytest.raises(DataIntegrityError):
        tables.remove_field("Kunden", "KundenNr")


def test_lookup_fields_value_list_and_table(engine: AccessEngine) -> None:
    """Nachschlagefelder (te.comp Kapitel 0905): Werteliste und Tabelle als Datenherkunft."""
    tables = engine.tables
    countries = LookupSpec(values=["Österreich", "Deutschland", 'Land "X"'], limit_to_list=True)
    tables.add_field("Kunden", FieldSpec(name="Land", size=40, lookup=countries))
    customers = LookupSpec(row_source="SELECT KundenNr, Nachname FROM Kunden ORDER BY Nachname", column_count=2,
                           column_widths="0cm;4cm", allow_value_list_edits=False)
    tables.set_field_properties("Kunden", "KundenNr", FieldPropertyChanges(caption="Nr."))
    tables.add_field("Kunden", FieldSpec(name="Empfohlen_von", type=AccessType.LONG))
    tables.set_field_properties("Kunden", "Empfohlen_von", FieldPropertyChanges(lookup=customers))
    schema = engine.schema()
    land = schema.table("Kunden").field("Land")
    assert land.lookup_source == '"Österreich";"Deutschland";"Land ""X"""'
    assert land.lookup_limit_to_list and land.lookup_allow_edits
    ref = schema.table("Kunden").field("Empfohlen_von")
    assert ref.lookup_source.startswith("SELECT KundenNr") and not ref.lookup_limit_to_list and not ref.lookup_allow_edits
    plain = schema.table("Kunden").field("Nachname")
    assert plain.lookup_source == "", "normale Felder sind keine Nachschlagefelder"
    text = schema.to_prompt_text()
    assert "Nachschlagefeld: \"Österreich\"" in text and "nur Listeneinträge" in text and "Beschriftung: Nr." in text
    with pytest.raises(ValueError):
        LookupSpec()  # weder Werte noch Datenherkunft


def test_change_field_type_keeps_data_and_properties(engine: AccessEngine) -> None:
    tables = engine.tables
    tables.set_field_properties("Kunden", "PLZ", FieldPropertyChanges(description="Postleitzahl"))
    tables.change_field_type("Kunden", "PLZ", AccessType.LONG)
    field = engine.schema().table("Kunden").field("PLZ")
    assert field.type == AccessType.LONG
    assert field.description == "Postleitzahl"
    _, rows = tables.read_sql("SELECT PLZ FROM Kunden WHERE Nachname='Gruber'")
    assert rows == [[6460]]
    tables.change_field_type("Kunden", "PLZ", AccessType.SHORT_TEXT, 8)
    field = engine.schema().table("Kunden").field("PLZ")
    assert (field.type, field.size) == (AccessType.SHORT_TEXT, 8)
    # Text -> Datum mit unpassenden Daten muss sauber scheitern oder Nullen erzeugen, nie abstürzen
    tables.change_field_type("Artikel", "Lagerbestand", AccessType.DOUBLE)
    assert engine.schema().table("Artikel").field("Lagerbestand").type == AccessType.DOUBLE


def test_primary_key_and_indexes(empty_engine: AccessEngine) -> None:
    tables = empty_engine.tables
    tables.create_table(
        "Kurse",
        [FieldSpec(name="KursNr", type="long"), FieldSpec(name="Semester", type="text", size=10),
         FieldSpec(name="Titel", type="text", size=60)],
    )
    assert tables.primary_key("Kurse") == []
    tables.set_primary_key("Kurse", ["KursNr"])
    assert tables.primary_key("Kurse") == ["KursNr"]
    tables.set_primary_key("Kurse", ["KursNr", "Semester"])
    assert tables.primary_key("Kurse") == ["KursNr", "Semester"]
    tables.create_index("Kurse", "idxTitel", ["Titel"], unique=True)
    with pytest.raises(ObjectExistsError):
        tables.create_index("Kurse", "idxTitel", ["Titel"])
    indexes = {i.name: i for i in empty_engine.schema().table("Kurse").indexes}
    assert indexes["idxTitel"].unique is True
    assert indexes["PrimaryKey"].primary is True
    tables.delete_index("Kurse", "idxTitel")
    assert "idxTitel" not in {i.name for i in empty_engine.schema().table("Kurse").indexes}
    tables.set_primary_key("Kurse", [])
    assert tables.primary_key("Kurse") == []


def test_insert_update_delete_records(engine: AccessEngine) -> None:
    tables = engine.tables
    inserted = tables.insert_records(
        "Kunden",
        [
            {"Vorname": "Nina", "Nachname": "Test", "Ort": "Imst", "Bundesland": "Tirol",
             "Geburtsdatum": "01.02.2003", "Newsletter": "ja", "Umsatz": "1.234,50"},
            {"Vorname": "Otto", "Nachname": "Probe", "Ort": "Wien", "Bundesland": "Wien",
             "Geburtsdatum": "2001-12-24", "Newsletter": False, "Umsatz": 10},
        ],
    )
    assert inserted == 2
    assert tables.count_records("Kunden") == 14
    columns, rows = tables.read_sql("SELECT Geburtsdatum, Newsletter, Umsatz FROM Kunden WHERE Nachname='Test'")
    assert columns == ["Geburtsdatum", "Newsletter", "Umsatz"]
    assert rows == [["01.02.2003", True, 1234.5]]

    # Pflichtfeld fehlt -> gesamter Stapel wird zurückgerollt
    with pytest.raises(DataIntegrityError):
        tables.insert_records("Kunden", [{"Vorname": "A", "Nachname": "Ok"}, {"Vorname": "ohne Nachname"}])
    assert tables.count_records("Kunden") == 14
    with pytest.raises(ObjectNotFoundError):
        tables.insert_records("Kunden", [{"GibtEsNicht": 1}])
    with pytest.raises(DataIntegrityError):
        tables.insert_records("Kunden", [{"Nachname": "X", "Geburtsdatum": "kein Datum"}])

    changed = tables.update_records("Kunden", {"Ort": "Innsbruck", "Newsletter": True}, "Nachname = 'Test'")
    assert changed == 1
    _, rows = tables.read_sql("SELECT Ort, Newsletter FROM Kunden WHERE Nachname='Test'")
    assert rows == [["Innsbruck", True]]
    changed = tables.update_records("Artikel", {"Einzelpreis": "=[Einzelpreis]*2"}, "Kategorie='Software'")
    assert changed == 2

    deleted = tables.delete_records("Kunden", "Nachname In ('Test','Probe')")
    assert deleted == 2
    assert tables.count_records("Kunden") == 12


def test_mass_change_requires_confirmation_and_rolls_back(engine: AccessEngine) -> None:
    tables = engine.tables
    with pytest.raises(ApprovalRequiredError) as info:
        tables.delete_records("Kunden", None, max_affected=5)
    assert info.value.affected == 12  # type: ignore[attr-defined]
    assert tables.count_records("Kunden") == 12, "Löschung muss vollständig zurückgerollt sein"
    with pytest.raises(ApprovalRequiredError):
        tables.update_records("Kunden", {"Ort": "X"}, max_affected=3)
    assert tables.count_records("Kunden", "Ort='X'") == 0
    assert tables.delete_records("Kunden", "Bundesland='Wien'", max_affected=5) == 1


def test_where_fragment_is_validated(engine: AccessEngine) -> None:
    with pytest.raises(SqlSyntaxError):
        engine.tables.delete_records("Kunden", "1=1; DROP TABLE Artikel")
    with pytest.raises(SqlSyntaxError):
        engine.tables.count_records("Kunden", "Ort='Imst' ; DELETE FROM Kunden")
    assert engine.tables.table_exists("Artikel")
    # Ein Semikolon innerhalb eines Textwerts ist dagegen erlaubt
    assert engine.tables.count_records("Kunden", "Ort='a;b'") == 0


def test_table_sort_and_filter_properties(engine: AccessEngine) -> None:
    engine.tables.set_table_sort_filter("Kunden", order_by="[Kunden].[Nachname] DESC", filter_expr="[Ort]='Imst'")
    from app.access.tables import get_property

    tabledef = engine.session.db.TableDefs("Kunden")
    assert get_property(tabledef, "OrderBy") == "[Kunden].[Nachname] DESC"
    assert get_property(tabledef, "OrderByOn") is True
    assert get_property(tabledef, "Filter") == "[Ort]='Imst'"


def test_read_rows_limit_and_missing_source(engine: AccessEngine) -> None:
    columns, rows = engine.tables.read_rows("Kunden", limit=5, order_by="Nachname")
    assert len(rows) == 5 and columns[0] == "KundenNr"
    assert rows[0][2] == "Aigner"
    with pytest.raises(ObjectNotFoundError):
        engine.tables.read_rows("Nirgendwo")


def test_attach_files_into_attachment_field(engine: AccessEngine, tmp_path: Path) -> None:
    """te.comp 0207/0302: Fotos in einem Anlagefeld speichern, ein falsches Foto ersetzen – alle oder keine."""
    from app.access.constants import AccessType
    from app.access.specs import FieldSpec
    from app.core.errors import AccessError, ObjectNotFoundError

    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
    a, b, c = (tmp_path / n for n in ("Gruber.png", "Hofer.png", "neu.png"))
    for path in (a, b, c):
        path.write_bytes(png)
    engine.tables.add_field("Kunden", FieldSpec(name="Foto", type=AccessType.ATTACHMENT))
    assert engine.tables.attach_files("Kunden", "Foto", "Nachname", {"Gruber": a, "Hofer": b}) == 2
    assert engine.tables.attachment_names("Kunden", "Foto", "Nachname") == {"Gruber": ["Gruber.png"], "Hofer": ["Hofer.png"]}
    engine.tables.attach_files("Kunden", "Foto", "Nachname", {"Hofer": c}, replace=True)
    assert engine.tables.attachment_names("Kunden", "Foto", "Nachname")["Hofer"] == ["neu.png"]
    with pytest.raises(ObjectNotFoundError):  # unbekannter Schlüssel → nichts wird gespeichert
        engine.tables.attach_files("Kunden", "Foto", "Nachname", {"Bauer": a, "GibtEsNicht": b})
    assert "Bauer" not in engine.tables.attachment_names("Kunden", "Foto", "Nachname")
    with pytest.raises(AccessError):
        engine.tables.attach_files("Kunden", "Ort", "Nachname", {"Gruber": a})

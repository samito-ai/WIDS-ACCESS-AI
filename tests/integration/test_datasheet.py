"""Datenblattansicht von Tabellen und Abfragen einrichten (te.comp 0201, 0203, 0405) – in echtem Access."""
from __future__ import annotations

import pytest

from app.access.engine import AccessEngine
from app.access.specs import DatasheetSpec
from app.core.errors import ObjectNotFoundError

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_table_datasheet_layout_is_stored_and_readable(engine: AccessEngine) -> None:
    """0201/U3: Felder nach vorne, ID ausblenden, zwei Spalten fixieren, Breiten optimieren, andere Zeilenfarbe."""
    engine.tables.set_datasheet_layout("Kunden", DatasheetSpec(
        column_order=["Ort", "PLZ"], hidden=["KundenNr"], frozen=["Nachname", "Vorname"],
        widths_cm={"Strasse": 5.0}, best_fit=["Ort"], alternate_back_color="#FFFF00"))
    table = engine.schema().table("Kunden")
    assert table is not None
    ds = table.datasheet
    assert ds.startswith("Spaltenreihenfolge Nachname, Vorname, Ort, PLZ, KundenNr"), ds
    assert "fixiert: Nachname, Vorname" in ds and "ausgeblendet: KundenNr" in ds
    assert "Strasse 5.0 cm" in ds and "Ort " in ds.split("Spaltenbreiten:")[1]
    assert "alternative Zeilenfarbe #FFFF00" in ds
    assert "(Datenblatt: Spaltenreihenfolge" in engine.schema().to_prompt_text()
    # Gegenprobe in Access selbst: so sieht der Benutzer das Datenblatt
    app = engine.session.application()
    app.DoCmd.OpenTable("Kunden")
    try:
        sheet = app.Screen.ActiveDatasheet
        assert int(sheet.DatasheetAlternateBackColor) == 0x00FFFF  # BGR von #FFFF00
        assert bool(sheet.Controls("KundenNr").ColumnHidden)
        assert int(sheet.Controls("Nachname").ColumnOrder) == 1
    finally:
        app.DoCmd.Close(0, "Kunden", 2)  # acSaveNo


def test_query_field_formats_and_captions(engine: AccessEngine) -> None:
    """0405/U1: Abfragefelder formatieren („3 Personen“, Tausendertrennzeichen) ohne die Abfrage neu anzulegen."""
    engine.queries.create_query("qryUmsatz", sql="SELECT Nachname, Umsatz, Geburtsdatum FROM Kunden")
    engine.tables.set_datasheet_layout("qryUmsatz", DatasheetSpec(
        formats={"Umsatz": "#,##0.00", "Geburtsdatum": 'ddd", "dd.mm.'}, captions={"Nachname": "Familienname"}))
    query = engine.schema().query("qryUmsatz")
    assert query is not None
    assert "Umsatz „#,##0.00“" in query.datasheet and "Familienname" in query.datasheet, query.datasheet
    with pytest.raises(ObjectNotFoundError):
        engine.tables.set_datasheet_layout("qryUmsatz", DatasheetSpec(hidden=["GibtEsNicht"]))
    with pytest.raises(ObjectNotFoundError):
        engine.tables.set_datasheet_layout("Fehlt", DatasheetSpec(hidden=["x"]))
    with pytest.raises(ValueError):
        DatasheetSpec()

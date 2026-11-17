"""Live-Fund „Aktuelle Artikel“ (USt): Access versteht eingebaute Formate nur mit englischem Namen – „Prozent“ wurde
als kaputtes eigenes Format gespeichert (Anzeige „Proze48t“). WIDS übersetzt deutsche Namen überall, wo es ein Format
setzt, und vergleicht in der Prüfung die übersetzte Form."""
from __future__ import annotations

import pytest

from app.access.engine import AccessEngine
from app.access.specs import ControlChange, DatasheetSpec, FieldPropertyChanges, FormChangeSpec, FormSpec
from app.access.tables import get_property
from app.access.values import canonical_format

pytestmark = [pytest.mark.access]


@pytest.mark.parametrize(("german", "english"), [
    ("Prozent", "Percent"), ("Prozentzahl", "Percent"), ("Währung", "Currency"), ("Datum, kurz", "Short Date"),
    ("Festkommazahl", "Fixed"), ("Ja/Nein", "Yes/No"), ("0.0%", "0.0%"), ("nn:ss", "nn:ss"), ("Percent", "Percent"),
])
def test_canonical_format_names(german: str, english: str) -> None:
    assert canonical_format(german) == english


def test_query_table_and_control_formats_are_stored_as_access_names(engine: AccessEngine) -> None:
    engine.queries.create_query("Aktuelle Artikel", "SELECT ArtikelNr, Einzelpreis, 0.2 AS USt FROM Artikel")
    engine.tables.set_datasheet_layout("Aktuelle Artikel", DatasheetSpec(formats={"USt": "Prozent", "Einzelpreis": "Währung"}))
    query = engine.session.db.QueryDefs("Aktuelle Artikel")
    assert get_property(query.Fields("USt"), "Format") == "Percent"
    assert get_property(query.Fields("Einzelpreis"), "Format") == "Currency"
    app = engine.session.application()
    assert app.Eval('Format(0.2,"Percent")') == "20,00%", "Access zeigt das gespeicherte Format wirklich als Prozent"

    engine.tables.set_field_properties("Artikel", "Einzelpreis", FieldPropertyChanges(format="Währung"))
    assert engine.schema().table("Artikel").field("Einzelpreis").format == "Currency"

    engine.objects.create_form(FormSpec(name="frmF", record_source="Artikel", fields=["Einzelpreis"]))
    engine.objects.modify_form("frmF", FormChangeSpec(control_changes=[ControlChange(name="Einzelpreis", format="Prozent")]))
    app.DoCmd.OpenForm("frmF", 1, "", "", -1, 1)  # Entwurf, unsichtbar
    try:
        assert str(app.Forms("frmF").Controls("Einzelpreis").Format) == "Percent"
    finally:
        app.DoCmd.Close(2, "frmF", 2)

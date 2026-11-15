"""Integrationstests: Formulare und Berichte über eine unsichtbare Access-Instanz."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access import constants as c
from app.access.engine import AccessEngine
from app.access.specs import ControlSpec, FormSpec, GroupSpec, ReportSpec, ReportTotalSpec, SortSpec
from app.core.errors import ObjectExistsError, ObjectNotFoundError

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_create_and_inspect_form(engine: AccessEngine) -> None:
    spec = FormSpec(
        name="frmKunden",
        record_source="Kunden",
        title="Kundenverwaltung",
        fields=["Nachname", "Vorname", "Ort", "Newsletter"],
        controls=[
            ControlSpec(type="Kombinationsfeld", field="Bundesland", caption="Land",
                        row_source="SELECT DISTINCT Bundesland FROM Kunden"),
            ControlSpec(type="label", caption="Hinweis: Übungsformular", section="footer"),
        ],
        properties={"NavigationButtons": True, "RecordSelectors": False},
    )
    assert engine.objects.create_form(spec) == "frmKunden"
    assert engine.objects.form_names() == ["frmKunden"]
    info = engine.objects.inspect_form("frmKunden")
    assert info.record_source == "Kunden"
    assert info.caption == "frmKunden"
    assert info.properties["RecordSelectors"] is False
    by_name = {ctl.name: ctl for ctl in info.controls}
    assert by_name["Nachname"].control_type == c.AC_TEXT_BOX
    assert by_name["Nachname"].control_source == "Nachname"
    assert by_name["Newsletter"].control_type == c.AC_CHECK_BOX
    assert by_name["Bundesland"].control_type == c.AC_COMBO_BOX
    assert "DISTINCT" in by_name["Bundesland"].row_source
    assert by_name["lblBundesland"].caption == "Land"
    assert by_name["lblTitel"].caption == "Kundenverwaltung"
    assert by_name["lblTitel"].section == c.AC_HEADER
    # Steuerelemente überlappen nicht (untereinander angeordnet)
    tops = [by_name[n].top for n in ("Nachname", "Vorname", "Ort", "Newsletter", "Bundesland")]
    assert tops == sorted(tops) and len(set(tops)) == 5

    with pytest.raises(ObjectExistsError):
        engine.objects.create_form(spec)
    engine.objects.create_form(FormSpec(name="frmKunden", record_source="Artikel", fields=["Bezeichnung"]), replace=True)
    assert engine.objects.inspect_form("frmKunden").record_source == "Artikel"
    assert engine.objects.form_names() == ["frmKunden"]

    engine.objects.rename("form", "frmKunden", "frmArtikel")
    assert engine.objects.form_names() == ["frmArtikel"]
    engine.objects.delete("form", "frmArtikel")
    assert engine.objects.form_names() == []


def test_form_validation_errors(engine: AccessEngine) -> None:
    with pytest.raises(ObjectNotFoundError):
        engine.objects.create_form(FormSpec(name="f1", record_source="GibtEsNicht", fields=["A"]))
    with pytest.raises(ObjectNotFoundError):
        engine.objects.create_form(FormSpec(name="f1", record_source="Kunden", fields=["Nachnahme"]))
    assert engine.objects.form_names() == []


def test_grouped_report_with_totals(engine: AccessEngine) -> None:
    """Test E: einfacher gruppierter Bericht mit Summen."""
    spec = ReportSpec(
        name="rptUmsatzNachBundesland",
        record_source="Kunden",
        title="Umsatz nach Bundesland",
        fields=["Bundesland", "Nachname", "Ort", "Umsatz"],
        group_by=[GroupSpec(field="Bundesland")],
        sort_by=[SortSpec(field="Nachname", direction="DESC")],
        totals=[ReportTotalSpec(field="Umsatz", function="sum", scope="both"),
                ReportTotalSpec(field="*", function="count", scope="group", label="Anzahl Kunden:")],
    )
    engine.objects.create_report(spec)
    assert engine.objects.report_names() == ["rptUmsatzNachBundesland"]
    info = engine.objects.inspect_report("rptUmsatzNachBundesland")
    assert info.record_source == "Kunden"
    assert [(g.expression, g.has_header, g.has_footer, g.sort_descending) for g in info.group_levels] == [
        ("[Bundesland]", True, True, False),
        ("[Nachname]", False, False, True),
    ]
    by_name = {ctl.name: ctl for ctl in info.controls}
    assert by_name["lblTitel"].caption == "Umsatz nach Bundesland"
    assert by_name["lblTitel"].section == c.AC_HEADER
    assert by_name["Umsatz"].section == c.AC_DETAIL and by_name["Umsatz"].control_source == "Umsatz"
    assert by_name["grpBundesland"].section == c.AC_GROUP_LEVEL1_HEADER
    assert by_name["g0SumUmsatz"].control_source == "=Sum([Umsatz])"
    assert by_name["g0SumUmsatz"].section == c.AC_GROUP_LEVEL1_FOOTER
    assert by_name["g0CountAlle"].control_source == "=Count(*)"
    assert by_name["gesamtSumUmsatz"].section == c.AC_FOOTER
    assert by_name["txtSeite"].section == c.AC_PAGE_FOOTER
    # Summenfeld steht unter der Umsatz-Spalte
    assert by_name["g0SumUmsatz"].left == by_name["Umsatz"].left
    # das Gruppierungsfeld erscheint nicht zusätzlich im Detailbereich
    assert "Bundesland" not in by_name

    with pytest.raises(ObjectExistsError):
        engine.objects.create_report(spec)
    with pytest.raises(ObjectNotFoundError):
        engine.objects.create_report(
            ReportSpec(name="r2", record_source="Kunden", fields=["Nachname"],
                       totals=[ReportTotalSpec(field="Gibtsnicht")])
        )
    engine.objects.rename("Bericht", "rptUmsatzNachBundesland", "rptUmsatz")
    assert engine.objects.report_names() == ["rptUmsatz"]


def test_report_on_query_and_detailed_schema(engine: AccessEngine) -> None:
    engine.queries.create_query("qryTirol", "SELECT Nachname, Ort, Umsatz FROM Kunden WHERE Bundesland='Tirol'")
    engine.objects.create_report(
        ReportSpec(name="rptTirol", record_source="qryTirol", fields=["Nachname", "Ort", "Umsatz"],
                   orientation="landscape", show_date=True)
    )
    engine.objects.create_form(FormSpec(name="frmArtikel", record_source="Artikel", fields=["Bezeichnung", "Einzelpreis"],
                                        default_view="continuous"))
    schema = engine.schema(detailed_objects=True)
    assert [f.name for f in schema.forms] == ["frmArtikel"]
    assert schema.forms[0].detailed and schema.forms[0].default_view == 1
    assert schema.reports[0].record_source == "qryTirol"
    assert engine.object_exists("rptTirol") == "Bericht"
    assert engine.object_exists("frmArtikel") == "Formular"
    assert engine.object_exists("qryTirol") == "Abfrage"
    assert engine.object_exists("Kunden") == "Tabelle"
    assert engine.object_exists("nichts") is None
    # Tabellenoperationen funktionieren nach dem Wechsel auf die Access-Instanz weiterhin
    assert engine.tables.count_records("Kunden") == 12


def test_database_file_is_released_after_close(sample_db_path: Path) -> None:
    engine = AccessEngine.open(sample_db_path, prefer_running=False)
    engine.objects.create_form(FormSpec(name="frmX", record_source="Kunden", fields=["Nachname"]))
    engine.close()
    lock = sample_db_path.with_suffix(".laccdb")
    assert not lock.exists(), "Sperrdatei muss nach dem Schließen verschwunden sein"
    # Datei lässt sich wieder exklusiv öffnen und enthält das Formular
    again = AccessEngine.open(sample_db_path, prefer_running=False)
    try:
        assert again.objects.form_names() == ["frmX"]
    finally:
        again.close()

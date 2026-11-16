"""Unterformulare, Navigationsformulare und bedingte Formatierung – in echtem Access."""
from __future__ import annotations

import pytest

from app.access import constants as c
from app.access.engine import AccessEngine
from app.access.specs import (
    ControlSpec,
    FormatRuleSpec,
    FormSpec,
    NavigationTabSpec,
    RelationFieldPair,
    ReportSpec,
)
from app.core.errors import ObjectExistsError, ObjectNotFoundError

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_subform_with_link_fields(engine: AccessEngine) -> None:
    engine.relations.create_relationship("Kunden", "Bestellungen", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")])
    engine.objects.create_form(FormSpec(name="frmBestellungenUF", record_source="Bestellungen",
                                        fields=["KundenNr", "ArtikelNr", "Menge"], default_view="datasheet"))
    engine.objects.create_form(FormSpec(name="frmKundenHF", record_source="Kunden", fields=["KundenNr", "Nachname"],
                                        controls=[ControlSpec(type="Unterformular", source_object="frmBestellungenUF",
                                                              link_master_fields=["KundenNr"], link_child_fields=["KundenNr"])]))
    info = engine.objects.inspect_form("frmKundenHF")
    sub = next(ctl for ctl in info.controls if ctl.control_type == c.AC_SUBFORM)
    assert sub.source_object.endswith("frmBestellungenUF")
    assert (sub.link_master_fields, sub.link_child_fields) == ("KundenNr", "KundenNr")
    with pytest.raises(ObjectNotFoundError):
        engine.objects.create_form(FormSpec(name="frmX", controls=[ControlSpec(type="subform", source_object="frmFehlt")]))
    with pytest.raises(ValueError):
        ControlSpec(type="subform")


def test_form_control_formatting_is_applied_and_readable(engine: AccessEngine) -> None:
    """te.comp 0301/U1: „ein Feld in Fettschrift“, „ein Feld farblich hinterlegen“, Beschriftung ändern."""
    engine.objects.create_form(FormSpec(name="Golfclubs", record_source="Kunden", fields=["Vorname", "Nachname", "Ort"],
                                        controls=[ControlSpec(field="Nachname", caption="Familienname", bold=True),
                                                  ControlSpec(field="Ort", back_color="#FFFF00", font_size=12, left_cm=14, top_cm=0.3)]))
    info = engine.objects.inspect_form("Golfclubs")
    by_name = {c.name: c for c in info.controls}
    bound = [c.control_source for c in info.controls if c.control_source]
    assert bound == ["Vorname", "Nachname", "Ort"], "Feld in fields UND controls nur einmal, Reihenfolge bleibt"
    assert by_name["Nachname"].formatting == "fett"
    assert "Hintergrund #FFFF00" in by_name["Ort"].formatting
    assert by_name["lblNachname"].caption == "Familienname"
    assert by_name["lblOrt"].left > by_name["lblNachname"].left and by_name["lblOrt"].left < by_name["Ort"].left, \
        "die Beschriftung wandert mit ihrem Feld in die rechte Spalte"
    schema = engine.schema(detailed_objects=True)
    text = schema.to_prompt_text()
    assert "Nachname: fett" in text and "Ort: Hintergrund #FFFF00" in text and "„Familienname“" in text


def test_stacked_layout_column_widths_merge_and_right_column(engine: AccessEngine) -> None:
    """te.comp 0301/U1: Formular mit Steuerelementlayout, rechte Spalte verschmälern, Felder in die rechte Spalte,
    Zellen zusammenführen – und das alles nachweisbar wieder auslesen."""
    from app.access.models import layout_summary

    engine.objects.create_form(FormSpec(
        name="frmLayout", record_source="Kunden", layout="stacked", fields=["Vorname", "Nachname", "Ort", "PLZ"],
        controls=[ControlSpec(field="Ort", layout_column=1), ControlSpec(field="PLZ", layout_column=1)],
        column_widths_cm=[3.0, 4.0, 2.5, 1.8], span_right={"Nachname": 1}))
    info = engine.objects.inspect_form("frmLayout")
    by_name = {c.name: c for c in info.controls}
    assert by_name["Vorname"].layout == 2 and by_name["Vorname"].layout_id, "gestapeltes Steuerelementlayout"
    assert by_name["Ort"].layout_id != by_name["Vorname"].layout_id and by_name["Ort"].left > by_name["Vorname"].left, \
        "Ort und PLZ stehen in der rechten Feldspalte (eigenes Layout daneben)"
    assert abs(by_name["lblVorname"].width - 3 * 567) < 60, "Breite der Beschriftungsspalte"
    assert abs(by_name["Ort"].width - 1.8 * 567) < 60, "rechte Feldspalte verschmälert"
    summary = " ".join(layout_summary(info.controls))
    assert "gestapelt" in summary and "Nachname über 2 Spalten" in summary, summary
    assert "Spaltenbreiten 1: 3.0 cm, 2: 4.0 cm" in summary, summary
    with pytest.raises(ValueError):
        FormSpec(name="x", column_widths_cm=[3.0])  # nur mit layout „stacked“


def test_navigation_form(engine: AccessEngine) -> None:
    engine.objects.create_form(FormSpec(name="frmKunden", record_source="Kunden", fields=["Nachname"]))
    engine.objects.create_report(ReportSpec(name="rptArtikel", record_source="Artikel", fields=["Bezeichnung"]))
    tabs = [NavigationTabSpec(caption="Kunden", target="frmKunden"),
            NavigationTabSpec(caption="Artikel", target="rptArtikel", target_type="report")]
    assert engine.objects.create_navigation_form("Navigation", tabs, title="Übersicht") == "Navigation"
    info = engine.objects.inspect_form("Navigation")
    kinds = {ctl.control_type for ctl in info.controls}
    assert {c.AC_NAVIGATION_CONTROL, c.AC_NAVIGATION_BUTTON, c.AC_SUBFORM} <= kinds
    # Access ergänzt selbst eine leere Schaltfläche „[Neues hinzufügen]“
    targets = sorted(ctl.navigation_target for ctl in info.controls
                     if ctl.control_type == c.AC_NAVIGATION_BUTTON and ctl.navigation_target)
    assert targets == ["frmKunden", "rptArtikel"]
    with pytest.raises(ObjectExistsError):
        engine.objects.create_navigation_form("Navigation", tabs)
    with pytest.raises(ObjectNotFoundError):
        engine.objects.create_navigation_form("Nav2", [NavigationTabSpec(caption="x", target="frmFehlt")])
    assert "Nav2" not in engine.objects.form_names(), "nach einem Fehler bleibt kein halbes Formular zurück"


def test_conditional_formatting_form_and_report(engine: AccessEngine) -> None:
    engine.objects.create_form(FormSpec(name="frmArtikel", record_source="Artikel", fields=["Bezeichnung", "Einzelpreis"]))
    rules = [FormatRuleSpec(operator="greater", value="100", back_color="#FF0000", bold=True),
             FormatRuleSpec(expression="[Einzelpreis] Between 10 And 20", fore_color="#0000FF")]
    assert engine.objects.set_conditional_format("form", "frmArtikel", "Einzelpreis", rules) == 2
    found = engine.objects.conditional_formats("form", "frmArtikel")["Einzelpreis"]
    assert found[0].startswith("Feldwert greater 100") and "#FF0000" in found[0] and "fett" in found[0]
    assert "Between 10 And 20" in found[1] and "Schrift #0000FF" in found[1]
    assert engine.objects.set_conditional_format("form", "frmArtikel", "Einzelpreis", rules[:1], replace=True) == 1
    engine.objects.create_report(ReportSpec(name="rptPreise", record_source="Artikel", fields=["Bezeichnung", "Einzelpreis"]))
    engine.objects.set_conditional_format("report", "rptPreise", "Einzelpreis",
                                          [FormatRuleSpec(operator="between", value="1", value2="50", back_color="#FFFF00")])
    report = engine.objects.inspect_report("rptPreise")
    ctl = next(x for x in report.controls if x.name == "Einzelpreis")
    assert ctl.conditions and "between 1 und 50" in ctl.conditions[0]
    with pytest.raises(ObjectNotFoundError):
        engine.objects.set_conditional_format("form", "frmArtikel", "GibtEsNicht", rules)
    with pytest.raises(ValueError):
        FormatRuleSpec(expression="[A]>1")  # keine Formatierung angegeben


def test_report_sections_filter_sort_new_page_and_calculated_controls(engine: AccessEngine) -> None:
    """te.comp 0503/0702/0703: Filter und Sortierung im BERICHT, jede Gruppe auf neuer Seite, Detailbereich aus,
    berechnete Felder im Gruppenfuß und Seitenfuß, Trennlinie – alles wieder auslesbar."""
    from app.access.specs import GroupSpec, ReportControlSpec

    spec = ReportSpec(
        name="rptKundenNachLand", record_source="Kunden", fields=["Nachname", "Ort", "Umsatz"],
        group_by=[GroupSpec(field="Bundesland", new_page=True, keep_together=True)],
        filter='[Newsletter]=True', order_by="[Nachname]", hide_detail=True,
        controls=[ReportControlSpec(section="group_footer", expression='=Count(*) & " Kunden"', caption="Anzahl:", bold=True),
                  ReportControlSpec(section="page_footer", expression="=Now()", format="dd.mm.yyyy hh:nn", left_cm=10),
                  ReportControlSpec(type="line", section="group_footer", top_cm=0.8),
                  ReportControlSpec(type="label", section="report_header", caption="Kundenliste", font_size=16)])
    engine.objects.create_report(spec)
    info = engine.objects.inspect_report("rptKundenNachLand")
    assert info.group_levels[0].new_page and info.group_levels[0].keep_together
    page = info.properties.pop("Seite")
    assert page.startswith("A4 hoch, Ränder"), page
    assert {k: v for k, v in info.properties.items() if k != "Bereichsfarben"} == {
        "Filter": "[Newsletter]=True", "OrderBy": "[Nachname]", "Detailbereich": "ausgeblendet"}
    sources = {ctl.control_source for ctl in info.controls}
    assert '=Count(*) & " Kunden"' in sources and "=Now()" in sources
    assert any(ctl.control_type == c.AC_LINE for ctl in info.controls)
    assert any(ctl.caption == "Kundenliste" for ctl in info.controls)
    text = engine.schema(detailed_objects=True).to_prompt_text()
    assert "(neue Seite je Gruppe)" in text and "Bericht-Filter: [Newsletter]=True" in text
    with pytest.raises(ValueError):
        ReportSpec(name="x", record_source="Kunden", fields=["Ort"],
                   controls=[ReportControlSpec(section="group_footer", expression="=1")])  # keine Gruppe


def test_report_controls_without_position_do_not_overlap(engine: AccessEngine) -> None:
    """Codex-Review: mehrere Zusatzfelder ohne Position im selben Bereich werden untereinander gestapelt."""
    from app.access.specs import ReportControlSpec

    engine.objects.create_report(ReportSpec(name="rptStapel", record_source="Kunden", fields=["Nachname"],
                                            controls=[ReportControlSpec(section="page_footer", expression="=Date()"),
                                                      ReportControlSpec(section="page_footer", expression="=Time()")]))
    info = engine.objects.inspect_report("rptStapel")
    date_box = next(x for x in info.controls if x.control_source == "=Date()")
    time_box = next(x for x in info.controls if x.control_source == "=Time()")
    assert time_box.top >= date_box.top + date_box.height, (date_box.top, time_box.top)


def test_modify_existing_report(engine: AccessEngine) -> None:
    """te.comp 0503/0702/0703: einen VORHANDENEN Bericht filtern, sortieren, je Gruppe neue Seite, Summenfeld im
    Gruppenfuß ergänzen, Beschriftung ändern und Feld verschieben – ohne ihn neu anzulegen."""
    from app.access.specs import ControlChange, GroupSpec, ReportChangeSpec, ReportControlSpec

    engine.objects.create_report(ReportSpec(name="Telefonliste", record_source="Kunden", fields=["Nachname", "Ort"],
                                            group_by=[GroupSpec(field="Bundesland", footer=False)]))
    engine.objects.modify_report("Telefonliste", ReportChangeSpec(
        filter='[Newsletter]=True', order_by="[Nachname]",
        groups=[GroupSpec(field="Bundesland", new_page=True)],
        controls=[ReportControlSpec(section="group_footer", expression='=Count(*) & " Kunden"')],
        control_changes=[ControlChange(name="lblNachname", caption="Familienname", bold=True),
                         ControlChange(name="Ort", left_cm=9.0)]))
    info = engine.objects.inspect_report("Telefonliste")
    assert info.properties.get("Filter") == "[Newsletter]=True" and info.properties.get("OrderBy") == "[Nachname]"
    assert len(info.group_levels) == 1 and info.group_levels[0].new_page
    summe = next(x for x in info.controls if x.control_source == '=Count(*) & " Kunden"')
    assert summe.section == c.AC_GROUP_LEVEL1_HEADER, "ohne Gruppenfuß landet die Gruppensumme im Gruppenkopf"
    by_name = {x.name: x for x in info.controls}
    assert by_name["lblNachname"].caption == "Familienname"
    assert abs(by_name["Ort"].left - 9 * 567) < 30
    assert '=Count(*) & " Kunden"' in {x.control_source for x in info.controls}
    with pytest.raises(ObjectNotFoundError):
        engine.objects.modify_report("Telefonliste", ReportChangeSpec(control_changes=[ControlChange(name="Fehlt", bold=True)]))
    assert engine.objects.inspect_report("Telefonliste").properties.get("Filter") == "[Newsletter]=True", \
        "ein Fehler lässt den gespeicherten Bericht unverändert"


def test_modify_existing_form(engine: AccessEngine) -> None:
    """te.comp 0301/U2, 0802, 0803: vorhandenes Formular – Felder löschen, Formkontur/Umrandung, Schriftfarbe und
    -größe, verkettetes Feld ergänzen, PopUp, geteiltes Formular ohne Trennleiste."""
    from app.access.specs import ControlChange, FormChangeSpec, ReportControlSpec

    engine.objects.create_form(FormSpec(name="Personal", record_source="Kunden",
                                        fields=["Vorname", "Nachname", "Ort", "PLZ"]))
    engine.objects.modify_form("Personal", FormChangeSpec(
        caption="Personaldetails", default_view="split",
        properties={"PopUp": True, "SplitFormSplitterBar": False},
        delete_controls=["PLZ"],
        control_changes=[ControlChange(name="Ort", border_style=0, fore_color="#FF0000", font_size=14),
                         ControlChange(name="Nachname", border_width=2, border_color="#0000FF")],
        controls=[ReportControlSpec(expression='=[Vorname] & " " & [Nachname]', caption="Name", top_cm=4)]))
    info = engine.objects.inspect_form("Personal")
    names = {x.name for x in info.controls}
    assert "PLZ" not in names and info.caption == "Personaldetails" and info.default_view == 5
    assert info.properties.get("PopUp") is True, info.properties
    assert '=[Vorname] & " " & [Nachname]' in {x.control_source for x in info.controls}
    app = engine.session.application()
    app.DoCmd.OpenForm("Personal", 1, "", "", -1, 1)  # Entwurf, verborgen – Werte direkt in Access gegenprüfen
    try:
        form = app.Forms("Personal")
        assert int(form.Controls("Ort").BorderStyle) == 0 and int(form.Controls("Ort").FontSize) == 14
        assert int(form.Controls("Nachname").BorderWidth) == 2
        assert not bool(form.SplitFormSplitterBar)
    finally:
        app.DoCmd.Close(2, "Personal", 2)
    with pytest.raises(ValueError):
        FormChangeSpec(controls=[ReportControlSpec(section="page_footer", expression="=1")])


def test_data_bars(engine: AccessEngine) -> None:
    """te.comp 0807/U3/U4: Datenwerte als Balken, Unter- und Obergrenze als Zahl, Balkenfarbe."""
    engine.objects.create_form(FormSpec(name="frmUmsatz", record_source="Kunden", fields=["Nachname", "Umsatz"],
                                        default_view="datasheet"))
    rule = FormatRuleSpec(data_bar=True, back_color="#00B050", shortest_value=0, longest_value=5000)
    assert engine.objects.set_conditional_format("form", "frmUmsatz", "Umsatz", [rule]) == 1
    found = engine.objects.conditional_formats("form", "frmUmsatz")["Umsatz"][0]
    assert found.startswith("Datenbalken kürzester Zahl 0") and "längster Zahl 5000" in found, found
    assert "#00B050" in found
    with pytest.raises(ValueError):
        FormatRuleSpec(data_bar=True, expression="[Umsatz]>1")


def test_tab_control_with_fields_and_subform(engine: AccessEngine) -> None:
    """te.comp 0805: Registersteuerelement – alle Felder auf Seite 1, Formular „Anmeldungen“ auf Seite 2."""
    from app.access.specs import FormChangeSpec, TabControlSpec, TabPageSpec

    engine.objects.create_form(FormSpec(name="Anmeldungen", record_source="Kunden", fields=["Nachname", "Ort"],
                                        default_view="datasheet"))
    engine.objects.create_form(FormSpec(name="Kursprogramm", record_source="Kunden", fields=["Vorname", "Nachname", "Ort"]))
    engine.objects.modify_form("Kursprogramm", FormChangeSpec(tab_control=TabControlSpec(pages=[
        TabPageSpec(caption="Kurs", fields=["Vorname", "Nachname", "Ort"]),
        TabPageSpec(caption="Anmeldungen", subform="Anmeldungen")])))
    info = engine.objects.inspect_form("Kursprogramm")
    pages = {x.name: x.caption for x in info.controls if x.control_type == c.AC_TAB_PAGE}
    assert sorted(pages.values()) == ["Anmeldungen", "Kurs"]
    by_caption = {cap: name for name, cap in pages.items()}
    on_first = sorted(x.control_source for x in info.controls if x.parent == by_caption["Kurs"] and x.control_source)
    assert on_first == ["Nachname", "Ort", "Vorname"]
    assert [x.control_source for x in info.controls if x.control_source and x.parent == "Kursprogramm"] == [], \
        "die Felder stehen nur noch im Register"
    sub = next(x for x in info.controls if x.control_type == c.AC_SUBFORM)
    assert sub.parent == by_caption["Anmeldungen"] and sub.source_object.endswith("Anmeldungen")
    assert "Register: Seite „Kurs“" in engine.schema(detailed_objects=True).to_prompt_text()


def test_columnar_report_layout(engine: AccessEngine) -> None:
    """te.comp 0501/U6: Bericht im Spaltenlayout – Beschriftung links, Feld rechts, untereinander im Detailbereich."""
    engine.objects.create_report(ReportSpec(name="rptSpalten", record_source="Kunden", layout="columnar",
                                            fields=["Vorname", "Nachname", "Ort"]))
    info = engine.objects.inspect_report("rptSpalten")
    boxes = [x for x in info.controls if x.control_source in ("Vorname", "Nachname", "Ort")]
    labels = {x.caption: x for x in info.controls if x.control_type == 100 and x.section == 0}
    assert all(b.section == 0 for b in boxes) and sorted(labels) == ["Nachname", "Ort", "Vorname"]
    assert boxes[0].top < boxes[1].top < boxes[2].top, "untereinander"
    assert all(labels[b.control_source].left < b.left for b in boxes), "Beschriftung links vom Feld"

"""Etikettenberichte, Seite einrichten, Kopfzeilen-Titel und Excel-Formatierung – in echtem Access/Excel.

Druckeinstellungen werden nur im jeweiligen Bericht gespeichert; geprüft wird zusätzlich, dass der
Standarddrucker von Windows und Access unverändert bleibt. Gedruckt wird nie – gezählt wird in der Seitenansicht.
"""
from __future__ import annotations

import math
import winreg
from pathlib import Path
from typing import Any

import pytest

from app.access.engine import AccessEngine
from app.access.specs import (
    ControlChange,
    FormChangeSpec,
    FormSpec,
    LabelSpec,
    PageSetupSpec,
    ReportChangeSpec,
    ReportControlSpec,
    ReportSpec,
    SortSpec,
)
from app.agent.executor import StepResult
from app.agent.verifier import ResultVerifier
from app.core.errors import AccessError
from app.tasks import models as m

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def _verify(engine: AccessEngine, action: Any) -> list[tuple[str, bool, str]]:
    step = m.PlanStep(id=1, title="Prüfung", action=action)
    result = StepResult(step_id=1, message="", details={})
    checks = ResultVerifier().verify(engine, step, result).checks
    return [(x.name, x.passed, x.detail) for x in checks]


def _default_printer() -> str:
    import win32print

    return str(win32print.GetDefaultPrinter())


def _labels(**overrides: Any) -> ReportSpec:
    values: dict[str, Any] = dict(
        name="Etiketten", record_source="Kunden", fields=["Vorname", "Nachname", "Strasse", "PLZ", "Ort"],
        sort_by=[SortSpec(field="PLZ")], labels=LabelSpec(type="Avery J8163"),
        controls=[ReportControlSpec(type="label", caption="Österreichische Post AG Info.Mail Entgelt bezahlt", font_size=7),
                  ReportControlSpec(expression='=[Vorname] & " " & [Nachname]', top_cm=0.9),
                  ReportControlSpec(expression="Strasse"),
                  ReportControlSpec(expression='=[PLZ] & " " & [Ort]')])
    values.update(overrides)
    return ReportSpec(**values)


def test_label_report_avery_j8163(engine: AccessEngine) -> None:
    """te.comp 0502: Avery J8163, sortiert nach PLZ, Info.Mail-Hinweis in kleinerer Schrift."""
    printer = _default_printer()
    spec = _labels()
    engine.objects.create_report(spec)
    page = engine.objects.page_setup("Etiketten")
    assert (page["ItemsAcross"], page["DefaultSize"], page["Orientation"], page["PaperSize"]) == (2, 0, 1, 9), page
    assert abs(page["ItemSizeWidth"] - 9.91 * 567) < 15 and abs(page["ItemSizeHeight"] - 3.81 * 567) < 15
    assert engine.objects.preview_pages("Etiketten") == (1, ""), "12 Kunden auf einem Bogen mit 14 Etiketten"
    checks = _verify(engine, m.CreateReport(report=spec))
    assert all(ok for _, ok, _ in checks), checks
    info = engine.objects.inspect_report("Etiketten")
    assert "Etiketten 2 nebeneinander" in info.properties["Seite"], info.properties
    assert "[PLZ]" in {g.expression for g in info.group_levels}, "sortiert nach Postleitzahl"
    assert _default_printer() == printer, "Standarddrucker unverändert"


def test_labels_per_sheet_are_counted(engine: AccessEngine) -> None:
    """L7165 = 8 Etiketten je Bogen: 12 Kunden ergeben genau 2 Seiten – zählt Access in der Seitenansicht."""
    spec = _labels(name="Gross", labels=LabelSpec(type="avery l7165"))
    engine.objects.create_report(spec)
    assert engine.objects.preview_pages("Gross") == (math.ceil(12 / 8), "")
    checks = _verify(engine, m.CreateReport(report=spec))
    assert all(ok for _, ok, _ in checks), checks


def test_label_report_rejects_overfull_or_unknown_labels(engine: AccessEngine) -> None:
    lines = [ReportControlSpec(expression="Nachname", font_size=20) for _ in range(6)]
    with pytest.raises(AccessError, match="passen nicht auf das Etikett"):
        engine.objects.create_report(_labels(name="Voll", controls=lines))
    assert not engine.objects.report_exists("Voll"), "nichts Halbes gespeichert"
    with pytest.raises(AccessError, match="breiter als das Etikett"):
        engine.objects.create_report(_labels(name="Breit", controls=[ReportControlSpec(expression="Ort", width_cm=12)]))
    assert not engine.objects.report_exists("Breit")
    with pytest.raises(ValueError, match="unbekannt"):
        LabelSpec(type="Herma 4711")
    with pytest.raises(ValueError, match="nicht auf A4"):
        LabelSpec(width_cm=12, height_cm=4, across=2, down=5)
    with pytest.raises(ValueError, match="Etikettenbericht"):
        _labels(group_by=[{"field": "Ort"}])


def test_page_setup_fits_report_on_one_page(engine: AccessEngine) -> None:
    """te.comp 0503/U1, 0504/U1: „alle Daten auf einer Seite“ – Querformat, schmale Ränder, Seiten nachgezählt."""
    printer = _default_printer()
    app = engine.session.application()
    access_printer = (int(app.Printer.Orientation), int(app.Printer.TopMargin))
    engine.objects.create_report(ReportSpec(name="Liste", record_source="Kunden", show_page_numbers=False,
                                            fields=["Vorname", "Nachname", "Ort"]))
    wide = ReportChangeSpec(controls=[ReportControlSpec(expression="Bundesland", left_cm=17.5, top_cm=0, width_cm=5)])
    engine.objects.modify_report("Liste", wide)
    fit_only = ReportChangeSpec(page_setup=PageSetupSpec(max_pages=1))
    before = {n: ok for n, ok, _ in _verify(engine, m.ModifyReport(name="Liste", changes=fit_only))}
    # 22,5 cm breit, A4 hoch druckbar 19,7 cm – Access zählt trotzdem 1 Seite (Pages zählt nur nach unten)
    assert before == {"Berichtsbreite passt auf die Seite": False, "Höchstens 1 Seite(n)": True}, before
    change =ReportChangeSpec(page_setup=PageSetupSpec(orientation="landscape", margin_left_cm=1, margin_right_cm=1,
                                                       margin_top_cm=1.5, max_pages=1))
    engine.objects.modify_report("Liste", change)
    checks = _verify(engine, m.ModifyReport(name="Liste", changes=change))
    assert all(ok for _, ok, _ in checks), checks
    assert {"Ausrichtung", "Seitenrand links", "Höchstens 1 Seite(n)"} <= {name for name, _, _ in checks}
    assert (int(app.Printer.Orientation), int(app.Printer.TopMargin)) == access_printer, "Access-Drucker unverändert"
    assert _default_printer() == printer


def test_page_count_is_refused_when_preview_could_block(engine: AccessEngine) -> None:
    """Gemessen: ein unbekannter Name öffnet in der unsichtbaren Instanz die Parameterabfrage und blockiert Access.
    WIDS zählt dann nicht, sondern meldet den Grund – und die Prüfung gilt NICHT als bestanden."""
    engine.objects.create_report(ReportSpec(name="Kaputt", record_source="Kunden", fields=["Nachname"]))
    change = ReportChangeSpec(controls=[ReportControlSpec(expression="=[Gibtsnicht] & 'x'")],
                              page_setup=PageSetupSpec(max_pages=1))
    engine.objects.modify_report("Kaputt", change)
    pages, reason = engine.objects.preview_pages("Kaputt")
    assert pages is None and "gibtsnicht" in reason, reason
    checks = _verify(engine, m.ModifyReport(name="Kaputt", changes=change))
    assert ("Seitenzahl in der Seitenansicht", False) in {(n, ok) for n, ok, _ in checks}, checks


def test_page_count_never_deletes_an_existing_report(engine: AccessEngine) -> None:
    """Heißt ein Bericht des Benutzers zufällig wie die temporäre Zählkopie, wird nicht gezählt statt gelöscht."""
    def names() -> set[str]:  # roh, inkl. „~“-Namen, die WIDS sonst ausblendet
        docs = engine.session.db.Containers("Reports").Documents
        docs.Refresh()
        return {str(d.Name) for d in docs}

    engine.objects.create_report(ReportSpec(name="Eigen", record_source="Kunden", fields=["Ort"]))
    engine.session.application().DoCmd.Rename("~WIDS_Seitenzaehlung", 3, "Eigen")
    engine.objects.create_report(ReportSpec(name="Liste", record_source="Kunden", fields=["Nachname"]))
    pages, reason = engine.objects.preview_pages("Liste")
    assert pages is None and "bereits vergeben" in reason
    assert "~WIDS_Seitenzaehlung" in names(), "Bericht des Benutzers nicht gelöscht"
    engine.session.application().DoCmd.DeleteObject(3, "~WIDS_Seitenzaehlung")
    assert engine.objects.preview_pages("Liste") == (1, "")
    assert "~WIDS_Seitenzaehlung" not in names(), "Zählkopie wieder entfernt"


def test_form_header_title_without_vba(engine: AccessEngine) -> None:
    """te.comp 0803/U1 („Titelleiste“ zeigt den Namen): Lösung laut te.comp-Video = Textfeld im Formularkopf,
    transparent, ohne Rahmen, weiße Schrift, nicht anklickbar – kein VBA nötig."""
    engine.objects.create_form(FormSpec(name="Personaldetails", record_source="Kunden", fields=["Vorname", "Nachname"]))
    change = FormChangeSpec(
        properties={"PopUp": True},
        controls=[ReportControlSpec(section="report_header", name="txtTitel", font_size=18,
                                    expression='=[Vorname] & " " & [Nachname]', width_cm=10)],
        control_changes=[ControlChange(name="txtTitel", transparent=True, border_style=0, fore_color="#FFFFFF",
                                       enabled=False, locked=True)])
    engine.objects.modify_form("Personaldetails", change)
    title = next(x for x in engine.objects.inspect_form("Personaldetails").controls if x.name == "txtTitel")
    assert {"transparent", "ohne Rahmen", "nicht aktiviert", "gesperrt"} <= set(title.formatting.split(", "))
    checks = _verify(engine, m.ModifyForm(name="Personaldetails", changes=change))
    assert all(ok for _, ok, _ in checks), checks
    app = engine.session.application()
    app.DoCmd.OpenForm("Personaldetails", 0, "", "", -1, 1)  # Formularansicht, verborgen: Ausdruck wirklich rechnen
    try:
        assert app.Forms("Personaldetails").Controls("txtTitel").Value == "Anna Gruber"
    finally:
        app.DoCmd.Close(2, "Personaldetails", 2)


def _excel_installed() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CurVer"))
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _excel_installed(), reason="Excel nicht installiert")
def test_excel_formatting_after_export(engine: AccessEngine, tmp_path: Path) -> None:
    """te.comp 0205/U4: Textumbruch aller Daten aus, Spaltenbreiten optimal – geprüft direkt in der xlsx-Datei."""
    from app.access import excel

    path = tmp_path / "Kunden.xlsx"
    engine.tables.export_data("Kunden", path)
    excel.format_workbook(path, wrap_text=True)  # Ausgangslage wie nach „Kopieren und Einfügen“
    assert excel.workbook_layout(path)["wrapped_cells"] > 0
    excel.format_workbook(path, wrap_text=False, autofit=True)
    layout = excel.workbook_layout(path)
    assert layout["wrapped_cells"] == 0
    longest, widths = layout["longest_text"], layout["widths"]
    assert set(longest) <= set(widths), (longest, widths)
    strasse = next(col for col, n in longest.items() if n == len("Lindenallee 21"))
    assert len("Lindenallee 21") * 0.7 < widths[strasse] < len("Lindenallee 21") * 1.5, widths
    action = m.ExportData(source="Kunden", file="Kunden.xlsx", excel_no_wrap=True, excel_autofit=True)
    step = m.PlanStep(id=1, title="Export", action=action)
    verification = ResultVerifier().verify(engine, step, StepResult(step_id=1, message="", details={"path": str(path)}))
    assert verification.passed, [(x.name, x.detail) for x in verification.checks]
    with pytest.raises(ValueError, match=r"nur beim Export nach .xlsx"):
        m.ExportData(source="Kunden", file="Kunden.csv", excel_autofit=True)

"""Regressionstests zu den Befunden des Codex-Bughunts (12.11.2026)."""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

from app.access import importer
from app.access.models import DatabaseSchema, RelationInfo
from app.agent.diff import schema_diff
from app.core.settings import AppSettings
from app.security.policy import SafetyPolicy

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def test_whitelist_phrase_does_not_disable_exam_keywords() -> None:
    policy = SafetyPolicy(AppSettings().security)
    assert policy.check_environment(["te.comp Zertifizierung - Automatische Prüfung"], []).blocked
    assert policy.check_environment(["Prüfungsmodus: Lösung überprüfen"], []).blocked
    assert not policy.check_environment(["te.comp-training", "Automatische Prüfung"], []).blocked


def test_changed_relationship_settings_appear_in_diff() -> None:
    rel = RelationInfo(name="r", table="Kunden", foreign_table="Auftraege", fields=[("KNr", "KNr")], enforce_integrity=False)
    before = DatabaseSchema(path="", name="x", relations=[rel])
    after = DatabaseSchema(path="", name="x", relations=[rel.model_copy(update={"enforce_integrity": True,
                                                                                 "cascade_delete": True})])
    assert schema_diff(before, after) == [
        "geändert: Beziehung Kunden → Auftraege (enforce_integrity: False → True, cascade_delete: False → True)"]


def test_datasheet_changes_of_tables_and_queries_appear_in_diff() -> None:
    """Live-Läufe 14.11.: Formate einer Abfrage (0405/U2) und Datenblatt-Layout einer Tabelle (0201/U3) wurden
    korrekt gesetzt, die Änderungsliste meldete aber „nichts geändert“."""
    from app.access.models import QueryInfo, TableInfo

    widths = "Spaltenbreiten: " + ", ".join(f"Feld{i} 2.0 cm" for i in range(200))  # unverändert, lang
    query = QueryInfo(name="Aktuelle Artikel", sql="SELECT 1", datasheet=f"{widths}; Formate: USt „Percent“")
    table = TableInfo(name="Hotels", datasheet="")
    before = DatabaseSchema(path="", name="x", queries=[query], tables=[table])
    after = DatabaseSchema(path="", name="x",
                           queries=[query.model_copy(update={"datasheet": f"{widths}; Formate: USt „0%“"})],
                           tables=[table.model_copy(update={"datasheet": "ausgeblendet: ID"})])
    assert schema_diff(before, after) == [  # nur die geänderten Teile, nicht die unveränderten Breiten
        "Datenblatt von „Hotels“: (nichts) → ausgeblendet: ID",
        "Datenblatt der Abfrage „Aktuelle Artikel“: Formate: USt „Percent“ → Formate: USt „0%“"]
    assert schema_diff(before, before) == []
    long_change = DatabaseSchema(path="", name="x", queries=[query.model_copy(update={"datasheet": "Formate: " + "x" * 900})])
    assert all(len(line) < 800 for line in schema_diff(before, long_change)), "gekürzt"


def _xlsx(path: Path, sheet_xml: str, *, date1904: bool) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        pr = '<workbookPr date1904="1"/>' if date1904 else ""
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{_MAIN}" xmlns:r="{_R}">{pr}'
                                      '<sheets><sheet name="T" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/styles.xml", f'<styleSheet xmlns="{_MAIN}"><cellXfs count="2"><xf numFmtId="0"/>'
                                    '<xf numFmtId="14"/></cellXfs></styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{_MAIN}"><sheetData>{sheet_xml}</sheetData></worksheet>')
    return path


def test_xlsx_cells_without_ref_iso_dates_and_1904(tmp_path: Path) -> None:
    rows = importer.read_rows(_xlsx(tmp_path / "a.xlsx",
                                    '<row><c t="inlineStr"><is><t>a</t></is></c><c t="inlineStr"><is><t>b</t></is></c>'
                                    '<c t="d"><v>2024-01-01T00:00:00</v></c></row>', date1904=False), has_header=False)
    assert rows == [["a", "b", dt.datetime(2024, 1, 1)]]
    rows = importer.read_rows(_xlsx(tmp_path / "b.xlsx", '<row><c r="A1" s="1"><v>43830</v></c></row>', date1904=True),
                              has_header=False)
    assert rows == [[dt.datetime(2024, 1, 1)]]


def test_offline_tests_cannot_reach_the_ai_api() -> None:
    """Nachweis „Offline-Tests kosten nichts“: der echte Anthropic-Client kommt in Offline-Läufen nicht ins Netz."""
    import os

    import anthropic
    import pytest

    if os.environ.get("WIDS_LIVE_AI") == "1":
        pytest.skip("nur im Offline-Lauf")
    client = anthropic.Anthropic(api_key="sk-ant-offline-test", max_retries=0)
    with pytest.raises(anthropic.APIConnectionError) as info:
        client.messages.create(model="claude-opus-5-5", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert "gesperrt" in repr(info.value.__cause__) or "gesperrt" in str(info.value.__context__)
    import openai

    with pytest.raises((openai.APIConnectionError, RuntimeError), match=r"gesperrt|Connection"):
        openai.OpenAI(api_key="sk-offline-test", max_retries=0).models.list()

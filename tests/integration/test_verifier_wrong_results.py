"""Absichtlich falsche, aber ähnlich aussehende Ergebnisse – der Verifier muss sie ablehnen (Codex-Audit 16.11.).

Die falschen Zustände werden direkt in Access/DAO hergestellt, nicht über den Executor: die Tests übernehmen so nicht
dessen Annahmen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.access.engine import AccessEngine
from app.access.specs import FieldSpec, FormSpec, ReportChangeSpec, ReportSpec
from app.agent.executor import AccessExecutor, StepResult
from app.agent.verifier import ResultVerifier
from app.core.errors import ObjectExistsError
from app.tasks import models as m
from app.utils.sampledb import create_sample_database

pytestmark = pytest.mark.access_app


@pytest.fixture
def engine(tmp_path: Path) -> Any:
    eng = AccessEngine.open(create_sample_database(tmp_path / "Falsch.accdb"), prefer_running=False)
    yield eng
    eng.close()


def _step(action: dict) -> m.PlanStep:
    return m.PlanStep.model_validate({"id": 1, "title": "t", "action": action})


def _failed(engine: AccessEngine, step: m.PlanStep, result: StepResult) -> list[str]:
    return [c.name for c in ResultVerifier().verify(engine, step, result).failed_checks]


def test_right_columns_wrong_criteria_are_rejected(engine: AccessEngine) -> None:
    """Spalten stimmen, aber die gespeicherte Bedingung liefert andere Datensätze (z. B. nach einer SQL-Reparatur)."""
    step = _step({"action": "create_query", "name": "qryTirol", "spec": {"tables": ["Kunden"], "fields": [
        {"field": "Nachname"}, {"field": "Bundesland", "criteria": "\"Tirol\""}]}})
    engine.queries.create_query("qryTirol", "SELECT Nachname, Bundesland FROM Kunden WHERE Bundesland=\"Wien\"")
    assert "Ergebnis wie verlangt" in _failed(engine, step, StepResult(step_id=1, final_name="qryTirol"))


def test_result_comparison_still_runs_when_database_is_opened_exclusively(engine: AccessEngine,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Beta-3-Live-Fund: ist die Datei exklusiv geöffnet, scheitert die zweite DAO-Verbindung – der Vergleich darf
    trotzdem nicht entfallen (Rückfall auf die bestehende Verbindung), eine falsche Bedingung fällt weiter auf."""
    from app.core.errors import AccessError

    def locked(*a: Any, **k: Any) -> Any:
        raise AccessError("Die Datenbank wurde in einen Status versetzt, in dem sie nicht geöffnet werden kann.")

    monkeypatch.setattr(engine.queries, "run_select_isolated", locked)
    step = _step({"action": "create_query", "name": "qryTirol", "spec": {"tables": ["Kunden"], "fields": [
        {"field": "Nachname"}, {"field": "Bundesland", "criteria": "\"Tirol\""}]}})
    engine.queries.create_query("qryTirol", "SELECT Nachname, Bundesland FROM Kunden WHERE Bundesland=\"Wien\"")
    failed = _failed(engine, step, StepResult(step_id=1, final_name="qryTirol"))
    assert "Ergebnis wie verlangt" in failed and "Verlangtes Ergebnis vergleichbar" not in failed
    engine.queries.create_query("qryTirol", AccessExecutor().resolve_sql(step.action, engine.schema()), replace=True)
    assert _failed(engine, step, StepResult(step_id=1, final_name="qryTirol")) == []


def test_update_checks_exactly_the_affected_records(engine: AccessEngine) -> None:
    """Ein betroffener Datensatz behält den alten Wert, ein FREMDER hat zufällig den neuen – früher bestanden."""
    step = _step({"action": "update_records", "table": "Kunden", "values": {"Ort": "Neuort"}, "where": "KundenNr <= 2"})
    result = AccessExecutor().execute(engine, step)
    assert _failed(engine, step, result) == []
    engine.session.db.Execute("UPDATE Kunden SET Ort='Altort' WHERE KundenNr = 1")
    engine.session.db.Execute("UPDATE Kunden SET Ort='Neuort' WHERE KundenNr = 3")
    assert "Ort gesetzt" in _failed(engine, step, result)


def test_group_total_without_group_footer_is_rejected(engine: AccessEngine) -> None:
    """Gruppensumme verlangt, aber nur eine Gesamtsumme im Berichtsfuß vorhanden."""
    spec = {"name": "rptSumme", "record_source": "Kunden", "fields": ["Bundesland", "Umsatz"],
            "group_by": [{"field": "Bundesland", "footer": False}],
            "totals": [{"field": "Umsatz", "function": "sum", "scope": "both"}]}
    engine.objects.create_report(ReportSpec.model_validate(spec))
    failed = _failed(engine, _step({"action": "create_report", "report": spec}), StepResult(step_id=1))
    assert "Gruppenberechnung sum(Umsatz)" in failed and "Gesamtberechnung sum(Umsatz) im Berichtsfuß" not in failed


def test_swapped_conditional_format_colors_are_rejected(engine: AccessEngine) -> None:
    engine.objects.create_form(FormSpec.model_validate({"name": "frmUmsatz", "record_source": "Kunden",
                                                        "fields": ["Nachname", "Umsatz"]}))
    wanted = {"action": "set_conditional_format", "object_type": "form", "name": "frmUmsatz", "control": "Umsatz",
              "rules": [{"expression": "[Umsatz]<0", "back_color": "#FF0000"},
                        {"expression": "[Umsatz]>=0", "back_color": "#00FF00"}], "replace": True}
    swapped = {**wanted, "rules": [{"expression": "[Umsatz]<0", "back_color": "#00FF00"},
                                   {"expression": "[Umsatz]>=0", "back_color": "#FF0000"}]}
    executor = AccessExecutor()
    executor.execute(engine, _step(swapped))
    assert _failed(engine, _step(wanted), StepResult(step_id=1))
    executor.execute(engine, _step(wanted))
    assert _failed(engine, _step(wanted), StepResult(step_id=1)) == []


def test_table_copy_keeps_lookup_and_table_validation(engine: AccessEngine) -> None:
    """Codex-Befund: die Kopie verlor Nachschlagefeld und Tabellen-Gültigkeitsregel – und bestand trotzdem."""
    engine.tables.create_table("Kurse", [FieldSpec.model_validate({"name": "Start", "type": "datetime"}),
                                         FieldSpec.model_validate({"name": "Ende", "type": "datetime"}),
                                         FieldSpec.model_validate({"name": "Stufe", "type": "short_text", "lookup": {
                                             "values": ["A", "B", "C"], "limit_to_list": True}})])
    table = engine.session.db.TableDefs("Kurse")
    table.ValidationRule, table.ValidationText = "[Ende]>=[Start]", "Ende vor Start"
    step = _step({"action": "copy_table", "source": "Kurse", "new_name": "KurseKopie"})
    result = AccessExecutor().execute(engine, step)
    assert _failed(engine, step, result) == []
    copy = engine.session.db.TableDefs("KurseKopie")  # unabhängig per DAO
    assert str(copy.ValidationRule) == "[Ende]>=[Start]"
    assert '"A"' in str(copy.Fields("Stufe").Properties("RowSource").Value)


def test_named_control_is_never_duplicated(engine: AccessEngine) -> None:
    """Codex-Befund: eine Korrektur legte „txtGesamt_2“ neben dem alten „txtGesamt“ an – und bestand."""
    engine.objects.create_report(ReportSpec.model_validate({"name": "rptK", "record_source": "Kunden",
                                                            "fields": ["Nachname", "Umsatz"]}))
    add = {"controls": [{"type": "text_box", "name": "txtGesamt", "expression": "=Sum([Umsatz])",
                         "section": "report_footer"}]}
    engine.objects.modify_report("rptK", ReportChangeSpec.model_validate(add))
    with pytest.raises(ObjectExistsError):
        engine.objects.modify_report("rptK", ReportChangeSpec.model_validate(add))
    names = [x.name for x in engine.objects.inspect_report("rptK").controls]
    assert names.count("txtGesamt") == 1 and "txtGesamt_2" not in names


def test_greater_vs_less_with_same_value_and_color_is_rejected(engine: AccessEngine) -> None:
    """Codex: „> 100, rot“ verlangt, „< 100, rot“ gespeichert – früher bestand das (nur Wert und Farbe verglichen)."""
    engine.objects.create_form(FormSpec.model_validate({"name": "frmOp", "record_source": "Kunden",
                                                        "fields": ["Nachname", "Umsatz"]}))
    rule = {"operator": "greater", "value": "100", "back_color": "#FF0000"}
    wanted = {"action": "set_conditional_format", "object_type": "form", "name": "frmOp", "control": "Umsatz",
              "rules": [rule], "replace": True}
    AccessExecutor().execute(engine, _step({**wanted, "rules": [{**rule, "operator": "less"}]}))
    assert _failed(engine, _step(wanted), StepResult(step_id=1))
    AccessExecutor().execute(engine, _step(wanted))
    assert _failed(engine, _step(wanted), StepResult(step_id=1)) == []


def test_primary_key_update_and_duplicate_headers_are_not_false_failures(engine: AccessEngine, tmp_path: Path) -> None:
    """Codex: gültige Ergebnisse dürfen nicht scheitern – Schlüsseländerung bzw. doppelte Spaltenüberschriften."""
    engine.tables.create_table("Codes", [FieldSpec.model_validate({"name": "Code", "type": "long"}),
                                         FieldSpec.model_validate({"name": "Text", "type": "short_text"})], ["Code"])
    engine.session.db.Execute("INSERT INTO Codes (Code, [Text]) VALUES (1, 'eins')")
    step = _step({"action": "update_records", "table": "Codes", "values": {"Code": 2}, "where": "Code = 1"})
    assert _failed(engine, step, AccessExecutor().execute(engine, step)) == []
    source = tmp_path / "Namen.csv"
    source.write_text("Name;Name\nAlice;Bob\n", encoding="utf-8")
    db_dir = Path(engine.path).parent
    (db_dir / "Namen.csv").write_bytes(source.read_bytes())
    imp = _step({"action": "import_data", "file": "Namen.csv", "table": "Namen"})
    assert _failed(engine, imp, AccessExecutor().execute(engine, imp)) == []


def test_missing_join_condition_is_rejected(engine: AccessEngine) -> None:
    """Beta-4-Negativtest: verlangt ist Kunden ⋈ Bestellungen über KundenNr, gespeichert wurde ein Kreuzprodukt ohne
    JOIN – gleiche Spalten, falsche Datensätze. Die Ergebnisprüfung muss das ablehnen."""
    step = _step({"action": "create_query", "name": "qryKundenBestellungen", "spec": {
        "tables": ["Kunden", "Bestellungen"],
        "joins": [{"left_table": "Kunden", "right_table": "Bestellungen", "left_field": "KundenNr",
                   "right_field": "KundenNr"}],
        "fields": [{"table": "Kunden", "field": "Nachname"}, {"table": "Bestellungen", "field": "Menge"}]}})
    engine.queries.create_query("qryKundenBestellungen",
                                "SELECT Kunden.Nachname, Bestellungen.Menge FROM Kunden, Bestellungen")
    assert "Ergebnis wie verlangt" in _failed(engine, step, StepResult(step_id=1, final_name="qryKundenBestellungen"))

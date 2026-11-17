"""Beta 4: Antworten nur, wenn sie belegt sind – deterministische Rechnungen, ehrliche Unsicherheit."""
from __future__ import annotations

from fractions import Fraction

import pytest

from app.agent import calc
from app.tasks import models as m


def test_calculation_is_exact_rounds_commercially_and_has_a_second_path() -> None:
    assert calc.evaluate("0.1 + 0.2") == Fraction(3, 10), "exakt, ohne Gleitkommafehler"
    assert calc.format_value(calc.evaluate("2.5"), 0) == "3" and calc.format_value(calc.evaluate("0.125"), 2) == "0,13"
    assert calc.format_value(calc.evaluate("round(10/3, 2)"), None) == "3,33"
    assert calc.evaluate_float("240*15/100") == pytest.approx(36.0)
    assert calc.format_value(calc.evaluate("7/3"), None) == "2,3333"


@pytest.mark.parametrize("bad", ["__import__('os')", "open('x')", "a + 1", "2 ** 1000", "(1).real", "1/0", "[1, 2]",
                                 "((((9**10)**10)**10)**10)**10", "10**10 * 10**10", "round(1/3, 99)", "0 ** -1"])
def test_calculation_rejects_everything_but_arithmetic(bad: str) -> None:
    with pytest.raises(calc.CalcError):
        calc.evaluate(bad)


def test_numbers_must_come_from_the_task() -> None:
    given = "Der Artikel kostet 1.240,50 €; gewährt werden 15 % Rabatt."
    assert calc.numbers_in(given) >= {Fraction("1240.50"), Fraction(15)}
    assert calc.unknown_numbers("1240.50 * 15 / 100", given) == [], "100 ist Prozent-Konstante"
    assert calc.unknown_numbers("1240.50 * 18 / 100", given) == ["18"], "erfundene Zahl"
    assert calc.quoted_in("kostet 1.240,50 €;  gewährt", given) and not calc.quoted_in("kostet 999 €", given)


def _analysis(**kw: object) -> m.TaskAnalysis:
    return m.TaskAnalysis.model_validate({"task_type": "multiple_choice", **kw})


def test_answer_issues_block_unbacked_answers() -> None:
    sure = {"question": "?", "options": [{"label": "A", "text": "x", "correct": True, "reason": "Definition"},
                                        {"label": "B", "text": "y", "correct": False, "reason": "widerspricht"}]}
    assert m.answer_issues(_analysis(multiple_choice=sure)) == []
    unexplained = {**sure, "options": [{**sure["options"][0]}, {"label": "B", "text": "y", "correct": False}]}
    assert "einzeln begründet: B" in m.answer_issues(_analysis(multiple_choice=unexplained))[0]
    none = {**sure, "options": [{**o, "correct": False} for o in sure["options"]]}
    assert m.answer_issues(_analysis(multiple_choice=none)) == ["keine Antwortmöglichkeit als richtig belegt"]
    assert m.answer_issues(_analysis(multiple_choice={**sure, "uncertainty": "Abbildung fehlt"})) == [
        "Unsicherheit: Abbildung fehlt"]
    assert m.answer_issues(_analysis(task_type="theory")) == ["keine Antwort ermittelt"]
    assert m.answer_issues(_analysis(task_type="theory", answer="Ein Index beschleunigt die Suche.",
                                     open_questions=["Welche Tabelle?"])) == ["offene Fragen: Welche Tabelle?"]


def test_practical_task_without_changes_is_not_treated_as_a_question() -> None:
    assert m.answer_issues(m.TaskAnalysis.model_validate({"task_type": "select_query"})) == []


def test_held_deletion_blocks_data_steps_on_that_table_but_not_forms() -> None:
    """Live-Fund 17.11. + Codex: der Formularfilter „Golfclubs“ läuft; jeder spätere DATEN-Schritt an der Tabelle
    (Hinzufügen, bedingte Änderung, SQL-Aktionsabfrage, Kopie) wartet auf die nicht ausgeführte Löschung."""
    from app.agent.orchestrator import _held_by, _tables_in

    def action(spec: dict[str, object]) -> object:
        return m.PlanStep.model_validate({"id": 1, "title": "t", "action": spec}).action

    tables = ["golfclubs", "kunden"]
    held = {t: 5 for t in _tables_in(action({"action": "run_action_query", "sql": "DELETE FROM Golfclubs WHERE ID=3"}),
                                     tables)}
    assert held == {"golfclubs": 5}, "Ziel aus dem SQL einer Aktionsabfrage"
    form_filter = action({"action": "modify_form", "name": "Golfclubs", "changes": {
        "properties": {"Filter": "[Bundesland]=\"Baden-Württemberg\"", "FilterOn": True}}})
    assert _held_by(form_filter, held, tables) is None
    for spec in ({"action": "insert_records", "table": "Golfclubs", "records": [{"Golfplatz": "Neu"}]},
                 {"action": "update_records", "table": "Golfclubs", "values": {"Preis": "[Preis]*1.1"}, "where": "Preis>0"},
                 {"action": "copy_table", "source": "Golfclubs", "new_name": "Archiv"},
                 {"action": "run_action_query", "sql": "INSERT INTO Golfclubs (Golfplatz) VALUES ('X')"}):
        assert _held_by(action(spec), held, tables) == 5, spec
    assert _held_by(action({"action": "insert_records", "table": "Kunden", "records": [{"Nachname": "A"}]}),
                    held, tables) is None, "andere Tabelle läuft"


def test_record_values_are_not_reported_as_missing_objects() -> None:
    from app.access.models import DatabaseSchema, FormInfo, TableInfo
    from app.agent.diff import missing_named_objects

    schema = DatabaseSchema(path="x", name="x", tables=[TableInfo(name="Golfclubs")], forms=[FormInfo(name="Golfclubs")])
    text = ("Ergänzen Sie den Datensatz „Golfclub Bruchsal“, löschen Sie „Golfclub Neckartal“ und filtern Sie das "
            "Formular „Golfclubs“ nach „Baden-Württemberg“. Erstellen Sie den Bericht „rptClubs“.")
    assert missing_named_objects(text, schema) == ["rptClubs"], "nur echte, fehlende Objekte"


def test_codex_findings_calc_rounding_and_numbers() -> None:
    assert calc.format_value(calc.evaluate("-2.5"), 0) == "-3", "kaufmännisch auch negativ"
    big = "100000000000000 + 499999999999999/1000000000000000"
    assert calc.format_value(calc.evaluate(big), 0) == "100000000000000", "keine Zwischenrundung"
    assert calc.format_value(calc.evaluate("round(1.15 - 1, 1)"), None) == "0,2"
    assert calc.evaluate_float("round(1.15 - 1, 1)") == pytest.approx(0.2), "Gegenrechnung rundet exakt"
    assert calc.unknown_numbers("0.125 * 100", "Berechnen Sie 0.125 * 100.") == []
    assert calc.unknown_numbers("1.125 * 2", "Faktor 1.125") == [] and calc.unknown_numbers("1125 * 2", "1.125 Stück") == []


def test_codex_findings_answer_issues() -> None:
    practical = m.TaskAnalysis.model_validate({"task_type": "data_editing", "open_questions": ["Welche Quelldatei?"]})
    assert m.answer_issues(practical) == ["offene Fragen: Welche Quelldatei?"]
    empty_mc = m.TaskAnalysis.model_validate({"task_type": "multiple_choice", "answer": "vermutlich B",
                                             "multiple_choice": {"options": [], "uncertainty": "Abbildung fehlt"}})
    assert "Unsicherheit: Abbildung fehlt" in m.answer_issues(empty_mc)
    assert "keine Antwortmöglichkeiten ausgewertet" in m.answer_issues(empty_mc)


def test_codex_finding_object_name_after_naming_phrase() -> None:
    from app.access.models import DatabaseSchema
    from app.agent.diff import missing_named_objects

    text = "Speichern Sie die Abfrage unter dem Namen „qryKunden“."
    assert missing_named_objects(text, DatabaseSchema(path="x", name="x")) == ["qryKunden"]


def test_codex_finding_no_shared_read_when_vba_modules_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Über die bestehende Verbindung könnte eine SELECT eigene VBA-Funktionen ausführen – mit Modulen nie."""
    from types import SimpleNamespace

    from app.access import queries
    from app.core.errors import AccessError

    def open_fails(self: object, sql: str, *, limit: int = 200, _open_only_errors: bool = False) -> object:
        raise queries._OpenFailed("exklusiv geöffnet")

    session = SimpleNamespace(_app=SimpleNamespace(CurrentProject=SimpleNamespace(AllModules=SimpleNamespace(Count=2))))
    ops = queries.QueryOps(session)  # type: ignore[arg-type]
    monkeypatch.setattr(queries.QueryOps, "run_select_isolated", open_fails)
    shared: list[str] = []
    monkeypatch.setattr(queries.QueryOps, "run_select_sql", lambda self, sql, limit=200: shared.append(sql) or ([], []))
    with pytest.raises(AccessError, match="VBA-Module"):
        ops.run_select_readonly("SELECT LogRead() FROM Kunden")
    assert shared == []
    session._app.CurrentProject.AllModules.Count = 0
    assert ops.run_select_readonly("SELECT 1 FROM Kunden")[2] is False and shared == ["SELECT 1 FROM Kunden"]

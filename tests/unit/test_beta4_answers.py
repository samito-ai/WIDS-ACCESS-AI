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


def test_dependency_key_separates_object_kinds_and_only_real_dependencies_wait() -> None:
    """Live-Fund 17.11.: Formularfilter „Golfclubs“ wartete auf die Löschung in der TABELLE „Golfclubs“."""
    from app.agent.orchestrator import _builds_on_deletion, _object_key

    def action(spec: dict[str, object]) -> object:
        return m.PlanStep.model_validate({"id": 1, "title": "t", "action": spec}).action

    delete = action({"action": "delete_records", "table": "Golfclubs", "where": "ID=1"})
    form_filter = action({"action": "modify_form", "name": "Golfclubs", "changes": {
        "properties": {"Filter": "[Bundesland]=\"Baden-Württemberg\"", "FilterOn": True}}})
    insert = action({"action": "insert_records", "table": "Golfclubs", "records": [{"Golfplatz": "Neu"}]})
    update = action({"action": "update_records", "table": "Golfclubs", "values": {"Ort": "X"}, "where": "ID=2"})
    assert _object_key(delete) == ("table", "golfclubs") != _object_key(form_filter) == ("form", "golfclubs")
    assert _builds_on_deletion(insert) and not _builds_on_deletion(update) and not _builds_on_deletion(form_filter)


def test_record_values_are_not_reported_as_missing_objects() -> None:
    from app.access.models import DatabaseSchema, FormInfo, TableInfo
    from app.agent.diff import missing_named_objects

    schema = DatabaseSchema(path="x", name="x", tables=[TableInfo(name="Golfclubs")], forms=[FormInfo(name="Golfclubs")])
    text = ("Ergänzen Sie den Datensatz „Golfclub Bruchsal“, löschen Sie „Golfclub Neckartal“ und filtern Sie das "
            "Formular „Golfclubs“ nach „Baden-Württemberg“. Erstellen Sie den Bericht „rptClubs“.")
    assert missing_named_objects(text, schema) == ["rptClubs"], "nur echte, fehlende Objekte"

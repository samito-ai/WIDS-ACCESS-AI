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

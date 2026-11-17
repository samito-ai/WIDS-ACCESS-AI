"""Fragen mit Access-Bezug („Welche Datenquelle hat der Bericht …? Tabelle oder Abfrage?“) werden aus den gespeicherten
Eigenschaften beantwortet und in der Prüfung erneut gelesen – nie aus allgemeinem KI-Wissen. Echte Access-Pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.access.specs import FormSpec, ReportSpec
from app.agent.orchestrator import AutoInteraction
from app.tasks.models import CapturedTask
from tests.helpers import build_harness

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def ask(name: str, object_type: str, prop: str, field: str | None = None) -> dict[str, Any]:
    return {"title": f"Frage {name}", "action": {"action": "answer_object_property", "question": f"{prop} von {name}?",
                                                 "object_type": object_type, "name": name, "property": prop,
                                                 "field": field}}


def test_object_questions_are_answered_from_the_stored_state(sample_db_path: Path, tmp_path: Path) -> None:
    steps = [ask("rptTirol", "report", "record_source_type"), ask("frmKunden", "form", "record_source_type"),
             ask("Kunden", "table", "primary_key"), ask("Kunden", "table", "field_type", "Ort"),
             ask("rptTirol", "report", "record_source")]
    plan = {"analysis": {"task_type": "theory", "title": "Fragen", "summary": "s", "confidence": 0.9, "explanation": "e",
                         "requirements": [f"Frage {i + 1}" for i in range(len(steps))]},
            "plan": {"steps": [{**s, "covers": [i + 1]} for i, s in enumerate(steps)], "notes": []}}
    h = build_harness(sample_db_path, [plan], tmp_path)
    try:
        h.access.run(lambda e: e.queries.create_query("qryTirol", "SELECT * FROM Kunden WHERE Bundesland='Tirol'"))
        h.access.run(lambda e: e.objects.create_report(ReportSpec(name="rptTirol", record_source="qryTirol",
                                                                  fields=["Nachname"])))
        h.access.run(lambda e: e.objects.create_form(FormSpec(name="frmKunden", record_source="Kunden",
                                                              fields=["Nachname"])))
        outcome = h.pipeline.execute(h.pipeline.prepare(CapturedTask(text="Fragen zur Datenbank", method="manual")),
                                     AutoInteraction(interactive=False))
        answers = [r.details["answer"] for r in outcome.results]
        assert answers == ["Abfrage „qryTirol“", "Tabelle „Kunden“", "KundenNr", "Kurzer Text", "qryTirol"], answers
        assert all(v.passed and any(c.name == "Antwort erneut ermittelt" for c in v.checks) for v in outcome.verifications)
    finally:
        h.close()


def test_unknown_object_is_rejected_in_the_plan(sample_db_path: Path, tmp_path: Path) -> None:
    from app.agent.planner import PlanValidator
    from app.tasks import models as m

    h = build_harness(sample_db_path, [], tmp_path)
    try:
        step = m.PlanStep.model_validate({"id": 1, "title": "t", "action": {
            "action": "answer_object_property", "question": "q", "object_type": "report", "name": "Gibtsnicht",
            "property": "record_source"}})
        report = PlanValidator().validate(m.ExecutionPlan(steps=[step]), h.access.schema(refresh=True))
        assert "gibt es nicht" in report.error_text()
    finally:
        h.close()

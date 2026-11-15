"""Eine Schrittprüfung ohne einen einzigen kritischen Prüfpunkt darf nie als bestanden gelten."""
from __future__ import annotations

from typing import Any

import pytest

from app.agent.executor import StepResult
from app.agent.verifier import ResultVerifier
from app.tasks import models as m


def test_verification_without_any_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing_checked(*args: Any) -> None:
        return None

    verifier = ResultVerifier()
    monkeypatch.setattr(verifier, "_verify_create_query", nothing_checked)
    step = m.PlanStep(id=1, title="Abfrage", action=m.CreateQuery(name="q", sql="SELECT 1"))
    verification = verifier.verify(None, step, StepResult(step_id=1, message=""))  # type: ignore[arg-type]
    assert not verification.passed
    assert [c.name for c in verification.failed_checks] == ["Ergebnis nachgeprüft"]

"""Live-Fund 17.11. (11:06): te.comp/Access hält die Übungsdatei EXKLUSIV geöffnet. Eine zweite DAO-Verbindung ist dann
grundsätzlich unmöglich – Datenfragen und Nachprüfungen müssen über die bestehende Verbindung lesen, ehrlich
gekennzeichnet, ohne sinnlose Wiederholungen. Eigene Testdatenbank, eigene Access-Instanz."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access.engine import AccessEngine
from app.agent.executor import AccessExecutor
from app.agent.verifier import ResultVerifier
from app.core.errors import WidsError
from app.tasks import models as m

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_reads_and_writes_work_while_access_holds_the_file_exclusively(sample_db_path: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("Access.Application")  # steht für das Access, das te.comp gestartet hat
    try:
        app.Visible = False
        app.OpenCurrentDatabase(str(sample_db_path), True)  # exklusiv – wie im Live-Fund
        engine = AccessEngine.open(sample_db_path, prefer_running=True)
        try:
            assert engine.mode == "attached"
            with pytest.raises(WidsError):  # Fehler reproduziert: eine zweite Verbindung geht nicht
                engine.queries.run_select_isolated("SELECT COUNT(*) FROM Kunden")
            question = m.PlanStep.model_validate({"id": 1, "title": "Frage", "action": {
                "action": "answer_query", "question": "Wie viele Kunden?", "sql": "SELECT COUNT(*) FROM Kunden"}})
            result = AccessExecutor().execute(engine, question)
            assert result.status == "ok" and result.details["isolated"] is False
            verification = ResultVerifier().verify(engine, question, result)
            assert verification.passed, [c.name for c in verification.failed_checks]
            assert any(c.name == "Nachprüfung über eigene Verbindung" and not c.passed and not c.critical
                       for c in verification.checks), "Einschränkung der Unabhängigkeit wird ehrlich ausgewiesen"
            # mehrere Lese- und Schreibschritte hintereinander über dieselbe Verbindung
            insert = m.PlanStep.model_validate({"id": 2, "title": "Neu", "action": {
                "action": "insert_records", "table": "Kunden", "records": [{"Nachname": "Sperrtest"}]}})
            assert AccessExecutor().execute(engine, insert).status == "ok"
            again = AccessExecutor().execute(engine, question)
            assert int(again.details["answer"]) == int(result.details["answer"]) + 1
            with pytest.raises(WidsError):  # nur lesende Anweisungen, auch über die bestehende Verbindung
                engine.queries.run_select_readonly("DELETE FROM Kunden")
        finally:
            engine.close()
    finally:
        app.CloseCurrentDatabase()
        app.Quit()

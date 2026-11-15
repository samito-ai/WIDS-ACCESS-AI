"""Integrationstest: Anhängen an eine bereits laufende Access-Instanz (der te.comp-Weg)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access.engine import AccessEngine
from app.access.session import find_running_database
from app.core.errors import ApprovalRequiredError

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_attach_to_running_access_instance(sample_db_path: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("Access.Application")  # steht für das von te.comp gestartete Access
    try:
        app.Visible = False
        app.OpenCurrentDatabase(str(sample_db_path), False)
        assert find_running_database(str(sample_db_path)) is not None

        engine = AccessEngine.open(sample_db_path, prefer_running=True)
        assert engine.mode == "attached"
        assert engine.schema().attached_to_running_instance
        engine.queries.create_query("qryAngehaengt", "SELECT Nachname FROM Kunden")
        engine.tables.insert_records("Kunden", [{"Nachname": "Angehängt"}])
        with pytest.raises(ApprovalRequiredError):
            engine.tables.delete_records("Kunden", None, max_affected=3)
        assert engine.tables.count_records("Kunden") == 13, "Transaktion über die Access-Instanz zurückgerollt"
        # Access selbst sieht die Änderung sofort …
        assert app.CurrentDb().QueryDefs("qryAngehaengt").Name == "qryAngehaengt"
        engine.close()
        # … und läuft nach dem Trennen weiter (fremde Instanzen werden nie beendet)
        assert app.CurrentProject.FullName
    finally:
        app.CloseCurrentDatabase()
        app.Quit(2)

"""Excel-Import mit Datentypen und Feldbeschreibungen über die ganze Pipeline – universell, nicht nur „CD-Sammlung“:
Quelldatei im Übungsordner finden (.xls und .xlsx), Tabelle anlegen, Daten importieren und unabhängig prüfen
(Feldnamen, Datentypen, Beschreibungen, alle Zeilen)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from app.access import importer
from app.agent.orchestrator import AutoInteraction
from app.tasks.models import CapturedTask
from app.utils.sampledb import create_sample_database
from tests.helpers import build_harness, solution
from tests.integration.test_import import write_xlsx

pytestmark = pytest.mark.access

WORKSHOP = Path(r"C:\Program Files (x86)\te.comp\te.comp-training\access\workshops\CD-Sammlung")


def _import_with_types_and_descriptions(db: Path, source: Path, table: str, tmp_path: Path) -> dict[str, Any]:
    """Wie ein KI-Plan: EIN import_data-Schritt mit allen Spalten (Name = Überschrift, passender Typ, Beschreibung)."""
    rows = importer.read_rows(source)
    specs = importer.infer_fields(rows[0], rows[1:])
    fields = [{"name": s.name, "type": s.type.value, "description": f"Spalte „{s.name}“ aus {source.name}"} for s in specs]
    step = {"title": "Tabelle aus Excel", "action": {"action": "import_data", "file": source.name, "table": table,
                                                     "fields": fields}}
    h = build_harness(db, [solution([step], task_type="import")], tmp_path)
    try:
        prepared = h.pipeline.prepare(CapturedTask(
            text=f"Erstelle die Tabelle {table} aus der Excel-Datei {source.name}, übernimm die Feldnamen, lege passende "
                 "Datentypen und Feldbeschreibungen fest und importiere die Daten.", method="manual"))
        outcome = h.pipeline.execute(prepared, AutoInteraction())  # Plan im Dashboard freigegeben
        schema = h.access.run(lambda e: e.schema())
        return {"outcome": outcome, "table": schema.table(table), "fields": fields, "rows": len(rows) - 1}
    finally:
        h.close()


def _assert_complete(result: dict[str, Any]) -> None:
    outcome = result["outcome"]
    assert outcome.status == "erfolgreich", outcome.error or outcome.report_md
    checks = {c.name: c for v in outcome.verifications for c in v.checks}
    assert checks["Alle Zeilen der Quelldatei importiert"].passed, checks["Alle Zeilen der Quelldatei importiert"].detail
    for field in result["fields"]:
        assert checks[f"Feld {field['name']} vorhanden"].passed
        assert checks[f"Datentyp {field['name']}"].passed
        assert checks[f"Beschreibung {field['name']}"].passed
        info = result["table"].field(field["name"])
        assert info is not None and info.description == field["description"] and info.type.value == field["type"]


def test_xlsx_import_with_types_and_descriptions_any_file_name(tmp_path: Path) -> None:
    """Beliebiger Datenbankname, .xlsx im Ordner der Übungsdatei."""
    db = create_sample_database(tmp_path / "Datenbank1.accdb")
    source = write_xlsx(tmp_path / "Begriffe.xlsx")
    result = _import_with_types_and_descriptions(db, source, "tblBegriffe", tmp_path)
    _assert_complete(result)
    assert result["rows"] == 2


@pytest.mark.tecomp
@pytest.mark.skipif(not (WORKSHOP / "CDs.xls").exists(), reason="te.comp-Workshop „CD-Sammlung“ nicht installiert")
def test_cd_collection_workshop_copy_xls(tmp_path: Path) -> None:
    """Übungskopie des Workshops „CD-Sammlung“ (Vorlage + CDs.xls in einen eigenen Ordner kopiert, wie auf W:):
    Tabelle „CDs“ aus dem alten .xls-Format mit Typen und Beschreibungen."""
    db = Path(shutil.copy(WORKSHOP / "CD-Sammlung.accdb", tmp_path / "CD-Sammlung_4711.accdb"))
    source = Path(shutil.copy(WORKSHOP / "CDs.xls", tmp_path / "CDs.xls"))
    result = _import_with_types_and_descriptions(db, source, "CDs", tmp_path)
    _assert_complete(result)
    assert result["rows"] > 0 and any(f["name"] == "Interpret" for f in result["fields"])

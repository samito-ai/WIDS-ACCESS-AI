"""Objekte zwischen Access-Datenbanken übernehmen (te.comp 1001/U1, U3, U4) – in echtem Access."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.access import transfer
from app.access.engine import AccessEngine
from app.access.specs import ReportSpec
from app.access.transfer import TransferItem
from app.core.errors import AccessError, ObjectExistsError, ObjectNotFoundError

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_backup_database_import_and_link(engine: AccessEngine, sample_db_path: Path) -> None:
    folder = sample_db_path.parent
    engine.queries.create_query("qryTirol", "SELECT * FROM Kunden WHERE Bundesland='Tirol'")
    engine.objects.create_report(ReportSpec(name="rptKunden", record_source="Kunden", fields=["Nachname"]))

    # 1001/U1: neue Sicherungsdatenbank im Übungsordner, alle Tabellen + eine Abfrage + ein Bericht
    items = [TransferItem(type="table", name=t.name) for t in engine.schema().tables]
    items += [TransferItem(type="query", name="qryTirol"), TransferItem(type="report", name="rptKunden")]
    path = engine.transfer.transfer("export", "Sicherung Filmdaten", items, create_new=True)
    assert path == folder / "Sicherung Filmdaten.accdb" and path.is_file()
    assert all(transfer.transferred(path, items).values())
    with pytest.raises(ObjectExistsError):  # nie eine vorhandene Datenbank als „neu“ überschreiben
        engine.transfer.transfer("export", "Sicherung Filmdaten", items[:1], create_new=True)
    with pytest.raises(ObjectExistsError):  # Export in vorhandene Datenbank überschreibt keine Objekte
        engine.transfer.transfer("export", "Sicherung Filmdaten", items[:1])

    # 1001/U3: aus einer anderen Datenbank des Ordners in die geöffnete importieren
    other = folder / "Filmdaten.accdb"
    shutil.copy(path, other)
    engine.transfer.transfer("import", "Filmdaten", [TransferItem(type="table", name="Kunden", target_name="KundenKopie"),
                                                     TransferItem(type="report", name="rptKunden", target_name="rptKopie")])
    schema = engine.schema()
    assert schema.table("KundenKopie") is not None and not schema.table("KundenKopie").linked  # type: ignore[union-attr]
    assert "rptKopie" in engine.objects.report_names()

    # 1001/U4: Tabellen verknüpfen
    engine.transfer.transfer("link", "Filmdaten.accdb", [TransferItem(type="table", name="Artikel", target_name="ArtikelLink")])
    linked = engine.schema().table("ArtikelLink")
    assert linked is not None and linked.linked
    with pytest.raises(ObjectExistsError):
        engine.transfer.transfer("import", "Filmdaten", [TransferItem(type="table", name="Kunden")])
    with pytest.raises(ObjectNotFoundError):
        engine.transfer.transfer("import", "Filmdaten", [TransferItem(type="table", name="GibtEsNicht")])
    with pytest.raises(AccessError):
        engine.transfer.transfer("link", "Filmdaten", [TransferItem(type="query", name="qryTirol")])
    with pytest.raises(AccessError):  # nur im Übungsordner
        engine.transfer.transfer("import", str(Path.home() / "fremd.accdb"), [TransferItem(name="Kunden")])

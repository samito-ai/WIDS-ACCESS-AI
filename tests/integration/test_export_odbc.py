"""Export (Excel/CSV/Text) und ODBC-Import/-Verknüpfung – in echtem Access.

Für ODBC dient der Access-ODBC-Treiber mit einer zweiten Datenbank als Quelle: derselbe Codeweg wie beim
SQL Server (ODBC-Verbindungszeichenfolge, SELECT … INTO bzw. verknüpfte Tabelle), nur ohne externen Server.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.access import importer
from app.access.engine import AccessEngine
from app.core.errors import AccessError, ObjectExistsError
from app.core.logging_setup import redact

pytestmark = pytest.mark.access


def test_export_csv_text_and_excel(engine: AccessEngine, tmp_path: Path) -> None:
    tables = engine.tables
    csv_file = tmp_path / "Kunden.csv"
    assert tables.export_data("Kunden", csv_file) == tables.count_records("Kunden")
    rows = importer.read_rows(csv_file)
    assert rows[0][:3] == ["KundenNr", "Vorname", "Nachname"] and len(rows) == tables.count_records("Kunden") + 1
    with pytest.raises(ObjectExistsError):
        tables.export_data("Kunden", csv_file)
    tab = tmp_path / "Artikel.txt"
    tables.export_data("Artikel", tab, delimiter="\t", header=False)
    assert "\t" in tab.read_text(encoding="cp1252").splitlines()[0]
    assert len(importer.read_rows(tab, has_header=False)) == tables.count_records("Artikel")


def test_overwritten_export_file_is_backed_up_first(engine: AccessEngine, tmp_path: Path) -> None:
    """„Überschreiben“ einer vorhandenen Datei: der bisherige Inhalt landet vorher in den Sicherungen."""
    from app.core.paths import backups_dir

    target = tmp_path / "Kunden.csv"
    target.write_text("eigene Notizen des Benutzers", encoding="cp1252")
    engine.tables.export_data("Kunden", target, replace=True)
    saved = list((backups_dir() / "export-ziele").glob("Kunden_*.csv"))
    assert len(saved) == 1 and saved[0].read_text(encoding="cp1252") == "eigene Notizen des Benutzers"
    assert importer.read_rows(target)[0][0] == "KundenNr"


@pytest.mark.access_app
def test_export_excel(engine: AccessEngine, tmp_path: Path) -> None:
    xlsx = tmp_path / "Artikel.xlsx"
    count = engine.tables.export_data("Artikel", xlsx)
    rows = importer.read_rows(xlsx)
    assert len(rows) == count + 1 and "Bezeichnung" in rows[0]
    assert engine.tables.export_data("Artikel", xlsx, replace=True) == count


def test_odbc_sql_server_unreachable_fails_cleanly(engine: AccessEngine) -> None:
    """Echter SQL-Server-ODBC-Treiber, Server nicht erreichbar: verständlicher Fehler, nichts angelegt, kein Kennwort."""
    connect = "Driver={SQL Server};Server=127.0.0.1,1;Database=training;UID=uebung;PWD=geheim123"
    for link in (False, True):
        with pytest.raises(AccessError) as info:
            engine.tables.import_odbc(connect, "dbo.Aufgaben", "Aufgaben", link=link)
        assert "geheim123" not in redact(str(info.value) + (info.value.detail or ""))
        assert not engine.tables.table_exists("Aufgaben")
    assert "geheim123" not in redact(f"ODBC;{connect}"), "Kennwort erscheint nie im Log"
    with pytest.raises(ObjectExistsError):
        engine.tables.import_odbc(connect, "dbo.Aufgaben", "Kunden")  # Zielname belegt → vorher abgelehnt

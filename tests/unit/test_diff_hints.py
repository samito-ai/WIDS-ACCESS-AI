"""Hinweis „genannt, aber nicht vorhanden“: keine Fehlalarme für Fließtext und Quelldateien."""
from __future__ import annotations

from app.access.models import DatabaseSchema, QueryInfo
from app.agent.diff import missing_named_objects


def test_classifier_knows_new_task_kinds() -> None:
    from app.tasks.classifier import classify_task
    from app.tasks.models import TaskType

    assert classify_task("Erstellen Sie ein Navigationsformular mit Registerkarten.").task_type == TaskType.FORM
    assert classify_task("Binden Sie das Formular als Unterformular ein.").task_type == TaskType.FORM
    assert classify_task("Importieren Sie die Textdatei Filmcrew1 in eine neue Tabelle.").task_type == TaskType.DATA_EDITING
    assert classify_task("Exportieren Sie die Kunden in eine Excel-Datei.").task_type == TaskType.DATA_EDITING


def test_missing_named_objects_ignores_prose_and_files() -> None:
    schema = DatabaseSchema(path="", name="x", queries=[QueryInfo(name="Spielerstatus")])
    text = ('Verwenden Sie ein „bestehendes Feld“ als Primärschlüssel. Importieren Sie „Kunden.xlsx“. '
            'Speichern Sie die Abfragen unter "Spielerstatus" und „Senioren“ sowie „Leonardo DiCaprio“.')
    assert missing_named_objects(text, schema) == ["Leonardo DiCaprio", "Senioren"]


def test_missing_named_objects_knows_calculated_query_fields() -> None:
    """Live-Test 0604/U2: „Preis“ und „Netto“ sind berechnete Felder der Abfrage – kein fehlendes Objekt."""
    schema = DatabaseSchema(path="", name="x", queries=[QueryInfo(name="Abreiseliste", fields=["Zimmer", "Preis", "Netto"])])
    text = 'Ergänzen Sie in der Abfrage „Abreiseliste“ die Felder „Preis“ und „Netto“ sowie „Gesamt“.'
    assert missing_named_objects(text, schema) == ["Gesamt"]

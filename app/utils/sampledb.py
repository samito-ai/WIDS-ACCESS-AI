"""Erzeugt eine reproduzierbare Übungsdatenbank (Kunden, Artikel, Bestellungen).

Wird von den automatisierten Tests, von ``scripts/create_sample_db.py`` und von der
Schnellaktion „Beispieldatenbank erstellen“ im Dashboard verwendet.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.access.constants import AccessType
from app.access.engine import AccessEngine
from app.access.specs import FieldSpec, RelationFieldPair

KUNDEN: list[dict[str, object]] = [
    {"Vorname": "Anna", "Nachname": "Gruber", "Strasse": "Dorfstraße 4", "PLZ": "6460", "Ort": "Imst", "Bundesland": "Tirol", "Geburtsdatum": "12.03.1988", "Newsletter": True, "Umsatz": 1250.50},
    {"Vorname": "Lukas", "Nachname": "Hofer", "Strasse": "Bahnhofweg 12", "PLZ": "6020", "Ort": "Innsbruck", "Bundesland": "Tirol", "Geburtsdatum": "25.07.1992", "Newsletter": False, "Umsatz": 430.00},
    {"Vorname": "Maria", "Nachname": "Bauer", "Strasse": "Hauptplatz 1", "PLZ": "5020", "Ort": "Salzburg", "Bundesland": "Salzburg", "Geburtsdatum": "02.11.1979", "Newsletter": True, "Umsatz": 2890.75},
    {"Vorname": "Thomas", "Nachname": "Egger", "Strasse": "Seeweg 7", "PLZ": "6600", "Ort": "Reutte", "Bundesland": "Tirol", "Geburtsdatum": "18.01.1985", "Newsletter": True, "Umsatz": 760.20},
    {"Vorname": "Sophie", "Nachname": "Wagner", "Strasse": "Ringstraße 33", "PLZ": "1010", "Ort": "Wien", "Bundesland": "Wien", "Geburtsdatum": "30.09.1995", "Newsletter": False, "Umsatz": 3120.00},
    {"Vorname": "Michael", "Nachname": "Pichler", "Strasse": "Am Anger 9", "PLZ": "6410", "Ort": "Telfs", "Bundesland": "Tirol", "Geburtsdatum": "09.05.1971", "Newsletter": False, "Umsatz": 95.90},
    {"Vorname": "Julia", "Nachname": "Steiner", "Strasse": "Lindenallee 21", "PLZ": "8010", "Ort": "Graz", "Bundesland": "Steiermark", "Geburtsdatum": "14.12.1990", "Newsletter": True, "Umsatz": 1540.30},
    {"Vorname": "Daniel", "Nachname": "Mayr", "Strasse": "Kirchgasse 2", "PLZ": "6500", "Ort": "Landeck", "Bundesland": "Tirol", "Geburtsdatum": "21.06.1983", "Newsletter": True, "Umsatz": 2010.00},
    {"Vorname": "Elena", "Nachname": "Aigner", "Strasse": "Marktstraße 15", "PLZ": "4020", "Ort": "Linz", "Bundesland": "Oberösterreich", "Geburtsdatum": "05.04.1998", "Newsletter": False, "Umsatz": 310.45},
    {"Vorname": "Paul", "Nachname": "Berger", "Strasse": "Schulweg 8", "PLZ": "6850", "Ort": "Dornbirn", "Bundesland": "Vorarlberg", "Geburtsdatum": "27.08.1976", "Newsletter": True, "Umsatz": 880.00},
    {"Vorname": "Laura", "Nachname": "Auer", "Strasse": "Sonnenhang 3", "PLZ": "6460", "Ort": "Imst", "Bundesland": "Tirol", "Geburtsdatum": "16.02.2001", "Newsletter": False, "Umsatz": 150.00},
    {"Vorname": "Felix", "Nachname": "Leitner", "Strasse": "Gartenweg 5", "PLZ": "9020", "Ort": "Klagenfurt", "Bundesland": "Kärnten", "Geburtsdatum": "11.10.1987", "Newsletter": True, "Umsatz": 1975.60},
]

ARTIKEL: list[dict[str, object]] = [
    {"Bezeichnung": "Notebook 15 Zoll", "Kategorie": "Hardware", "Einzelpreis": 899.00, "Lagerbestand": 14},
    {"Bezeichnung": "Monitor 27 Zoll", "Kategorie": "Hardware", "Einzelpreis": 249.90, "Lagerbestand": 30},
    {"Bezeichnung": "Tastatur mechanisch", "Kategorie": "Zubehör", "Einzelpreis": 79.50, "Lagerbestand": 55},
    {"Bezeichnung": "Maus kabellos", "Kategorie": "Zubehör", "Einzelpreis": 29.90, "Lagerbestand": 120},
    {"Bezeichnung": "Office-Paket", "Kategorie": "Software", "Einzelpreis": 149.00, "Lagerbestand": 200},
    {"Bezeichnung": "Virenschutz 1 Jahr", "Kategorie": "Software", "Einzelpreis": 39.90, "Lagerbestand": 500},
    {"Bezeichnung": "USB-Stick 64 GB", "Kategorie": "Zubehör", "Einzelpreis": 12.90, "Lagerbestand": 0},
    {"Bezeichnung": "Drucker Laser", "Kategorie": "Hardware", "Einzelpreis": 199.00, "Lagerbestand": 8},
]

#: (KundenNr, ArtikelNr, Menge, Bestelldatum)
BESTELLUNGEN: list[tuple[int, int, int, str]] = [
    (1, 1, 1, "05.01.2025"), (1, 4, 2, "05.01.2025"), (2, 3, 1, "17.01.2025"), (3, 2, 2, "02.02.2025"),
    (3, 5, 1, "02.02.2025"), (4, 6, 3, "14.02.2025"), (5, 1, 2, "01.03.2025"), (5, 8, 1, "01.03.2025"),
    (6, 7, 5, "09.03.2025"), (7, 2, 1, "22.03.2025"), (8, 1, 1, "03.04.2025"), (8, 3, 2, "03.04.2025"),
    (9, 4, 1, "18.04.2025"), (10, 5, 4, "30.04.2025"), (11, 7, 2, "06.05.2025"), (12, 8, 1, "19.05.2025"),
    (1, 6, 1, "02.06.2025"), (4, 2, 1, "15.06.2025"), (8, 5, 2, "28.06.2025"), (12, 1, 1, "10.07.2025"),
]


def populate(engine: AccessEngine, *, with_relations: bool = False) -> None:
    """Legt die Beispieltabellen samt Daten in einer geöffneten (leeren) Datenbank an."""
    engine.tables.create_table(
        "Kunden",
        [
            FieldSpec(name="KundenNr", type=AccessType.AUTONUMBER, primary_key=True),
            FieldSpec(name="Vorname", type=AccessType.SHORT_TEXT, size=50),
            FieldSpec(name="Nachname", type=AccessType.SHORT_TEXT, size=50, required=True),
            FieldSpec(name="Strasse", type=AccessType.SHORT_TEXT, size=80),
            FieldSpec(name="PLZ", type=AccessType.SHORT_TEXT, size=10),
            FieldSpec(name="Ort", type=AccessType.SHORT_TEXT, size=50),
            FieldSpec(name="Bundesland", type=AccessType.SHORT_TEXT, size=30),
            FieldSpec(name="Geburtsdatum", type=AccessType.DATETIME),
            FieldSpec(name="Newsletter", type=AccessType.YES_NO),
            FieldSpec(name="Umsatz", type=AccessType.CURRENCY),
        ],
    )
    engine.tables.create_table(
        "Artikel",
        [
            FieldSpec(name="ArtikelNr", type=AccessType.AUTONUMBER, primary_key=True),
            FieldSpec(name="Bezeichnung", type=AccessType.SHORT_TEXT, size=80, required=True),
            FieldSpec(name="Kategorie", type=AccessType.SHORT_TEXT, size=30),
            FieldSpec(name="Einzelpreis", type=AccessType.CURRENCY),
            FieldSpec(name="Lagerbestand", type=AccessType.INTEGER),
        ],
    )
    engine.tables.create_table(
        "Bestellungen",
        [
            FieldSpec(name="BestellNr", type=AccessType.AUTONUMBER, primary_key=True),
            FieldSpec(name="KundenNr", type=AccessType.LONG),
            FieldSpec(name="ArtikelNr", type=AccessType.LONG),
            FieldSpec(name="Menge", type=AccessType.INTEGER, default_value="1"),
            FieldSpec(name="Bestelldatum", type=AccessType.DATETIME),
        ],
    )
    engine.tables.insert_records("Kunden", KUNDEN)
    engine.tables.insert_records("Artikel", ARTIKEL)
    engine.tables.insert_records(
        "Bestellungen",
        [
            {"KundenNr": kunde, "ArtikelNr": artikel, "Menge": menge, "Bestelldatum": datum}
            for kunde, artikel, menge, datum in BESTELLUNGEN
        ],
    )
    if with_relations:
        engine.relations.create_relationship(
            "Kunden", "Bestellungen", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")]
        )
        engine.relations.create_relationship(
            "Artikel", "Bestellungen", [RelationFieldPair(primary="ArtikelNr", foreign="ArtikelNr")]
        )


def create_sample_database(
    path: str | os.PathLike[str], *, with_relations: bool = False, overwrite: bool = False
) -> Path:
    """Erstellt die Beispieldatenbank als Datei und gibt deren Pfad zurück."""
    target = Path(path)
    engine = AccessEngine.create(target, overwrite=overwrite)
    try:
        populate(engine, with_relations=with_relations)
    finally:
        engine.close()
    return target

"""Integrationstests: Abfragen und Beziehungen gegen echtes DAO."""
from __future__ import annotations

import pytest

from app.access import constants as c
from app.access.engine import AccessEngine
from app.access.specs import (
    CrosstabSpec,
    FieldSpec,
    QueryFieldSpec,
    QueryParameterSpec,
    QuerySpec,
    RelationFieldPair,
)
from app.access.sqlbuilder import build_sql
from app.core.errors import (
    AccessError,
    ApprovalRequiredError,
    DataIntegrityError,
    ObjectExistsError,
    ObjectNotFoundError,
    SqlSyntaxError,
)

pytestmark = pytest.mark.access


# --------------------------------------------------------------------------- Abfragen
def test_saved_select_query_tirol_sorted(engine: AccessEngine) -> None:
    """Test B: gespeicherte Abfrage – Kunden aus Tirol, nach Nachname sortiert."""
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[
            QueryFieldSpec(field="Nachname", sort="ASC"),
            QueryFieldSpec(field="Vorname"),
            QueryFieldSpec(field="Ort"),
            QueryFieldSpec(field="Bundesland", criteria="Tirol", show=False),
        ],
    )
    info = engine.queries.create_query("qryKundenTirol", build_sql(spec, engine.schema()))
    assert info.type_name == "Auswahlabfrage"
    assert info.fields == ["Nachname", "Vorname", "Ort"]
    columns, rows = engine.queries.run_select("qryKundenTirol")
    assert columns == ["Nachname", "Vorname", "Ort"]
    assert [r[0] for r in rows] == ["Auer", "Egger", "Gruber", "Hofer", "Mayr", "Pichler"]
    stored = engine.schema().query("qryKundenTirol")
    assert stored is not None and "Tirol" in stored.sql and "ORDER BY" in stored.sql


def test_query_conflict_and_replace(engine: AccessEngine) -> None:
    """Test F (Engine-Teil): vorhandene Abfrage wird nicht stillschweigend überschrieben."""
    engine.queries.create_query("qryA", "SELECT Nachname FROM Kunden")
    with pytest.raises(ObjectExistsError):
        engine.queries.create_query("qryA", "SELECT Ort FROM Kunden")
    assert "Nachname" in engine.queries.get_sql("qryA")
    with pytest.raises(ObjectExistsError):
        engine.queries.create_query("Kunden", "SELECT Ort FROM Kunden")
    engine.queries.create_query("qryA", "SELECT Ort FROM Kunden", replace=True)
    assert "Ort" in engine.queries.get_sql("qryA")
    engine.queries.rename_query("qryA", "qryB")
    assert engine.queries.exists("qryB") and not engine.queries.exists("qryA")
    with pytest.raises(ObjectExistsError):
        engine.queries.rename_query("qryB", "Artikel")
    engine.queries.delete_query("qryB")
    with pytest.raises(ObjectNotFoundError):
        engine.queries.delete_query("qryB")


def test_calculated_field_query(engine: AccessEngine) -> None:
    """Test C: Abfrage mit berechnetem Feld über zwei Tabellen."""
    spec = QuerySpec(
        tables=["Bestellungen", "Artikel"],
        joins=[],
        fields=[
            QueryFieldSpec(field="BestellNr", sort="ASC"),
            QueryFieldSpec(field="Bezeichnung"),
            QueryFieldSpec(expression="[Menge]*[Einzelpreis]", alias="Gesamtpreis"),
        ],
    )
    # ohne gespeicherte Beziehung müssen die Verknüpfungen explicit sein -> erst Beziehung anlegen
    engine.relations.create_relationship(
        "Artikel", "Bestellungen", [RelationFieldPair(primary="ArtikelNr", foreign="ArtikelNr")]
    )
    sql = build_sql(spec, engine.schema())
    assert "INNER JOIN" in sql
    engine.queries.create_query("qryGesamtpreis", sql)
    columns, rows = engine.queries.run_select("qryGesamtpreis")
    assert columns == ["BestellNr", "Bezeichnung", "Gesamtpreis"]
    assert len(rows) == 20
    assert rows[0] == [1, "Notebook 15 Zoll", 899.0]
    assert rows[1] == [2, "Maus kabellos", 59.8]


def test_parameter_query(engine: AccessEngine) -> None:
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[QueryFieldSpec(field="Nachname", sort="ASC"), QueryFieldSpec(field="Ort", criteria="[Welcher Ort?]")],
        parameters=[QueryParameterSpec(name="Welcher Ort?", type="text")],
    )
    info = engine.queries.create_query("qryOrtParameter", build_sql(spec))
    assert [p.name for p in info.parameters] == ["Welcher Ort?"]
    _, rows = engine.queries.run_select("qryOrtParameter", parameters={"Welcher Ort?": "Imst"})
    assert [r[0] for r in rows] == ["Auer", "Gruber"]
    with pytest.raises(SqlSyntaxError):
        engine.queries.run_select("qryOrtParameter")


def test_aggregate_group_by_having(engine: AccessEngine) -> None:
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[
            QueryFieldSpec(field="Bundesland", aggregate="group_by", sort="ASC"),
            QueryFieldSpec(field="KundenNr", aggregate="count", alias="Anzahl"),
            QueryFieldSpec(field="Umsatz", aggregate="sum"),
            QueryFieldSpec(field="Umsatz", aggregate="avg", alias="Schnitt"),
            QueryFieldSpec(field="Umsatz", aggregate="max", alias="Hoechster"),
            QueryFieldSpec(field="Umsatz", aggregate="min", alias="Niedrigster", criteria=">=95"),
        ],
    )
    engine.queries.create_query("qryUmsatzJeLand", build_sql(spec))
    columns, rows = engine.queries.run_select("qryUmsatzJeLand")
    assert columns == ["Bundesland", "Anzahl", "SummevonUmsatz", "Schnitt", "Hoechster", "Niedrigster"]
    tirol = next(r for r in rows if r[0] == "Tirol")
    assert tirol[1] == 6
    assert tirol[2] == pytest.approx(4696.6)
    assert tirol[4] == pytest.approx(2010.0)
    assert tirol[5] == pytest.approx(95.9)


def test_like_and_or_between_criteria_execute(engine: AccessEngine) -> None:
    cases = {
        "Wie A*": ["Aigner", "Auer"],
        '"*er"': ["Aigner", "Auer", "Bauer", "Berger", "Egger", "Gruber", "Hofer", "Leitner", "Pichler", "Steiner",
                  "Wagner"],
        "Gruber Oder Mayr": ["Gruber", "Mayr"],
        'Nicht Wie "*er"': ["Mayr"],
        "Zwischen A Und C": ["Aigner", "Auer", "Bauer", "Berger"],
    }
    for criteria, expected in cases.items():
        spec = QuerySpec(tables=["Kunden"], fields=[QueryFieldSpec(field="Nachname", criteria=criteria, sort="ASC")])
        _, rows = engine.queries.run_select_sql(build_sql(spec))
        assert [r[0] for r in rows] == expected, criteria
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[QueryFieldSpec(field="Nachname", sort="ASC"),
                QueryFieldSpec(field="Geburtsdatum", criteria="Zwischen 01.01.1990 Und 31.12.1999", show=False),
                QueryFieldSpec(field="Newsletter", criteria="Nein", show=False)],
    )
    _, rows = engine.queries.run_select_sql(build_sql(spec))
    assert [r[0] for r in rows] == ["Aigner", "Hofer", "Wagner"]


def test_left_join_and_crosstab(engine: AccessEngine) -> None:
    engine.tables.insert_records("Kunden", [{"Vorname": "Ohne", "Nachname": "Bestellung", "Bundesland": "Tirol"}])
    sql = (
        "SELECT Kunden.Nachname FROM Kunden LEFT JOIN Bestellungen ON Kunden.KundenNr = Bestellungen.KundenNr "
        "WHERE Bestellungen.BestellNr Is Null"
    )
    _, rows = engine.queries.run_select_sql(sql)
    assert rows == [["Bestellung"]]
    cross = QuerySpec(
        kind="crosstab",
        tables=["Kunden"],
        crosstab=CrosstabSpec(row_headings=["Bundesland"], column_heading="Newsletter", value_field="Umsatz"),
    )
    info = engine.queries.create_query("qryKreuz", build_sql(cross))
    assert info.type_code == c.DB_Q_CROSSTAB
    columns, rows = engine.queries.run_select("qryKreuz")
    assert columns[0] == "Bundesland" and len(columns) == 3
    assert len(rows) == 7


def test_check_sql_detects_errors_and_unknown_names(engine: AccessEngine) -> None:
    ok = engine.queries.check_sql("SELECT Nachname, Ort FROM Kunden WHERE Ort = 'Imst'")
    assert ok.ok and ok.kind == "select" and ok.fields == ["Nachname", "Ort"] and ok.parameters == []
    typo = engine.queries.check_sql("SELECT Nachnahme FROM Kunden")
    assert typo.ok and typo.parameters == ["Nachnahme"], "unbekannte Feldnamen erscheinen als Parameter"
    broken = engine.queries.check_sql("SELEC Nachname FROM Kunden")
    assert not broken.ok and broken.error
    missing_table = engine.queries.check_sql("SELECT * FROM GibtEsNicht")
    assert not missing_table.ok
    multi = engine.queries.check_sql("SELECT 1; DROP TABLE Kunden")
    assert not multi.ok
    with pytest.raises(SqlSyntaxError):
        engine.queries.create_query("qryKaputt", "SELECT FROM WHERE")
    assert not engine.queries.exists("qryKaputt")


def test_action_queries_with_transaction_guard(engine: AccessEngine) -> None:
    update = QuerySpec(kind="update", tables=["Artikel"], set_values={"Einzelpreis": "[Einzelpreis]*1,1"},
                       fields=[QueryFieldSpec(field="Kategorie", criteria="Hardware")])
    info = engine.queries.create_query("qryPreiserhoehung", build_sql(update))
    assert info.is_action and info.type_name == "Aktualisierungsabfrage"
    with pytest.raises(AccessError):
        engine.queries.run_select("qryPreiserhoehung")
    with pytest.raises(ApprovalRequiredError):
        engine.queries.run_action(name="qryPreiserhoehung", max_affected=2)
    _, rows = engine.tables.read_sql("SELECT Einzelpreis FROM Artikel WHERE ArtikelNr=1")
    assert rows == [[899.0]], "zurückgerollt"
    assert engine.queries.run_action(name="qryPreiserhoehung", max_affected=10) == 3
    _, rows = engine.tables.read_sql("SELECT Einzelpreis FROM Artikel WHERE ArtikelNr=1")
    assert rows[0][0] == pytest.approx(988.9)

    make = QuerySpec(kind="make_table", tables=["Kunden"], target_table="Tiroler",
                     fields=[QueryFieldSpec(field="Nachname"), QueryFieldSpec(field="Bundesland", criteria="Tirol")])
    assert engine.queries.run_action(sql=build_sql(make)) == 6
    assert engine.tables.table_exists("Tiroler")
    append = QuerySpec(kind="append", tables=["Kunden"], target_table="Tiroler",
                       fields=[QueryFieldSpec(field="Nachname"), QueryFieldSpec(field="Bundesland", criteria="Wien")])
    assert engine.queries.run_action(sql=build_sql(append)) == 1
    delete = QuerySpec(kind="delete", tables=["Tiroler"], fields=[QueryFieldSpec(field="Bundesland", criteria="Wien")])
    assert engine.queries.run_action(sql=build_sql(delete)) == 1
    assert engine.tables.count_records("Tiroler") == 6
    with pytest.raises(AccessError):
        engine.queries.run_action(sql="SELECT * FROM Kunden")


# --------------------------------------------------------------------------- Beziehungen
def test_create_relationships_with_integrity(engine: AccessEngine) -> None:
    """Test D: zwei passende Tabellen über Schlüssel verbinden."""
    name = engine.relations.create_relationship(
        "Kunden", "Bestellungen", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")],
        enforce_integrity=True, cascade_update=True,
    )
    relations = engine.schema().relations
    assert len(relations) == 1
    rel = relations[0]
    assert rel.name == name
    assert (rel.table, rel.foreign_table, rel.fields) == ("Kunden", "Bestellungen", [("KundenNr", "KundenNr")])
    assert rel.enforce_integrity and rel.cascade_update and not rel.cascade_delete
    assert rel.relation_type == "1:n"
    # Integrität greift wirklich
    with pytest.raises(DataIntegrityError):
        engine.tables.insert_records("Bestellungen", [{"KundenNr": 999, "ArtikelNr": 1, "Menge": 1}])
    with pytest.raises(DataIntegrityError):
        engine.tables.delete_records("Kunden", "KundenNr = 1")
    with pytest.raises(ObjectExistsError):
        engine.relations.create_relationship(
            "Kunden", "Bestellungen", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")]
        )
    engine.relations.delete_relationship(name)
    assert engine.schema().relations == []
    with pytest.raises(ObjectNotFoundError):
        engine.relations.delete_relationship(name)


def test_relationship_conflicts_are_detected_before_change(engine: AccessEngine) -> None:
    relations = engine.relations
    # verwaiste Datensätze
    engine.tables.insert_records("Bestellungen", [{"KundenNr": 777, "ArtikelNr": 1, "Menge": 1}])
    pairs = [RelationFieldPair(primary="KundenNr", foreign="KundenNr")]
    problems = relations.check_relation("Kunden", "Bestellungen", pairs, enforce=True)
    assert len(problems) == 1 and "1 Datensätze" in problems[0]
    with pytest.raises(DataIntegrityError):
        relations.create_relationship("Kunden", "Bestellungen", pairs)
    # ohne Integrität ist es erlaubt
    name = relations.create_relationship("Kunden", "Bestellungen", pairs, enforce_integrity=False)
    assert engine.schema().relations[0].enforce_integrity is False
    relations.delete_relationship(name)
    # unpassende Datentypen
    problems = relations.check_relation(
        "Kunden", "Bestellungen", [RelationFieldPair(primary="Nachname", foreign="Menge")], enforce=False
    )
    assert problems and "Datentypen" in problems[0]
    # fehlender eindeutiger Index auf der 1-Seite
    problems = relations.check_relation(
        "Bestellungen", "Kunden", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")], enforce=True
    )
    assert problems and "eindeutigen Index" in problems[0]
    with pytest.raises(ObjectNotFoundError):
        relations.check_relation("Kunden", "Nirgendwo", pairs, enforce=True)
    with pytest.raises(ObjectNotFoundError):
        relations.check_relation(
            "Kunden", "Bestellungen", [RelationFieldPair(primary="KundenNr", foreign="Fehlt")], enforce=True
        )
    assert engine.schema().relations == []


def test_one_to_one_detection_and_candidates(engine: AccessEngine) -> None:
    engine.tables.create_table(
        "Kundendetails",
        [FieldSpec(name="KundenNr", type="long", primary_key=True), FieldSpec(name="Notiz", type="memo")],
    )
    engine.relations.create_relationship(
        "Kunden", "Kundendetails", [RelationFieldPair(primary="KundenNr", foreign="KundenNr")]
    )
    rel = engine.relations.find_relation("Kunden", "Kundendetails")
    assert rel is not None and rel.relation_type == "1:1"
    candidates = engine.relations.foreign_key_candidates()
    assert {"table": "Artikel", "field": "ArtikelNr", "foreign_table": "Bestellungen",
            "foreign_field": "ArtikelNr"} in candidates
    # bestehende Beziehungen bleiben beim Anlegen weiterer erhalten
    engine.relations.create_relationship(
        "Artikel", "Bestellungen", [RelationFieldPair(primary="ArtikelNr", foreign="ArtikelNr")], cascade_delete=True
    )
    names = {(r.table, r.foreign_table) for r in engine.schema().relations}
    assert names == {("Kunden", "Kundendetails"), ("Artikel", "Bestellungen")}
    with pytest.raises(DataIntegrityError):
        engine.tables.delete_table("Artikel")

"""Unit-Tests: Kriterienübersetzung, SQL-Erzeugung und SQL-Analyse (ohne Access)."""
from __future__ import annotations

import pytest

from app.access.models import DatabaseSchema, FieldInfo, RelationInfo, TableInfo
from app.access.specs import CrosstabSpec, JoinSpec, QueryFieldSpec, QueryParameterSpec, QuerySpec
from app.access.sqlbuilder import (
    build_sql,
    classify_sql,
    ensure_safe_fragment,
    ensure_single_statement,
    infer_joins,
    localize_expression,
    translate_criteria,
)
from app.core.errors import PlanValidationError, SqlSyntaxError


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        ('"Tirol"', '[F] = "Tirol"'),
        ("Tirol", '[F] = "Tirol"'),
        ("'Tirol'", '[F] = "Tirol"'),
        ('="Tirol"', '[F] = "Tirol"'),
        ("Bad Ischl", '[F] = "Bad Ischl"'),
        (">100", "[F] > 100"),
        (">=1,5", "[F] >= 1.5"),
        ("<>0", "[F] <> 0"),
        ("!= 3", "[F] <> 3"),
        ("5", "[F] = 5"),
        (">100 Und <=500", "[F] > 100 And [F] <= 500"),
        (">100 and <=500", "[F] > 100 And [F] <= 500"),
        ('"Imst" Oder "Wien"', '[F] = "Imst" Or [F] = "Wien"'),
        ("Imst oder Wien", '[F] = "Imst" Or [F] = "Wien"'),
        ('Wie "A*"', '[F] Like "A*"'),
        ("Wie A*", '[F] Like "A*"'),
        ("Like '*berg'", '[F] Like "*berg"'),
        ("A*", '[F] Like "A*"'),
        ('"*er"', '[F] Like "*er"'),
        ('Nicht Wie "A*"', '[F] Not Like "A*"'),
        ("Zwischen 10 Und 20", "[F] Between 10 And 20"),
        ("Between 10 And 20 Or >100", "[F] Between 10 And 20 Or [F] > 100"),
        ("Zwischen 01.01.2020 Und 31.12.2020", "[F] Between #01/01/2020# And #12/31/2020#"),
        (">01.05.2024", "[F] > #05/01/2024#"),
        ("#15.03.2021#", "[F] = #03/15/2021#"),
        ("Ist Null", "[F] Is Null"),
        ("Ist Nicht Null", "[F] Is Not Null"),
        ("Is Not Null", "[F] Is Not Null"),
        ("Null", "[F] Is Null"),
        ("Nicht Null", "[F] Is Not Null"),
        ("Wahr", "[F] = True"),
        ("Nein", "[F] = False"),
        ("Ja", "[F] = True"),
        ('Nicht "Wien"', '[F] <> "Wien"'),
        ("Nicht Wien", '[F] <> "Wien"'),
        ('In ("Wien";"Graz")', '[F] In ("Wien", "Graz")'),
        ("In (1, 2, 3)", "[F] In (1, 2, 3)"),
        ("In (Wien, Graz)", '[F] In ("Wien", "Graz")'),
        ("[Bitte Ort eingeben]", "[F] = [Bitte Ort eingeben]"),
        ('Wie [Anfang] & "*"', '[F] Like [Anfang] & "*"'),
        (">Datum()", "[F] > Date()"),
        ("<Date()-30", "[F] < Date() - 30"),
        (">=[Mindestumsatz]", "[F] >= [Mindestumsatz]"),
        ('(>5 Und <10) Oder 99', "([F] > 5 And [F] < 10) Or [F] = 99"),
    ],
)
def test_translate_criteria(criteria: str, expected: str) -> None:
    assert translate_criteria("[F]", criteria) == expected


@pytest.mark.parametrize("bad", ["", "   ", "Zwischen 1", "Ist Blau", ">"])
def test_translate_criteria_errors(bad: str) -> None:
    with pytest.raises(SqlSyntaxError):
        translate_criteria("[F]", bad)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("[Menge]*[Einzelpreis]", "[Menge] * [Einzelpreis]"),
        ("=[Preis] * 1,2", "[Preis] * 1.2"),
        ("Jahr([Geburtsdatum])", "Year([Geburtsdatum])"),
        ('[Vorname] & " " & [Nachname]', '[Vorname] & " " & [Nachname]'),
        ('Wenn([Umsatz]>1000;"A";"B")', 'IIf([Umsatz] > 1000, "A", "B")'),
        ("Runden([Preis]*0,2;2)", "Round([Preis] * 0.2, 2)"),
        ("DatDiff(\"yyyy\";[Geburtsdatum];Datum())", 'DateDiff("yyyy", [Geburtsdatum], Date())'),
    ],
)
def test_localize_expression(expression: str, expected: str) -> None:
    assert localize_expression(expression) == expected


def _schema() -> DatabaseSchema:
    def fields(*names: str) -> list[FieldInfo]:
        return [FieldInfo(name=n, type="long" if n in ("Menge", "Einzelpreis") else "short_text") for n in names]

    return DatabaseSchema(
        path="x.accdb",
        name="x.accdb",
        tables=[
            TableInfo(name="Kunden", fields=fields("KundenNr", "Nachname", "Ort", "Bundesland")),
            TableInfo(name="Bestellungen", fields=fields("BestellNr", "KundenNr", "ArtikelNr", "Menge")),
            TableInfo(name="Artikel", fields=fields("ArtikelNr", "Bezeichnung", "Einzelpreis")),
        ],
        relations=[
            RelationInfo(name="r1", table="Kunden", foreign_table="Bestellungen", fields=[("KundenNr", "KundenNr")]),
            RelationInfo(name="r2", table="Artikel", foreign_table="Bestellungen", fields=[("ArtikelNr", "ArtikelNr")]),
        ],
    )


def test_criteria_follow_field_type() -> None:
    """Zahl auf Textfeld wird gequotet, Zahlen-Text auf Zahlenfeld entquotet (sonst Datentypenkonflikt)."""

    def where(table: str, field: str, criteria: str) -> str:
        spec = QuerySpec(tables=[table], fields=[QueryFieldSpec(field=field, criteria=criteria)])
        return build_sql(spec, _schema()).split("WHERE ")[1]

    assert where("Kunden", "Ort", "6460") == '([Ort] = "6460");'
    assert where("Kunden", "Ort", ">=6000 Und <7000") == '([Ort] >= "6000" And [Ort] < "7000");'
    assert where("Kunden", "Ort", "In (6460; 6500)") == '([Ort] In ("6460", "6500"));'
    assert where("Kunden", "Ort", "Links([Ort];2)") == "([Ort] = Left([Ort], 2));"
    assert where("Bestellungen", "Menge", '"5"') == "([Menge] = 5);"
    assert where("Bestellungen", "Menge", ">1") == "([Menge] > 1);"


def test_simple_select_with_criteria_and_sort() -> None:
    """Test B (SQL-Teil): Kunden aus Tirol, sortiert nach Nachname."""
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[
            QueryFieldSpec(field="Nachname", sort="ASC"),
            QueryFieldSpec(field="Vorname"),
            QueryFieldSpec(field="Ort"),
            QueryFieldSpec(field="Bundesland", criteria='"Tirol"', show=False),
        ],
    )
    assert build_sql(spec) == (
        'SELECT [Nachname], [Vorname], [Ort]\nFROM [Kunden]\nWHERE ([Bundesland] = "Tirol")\nORDER BY [Nachname];'
    )


def test_or_rows_and_extra_where() -> None:
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[
            QueryFieldSpec(field="Nachname"),
            QueryFieldSpec(field="Ort", criteria="Imst", or_criteria=["Wien"]),
            QueryFieldSpec(field="Umsatz", criteria=">1000", or_criteria=[">500"], sort="absteigend"),
        ],
        where="[Newsletter] = True",
        distinct=True,
        top=5,
    )
    sql = build_sql(spec)
    assert sql.startswith("SELECT DISTINCT TOP 5 [Nachname], [Ort], [Umsatz]\nFROM [Kunden]\nWHERE ")
    assert '((([Ort] = "Imst") AND ([Umsatz] > 1000)) OR (([Ort] = "Wien") AND ([Umsatz] > 500)))' in sql
    assert sql.endswith("AND ([Newsletter] = True)\nORDER BY [Umsatz] DESC;")


def test_calculated_field_and_parameters() -> None:
    spec = QuerySpec(
        tables=["Bestellungen"],
        fields=[
            QueryFieldSpec(field="BestellNr"),
            QueryFieldSpec(expression="[Menge]*[Einzelpreis]", alias="Gesamtpreis"),
            QueryFieldSpec(field="Menge", criteria=">=[Mindestmenge]"),
        ],
        parameters=[QueryParameterSpec(name="Mindestmenge", type="Zahl")],
    )
    assert build_sql(spec) == (
        "PARAMETERS [Mindestmenge] Long;\n"
        "SELECT [BestellNr], [Menge] * [Einzelpreis] AS [Gesamtpreis], [Menge]\n"
        "FROM [Bestellungen]\nWHERE ([Menge] >= [Mindestmenge]);"
    )


def test_totals_query_group_by_having() -> None:
    spec = QuerySpec(
        tables=["Kunden"],
        fields=[
            QueryFieldSpec(field="Bundesland", aggregate="Gruppierung", sort="ASC"),
            QueryFieldSpec(field="Umsatz", aggregate="Summe", criteria=">1000"),
            QueryFieldSpec(field="KundenNr", aggregate="Anzahl", alias="AnzahlKunden"),
            QueryFieldSpec(field="Newsletter", aggregate="Bedingung", criteria="Wahr"),
        ],
    )
    assert build_sql(spec) == (
        "SELECT [Bundesland], Sum([Umsatz]) AS [SummevonUmsatz], Count([KundenNr]) AS [AnzahlKunden]\n"
        "FROM [Kunden]\nWHERE ([Newsletter] = True)\nGROUP BY [Bundesland]\n"
        "HAVING (Sum([Umsatz]) > 1000)\nORDER BY [Bundesland];"
    )


def test_joins_inferred_from_relations_and_fields_qualified() -> None:
    spec = QuerySpec(
        tables=["Kunden", "Bestellungen", "Artikel"],
        fields=[
            QueryFieldSpec(field="Nachname"),
            QueryFieldSpec(field="Bezeichnung"),
            QueryFieldSpec(field="Menge", criteria=">1"),
        ],
    )
    sql = build_sql(spec, _schema())
    assert "SELECT [Kunden].[Nachname], [Artikel].[Bezeichnung], [Bestellungen].[Menge]" in sql
    assert (
        "FROM ([Kunden] INNER JOIN [Bestellungen] ON [Kunden].[KundenNr] = [Bestellungen].[KundenNr]) "
        "INNER JOIN [Artikel] ON [Artikel].[ArtikelNr] = [Bestellungen].[ArtikelNr]"
    ) in sql
    # mehrdeutiges Feld ohne Tabellenangabe
    with pytest.raises(PlanValidationError):
        build_sql(QuerySpec(tables=["Kunden", "Bestellungen"], fields=[QueryFieldSpec(field="KundenNr")]), _schema())


def test_explicit_left_join() -> None:
    spec = QuerySpec(
        tables=["Kunden", "Bestellungen"],
        joins=[JoinSpec(left_table="Kunden", right_table="Bestellungen", left_field="KundenNr",
                        right_field="KundenNr", type="LEFT JOIN")],
        fields=[QueryFieldSpec(field="Nachname", table="Kunden"),
                QueryFieldSpec(field="BestellNr", table="Bestellungen", criteria="Ist Null")],
    )
    assert build_sql(spec) == (
        "SELECT [Kunden].[Nachname], [Bestellungen].[BestellNr]\n"
        "FROM [Kunden] LEFT JOIN [Bestellungen] ON [Kunden].[KundenNr] = [Bestellungen].[KundenNr]\n"
        "WHERE ([Bestellungen].[BestellNr] Is Null);"
    )


def test_infer_joins_ignores_unrelated_tables() -> None:
    joins = infer_joins(["Kunden", "Artikel"], _schema().relations)
    assert joins == []


def test_action_queries() -> None:
    update = QuerySpec(kind="update", tables=["Artikel"], set_values={"Einzelpreis": "[Einzelpreis]*1,1"},
                       fields=[QueryFieldSpec(field="Kategorie", criteria="Software")])
    assert build_sql(update) == (
        'UPDATE [Artikel] SET [Einzelpreis] = [Einzelpreis] * 1.1\nWHERE ([Kategorie] = "Software");'
    )
    delete = QuerySpec(kind="delete", tables=["Artikel"], fields=[QueryFieldSpec(field="Lagerbestand", criteria="0")])
    assert build_sql(delete) == "DELETE [Artikel].*\nFROM [Artikel]\nWHERE ([Lagerbestand] = 0);"
    append = QuerySpec(kind="append", tables=["Kunden"], target_table="Archiv",
                       fields=[QueryFieldSpec(field="Nachname"), QueryFieldSpec(field="Ort", criteria="Wien")])
    assert build_sql(append) == (
        'INSERT INTO [Archiv] ( [Nachname], [Ort] )\nSELECT [Nachname], [Ort]\nFROM [Kunden]\nWHERE ([Ort] = "Wien");'
    )
    make = QuerySpec(kind="make_table", tables=["Kunden"], target_table="Tiroler",
                     fields=[QueryFieldSpec(field="*"), QueryFieldSpec(field="Bundesland", criteria="Tirol", show=False)])
    assert build_sql(make) == 'SELECT * INTO [Tiroler]\nFROM [Kunden]\nWHERE ([Bundesland] = "Tirol");'


def test_crosstab() -> None:
    spec = QuerySpec(
        kind="crosstab",
        tables=["Kunden"],
        crosstab=CrosstabSpec(row_headings=["Bundesland"], column_heading="Newsletter", value_field="Umsatz"),
    )
    assert build_sql(spec) == (
        "TRANSFORM Sum([Umsatz]) AS [SummevonUmsatz]\nSELECT [Bundesland]\nFROM [Kunden]\n"
        "GROUP BY [Bundesland]\nPIVOT [Newsletter];"
    )


def test_spec_validation() -> None:
    with pytest.raises(ValueError):
        QuerySpec(tables=["Kunden"], fields=[])
    with pytest.raises(ValueError):
        QueryFieldSpec()
    with pytest.raises(ValueError):
        QueryFieldSpec(expression="[A]+[B]")
    with pytest.raises(ValueError):
        QuerySpec(kind="update", tables=["A"])
    with pytest.raises(ValueError):
        QuerySpec(tables=["Kunden"], fields=[QueryFieldSpec(field="A")], unbekannt=1)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT * FROM Kunden", "select"),
        ("  select a from b;", "select"),
        ("PARAMETERS [x] Long; SELECT * FROM t WHERE a=[x];", "select"),
        ("SELECT a FROM b UNION SELECT a FROM c", "union"),
        ("SELECT * INTO Neu FROM Kunden", "make_table"),
        ("SELECT * INTO Neu FROM (SELECT a FROM b UNION ALL SELECT a FROM c) AS U", "make_table"),  # Codex-Review
        ("SELECT [INTO] FROM Kunden", "select"),
        ("SELECT 'UNION' AS x FROM Kunden", "select"),
        ("TRANSFORM Sum(x) SELECT a FROM b GROUP BY a PIVOT c", "crosstab"),
        ("UPDATE t SET a=1", "update"),
        ("DELETE FROM t", "delete"),
        ("INSERT INTO t (a) VALUES (1)", "append"),
        ("DROP TABLE t", "ddl"),
        ("ALTER TABLE t ADD COLUMN x TEXT", "ddl"),
        ("EXEC irgendwas", "unknown"),
    ],
)
def test_classify_sql(sql: str, kind: str) -> None:
    assert classify_sql(sql) == kind


def test_single_statement_guard() -> None:
    assert ensure_single_statement(" SELECT 1; ") == "SELECT 1;"
    assert ensure_single_statement("SELECT 'a;b' FROM t") == "SELECT 'a;b' FROM t"
    assert ensure_single_statement("PARAMETERS [p] Text ( 255 ); SELECT * FROM t;")
    for bad in ("", "SELECT 1; DROP TABLE t", "PARAMETERS [p] Long; SELECT 1; DELETE FROM t", "PARAMETERS [p] Long"):
        with pytest.raises(SqlSyntaxError):
            ensure_single_statement(bad)


def test_safe_fragment_guard() -> None:
    assert ensure_safe_fragment("Ort = 'Imst';") == "Ort = 'Imst'"
    assert ensure_safe_fragment("Name = 'Drop Dead'") == "Name = 'Drop Dead'"
    assert ensure_safe_fragment("[Update] = 1") == "[Update] = 1"
    for bad in ("", "1=1; DELETE FROM t", "x = 1 OR DROP TABLE t", "a IN (DELETE FROM t)"):
        with pytest.raises(SqlSyntaxError):
            ensure_safe_fragment(bad)

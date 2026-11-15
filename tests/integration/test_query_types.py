"""Abfragetypen der te.comp-Lernprogramme 04/06 in echtem Access: Spitzenwerte, Duplikate, berechnete Felder,
Wenn-Entscheidung, Datumsfunktionen, Gruppierung, Inkonsistenz (Inner/Left Join) und Parameter.

Jede Abfrage entsteht wie im Programm (QuerySpec → Access-SQL → gespeicherte Abfrage) und ihr Ergebnis wird mit
Werten verglichen, die der Test unabhängig aus den Rohdaten berechnet.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.access.engine import AccessEngine
from app.access.specs import JoinSpec, QueryFieldSpec, QueryParameterSpec, QuerySpec
from app.access.sqlbuilder import build_sql

pytestmark = [pytest.mark.access]


def _save(engine: AccessEngine, name: str, spec: QuerySpec) -> list[list[Any]]:
    engine.queries.create_query(name, build_sql(spec, engine.schema()))
    return engine.queries.run_select(name, limit=5000)[1]


def _raw(engine: AccessEngine, sql: str) -> list[list[Any]]:
    return engine.queries.run_select_sql(sql, limit=5000)[1]


def test_top_values_and_percent(engine: AccessEngine) -> None:
    """0602: „Die besten 3“ und „die besten 50 Prozent“ nach Umsatz."""
    umsatz = sorted((float(r[0]) for r in _raw(engine, "SELECT Umsatz FROM Kunden") if r[0] is not None), reverse=True)
    top = _save(engine, "Top 3", QuerySpec(tables=["Kunden"], top=3, fields=[
        QueryFieldSpec(field="Nachname"), QueryFieldSpec(field="Umsatz", sort="DESC")]))
    assert [float(r[1]) for r in top] == umsatz[:3]
    half = _save(engine, "Top 50 Prozent", QuerySpec(tables=["Kunden"], top=50, top_percent=True, fields=[
        QueryFieldSpec(field="Umsatz", sort="DESC")]))
    assert len(half) >= len(umsatz) // 2 and float(half[0][0]) == umsatz[0]


def test_distinct_list(engine: AccessEngine) -> None:
    """0603: Liste ohne Duplikate."""
    expected = {r[0] for r in _raw(engine, "SELECT Bundesland FROM Kunden")}
    rows = _save(engine, "Bundesländer", QuerySpec(tables=["Kunden"], distinct=True,
                                                    fields=[QueryFieldSpec(field="Bundesland", sort="ASC")]))
    assert [r[0] for r in rows] == sorted(expected, key=lambda v: (v is None, v)) and len(rows) == len(expected)


def test_calculated_iif_concatenation_and_dates(engine: AccessEngine) -> None:
    """0604–0607: zusammengesetzter Name, Wenn-Entscheidung (deutsche Schreibweise), Alter und Geburtsmonat."""
    rows = _save(engine, "Kundeninfo", QuerySpec(tables=["Kunden"], fields=[
        QueryFieldSpec(field="KundenNr", sort="ASC"),
        QueryFieldSpec(expression='[Vorname] & " " & [Nachname]', alias="Name"),
        QueryFieldSpec(expression='Wenn([Umsatz]>1000;"Top";"Normal")', alias="Klasse"),
        QueryFieldSpec(expression='DatDiff("yyyy";[Geburtsdatum];#1/1/2026#)', alias="Jahre"),
        QueryFieldSpec(expression="Monat([Geburtsdatum])", alias="Monat")]))
    raw = {r[0]: r for r in _raw(engine, "SELECT KundenNr, Vorname, Nachname, Umsatz, Geburtsdatum FROM Kunden")}
    assert len(rows) == len(raw)
    for nr, name, klasse, jahre, monat in rows:
        _, vor, nach, umsatz, geb = raw[nr]
        assert name == f"{vor or ''} {nach}".strip() or name == f"{vor} {nach}"
        assert klasse == ("Top" if umsatz is not None and float(umsatz) > 1000 else "Normal")
        if geb is not None:
            geb_d = geb if isinstance(geb, dt.datetime) else dt.datetime.strptime(str(geb)[:10], "%d.%m.%Y")
            assert jahre == 2026 - geb_d.year and monat == geb_d.month


def test_grouping_with_having(engine: AccessEngine) -> None:
    """0608: Anzahl und Summe je Gruppe, nur Gruppen über einer Grenze, absteigend sortiert."""
    raw = _raw(engine, "SELECT Bundesland, Umsatz FROM Kunden")
    sums: dict[Any, float] = {}
    for land, umsatz in raw:
        sums[land] = sums.get(land, 0.0) + float(umsatz or 0)
    rows = _save(engine, "Umsatz je Land", QuerySpec(tables=["Kunden"], having="Sum([Umsatz])>500", fields=[
        QueryFieldSpec(field="Bundesland", aggregate="group_by"),
        QueryFieldSpec(field="Umsatz", aggregate="sum", alias="Summe", sort="DESC")]))
    expected = sorted((v for v in sums.values() if v > 500), reverse=True)
    assert [round(float(r[1]), 2) for r in rows] == [round(v, 2) for v in expected]


def test_inconsistency_left_join_is_null(engine: AccessEngine) -> None:
    """0609: Kunden ohne Bestellung (Inkonsistenzabfrage)."""
    with_orders = {r[0] for r in _raw(engine, "SELECT DISTINCT KundenNr FROM Bestellungen")}
    all_customers = {r[0] for r in _raw(engine, "SELECT KundenNr FROM Kunden")}
    rows = _save(engine, "Kunden ohne Bestellung", QuerySpec(
        tables=["Kunden", "Bestellungen"],
        joins=[JoinSpec(left_table="Kunden", right_table="Bestellungen", left_field="KundenNr",
                        right_field="KundenNr", type="left")],
        fields=[QueryFieldSpec(field="KundenNr", table="Kunden"),
                QueryFieldSpec(field="BestellNr", table="Bestellungen", criteria="Is Null", show=False)]))
    assert {r[0] for r in rows} == all_customers - with_orders


def test_parameter_query(engine: AccessEngine) -> None:
    """0601: Parameterabfrage mit erkennbarer Eingabeaufforderung (ohne Punkt: Access liest „a.b“ als Tabelle.Feld)."""
    engine.queries.create_query("Kundensuche", build_sql(QuerySpec(
        tables=["Kunden"], parameters=[QueryParameterSpec(name="Bundesland eingeben (zum Beispiel Tirol)")],
        fields=[QueryFieldSpec(field="Nachname"),
                QueryFieldSpec(field="Bundesland", criteria="[Bundesland eingeben (zum Beispiel Tirol)]")]), engine.schema()))
    expected = len(_raw(engine, "SELECT * FROM Kunden WHERE Bundesland='Tirol'"))
    rows = engine.queries.run_select("Kundensuche", parameters={"Bundesland eingeben (zum Beispiel Tirol)": "Tirol"})[1]
    assert len(rows) == expected > 0

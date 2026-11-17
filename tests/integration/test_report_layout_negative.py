"""Beta 4, Fall „Students“: Berichtslayout wird am gespeicherten Bericht nachgemessen – absichtlich falsche Layouts
müssen auffallen, das richtige Layout muss bestehen. Eigene Testdatenbank, keine echten Übungen."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.access.engine import AccessEngine
from app.access.specs import ReportChangeSpec
from app.agent.executor import AccessExecutor
from app.agent.verifier import ResultVerifier
from app.tasks import models as m
from app.utils.sampledb import create_sample_database

pytestmark = pytest.mark.access_app

LAYOUT_CHECKS = {"Beschriftung links neben dem richtigen Feld", "Felder untereinander in der verlangten Reihenfolge",
                 "Spaltenüberschrift über dem richtigen Feld", "Spalten in der verlangten Reihenfolge",
                 "Steuerelemente im Detailbereich überlappen nicht", "Felder im Detailbereich", "2 Spalte(n)"}


@pytest.fixture
def engine(tmp_path: Path) -> Any:
    eng = AccessEngine.open(create_sample_database(tmp_path / "Layout.accdb"), prefer_running=False)
    yield eng
    eng.close()


def _create(engine: AccessEngine, report: dict[str, Any]) -> tuple[m.PlanStep, Any]:
    step = m.PlanStep.model_validate({"id": 1, "title": "Bericht", "action": {"action": "create_report",
                                                                              "report": report}})
    return step, AccessExecutor().execute(engine, step)


def _failed(engine: AccessEngine, step: m.PlanStep, result: Any) -> set[str]:
    return {c.name for c in ResultVerifier().verify(engine, step, result).failed_checks} & LAYOUT_CHECKS


def _move(engine: AccessEngine, report: str, **change: Any) -> None:
    engine.objects.modify_report(report, ReportChangeSpec.model_validate({"control_changes": [change]}))


COLUMNAR = {"name": "rptKartei", "record_source": "Kunden", "fields": ["Nachname", "Vorname", "Ort"],
            "layout": "columnar", "page_setup": {"columns": 2}}


def test_columnar_report_with_two_print_columns_passes(engine: AccessEngine) -> None:
    step, result = _create(engine, COLUMNAR)
    assert result.status == "ok", result.message
    assert _failed(engine, step, result) == set(), "richtiges Layout: Spaltenlayout einspaltig, Druck zweispaltig"


def test_label_on_the_wrong_side_is_rejected(engine: AccessEngine) -> None:
    step, result = _create(engine, COLUMNAR)
    _move(engine, "rptKartei", name="lblOrt", left_cm=13.0, width_cm=2.0)  # Beschriftung rechts vom Feld
    assert "Beschriftung links neben dem richtigen Feld" in _failed(engine, step, result)


def test_swapped_label_captions_are_rejected(engine: AccessEngine) -> None:
    step, result = _create(engine, COLUMNAR)
    engine.objects.modify_report("rptKartei", ReportChangeSpec.model_validate({"control_changes": [
        {"name": "lblNachname", "caption": "Vorname"}, {"name": "lblVorname", "caption": "Nachname"}]}))
    assert "Beschriftung links neben dem richtigen Feld" in _failed(engine, step, result), "optisch ähnlich, falsch"


def test_overlapping_field_is_rejected(engine: AccessEngine) -> None:
    step, result = _create(engine, COLUMNAR)
    info = engine.objects.inspect_report("rptKartei")
    nachname = next(x for x in info.controls if x.control_source == "Nachname")
    _move(engine, "rptKartei", name="Vorname", top_cm=nachname.top / 567)  # Feld über ein anderes geschoben
    # WIDS räumt Überlappungen beim Ändern selbst auf – die verschobene Zeile fällt trotzdem auf
    assert _failed(engine, step, result) & {"Steuerelemente im Detailbereich überlappen nicht",
                                            "Felder untereinander in der verlangten Reihenfolge",
                                            "Beschriftung links neben dem richtigen Feld"}


def test_layout_check_detects_overlap_and_missing_field_on_saved_controls() -> None:
    """Überlappung bzw. fehlendes Feld direkt an gespeicherten Steuerelement-Daten (ohne Bereinigung dazwischen)."""
    from app.access import constants as c
    from app.access.models import ControlInfo, ReportInfo
    from app.access.specs import ReportSpec
    from app.agent.verifier import Verification, _check_report_layout

    def ctl(name: str, kind: int, left: int, top: int, source: str = "", caption: str = "") -> ControlInfo:
        return ControlInfo(name=name, control_type=kind, control_source=source, caption=caption, section=c.AC_DETAIL,
                           left=left, top=top, width=1500, height=300)

    spec = ReportSpec.model_validate({"name": "r", "record_source": "Kunden", "fields": ["Nachname", "Ort"],
                                      "layout": "columnar"})
    good = [ctl("lblNachname", c.AC_LABEL, 0, 0, caption="Nachname"), ctl("Nachname", c.AC_TEXT_BOX, 1700, 0, "Nachname"),
            ctl("lblOrt", c.AC_LABEL, 0, 400, caption="Ort"), ctl("Ort", c.AC_TEXT_BOX, 1700, 400, "Ort")]
    v = Verification(step_id=1)
    _check_report_layout(ReportInfo(name="r", controls=good), spec, v)
    assert v.passed, [x.name for x in v.failed_checks]
    clash = [*good[:3], ctl("Ort", c.AC_TEXT_BOX, 1800, 50, "Ort")]  # Ort liegt über Nachname
    v = Verification(step_id=1)
    _check_report_layout(ReportInfo(name="r", controls=clash), spec, v)
    assert "Steuerelemente im Detailbereich überlappen nicht" in {x.name for x in v.failed_checks}
    v = Verification(step_id=1)
    _check_report_layout(ReportInfo(name="r", controls=good[:3]), spec, v)
    assert "Felder im Detailbereich" in {x.name for x in v.failed_checks}, "fehlendes Feld"


def test_wrong_print_columns_are_rejected(engine: AccessEngine) -> None:
    step, result = _create(engine, {**COLUMNAR, "name": "rptEinspaltig", "page_setup": {"columns": 1}})
    wrong = m.PlanStep.model_validate({"id": 1, "title": "Bericht", "action": {"action": "create_report",
                                                                               "report": COLUMNAR}})
    result.final_name = "rptEinspaltig"
    assert "2 Spalte(n)" in _failed(engine, wrong, result), "zweispaltiger Druck verlangt, einspaltig gespeichert"


def test_tabular_columns_in_wrong_order_are_rejected(engine: AccessEngine) -> None:
    report = {"name": "rptListe", "record_source": "Kunden", "fields": ["Nachname", "Vorname", "Ort"]}
    step, result = _create(engine, report)
    assert _failed(engine, step, result) == set()
    info = engine.objects.inspect_report("rptListe")
    left = {x.name: x.left for x in info.controls}
    engine.objects.modify_report("rptListe", ReportChangeSpec.model_validate({"control_changes": [
        {"name": "Nachname", "left_cm": left["Ort"] / 567}, {"name": "Ort", "left_cm": left["Nachname"] / 567}]}))
    failed = _failed(engine, step, result)
    assert {"Spalten in der verlangten Reihenfolge", "Spaltenüberschrift über dem richtigen Feld"} <= failed

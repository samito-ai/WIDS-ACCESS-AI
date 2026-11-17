"""Golfclub-/Mitglieder-Formular: Feld mit den leeren Layoutzellen DARUNTER verbinden (nicht nur höher machen) und
gestrichelte Rahmen – am gespeicherten Formular nachgemessen. Eigene Beispieldatenbank, echtes Access."""
from __future__ import annotations

import pytest

from app.access import constants as c
from app.access.engine import AccessEngine
from app.access.specs import ControlChange, FormChangeSpec, FormSpec
from app.agent.verifier import ResultVerifier, Verification, _cells_covered
from app.core.errors import AccessError

pytestmark = [pytest.mark.access, pytest.mark.access_app]

INSERT_ROW_BELOW = 679  # Access: acCmdLayoutInsertRowBelow (gemessen)


def _form_with_empty_row(engine: AccessEngine) -> None:
    engine.objects.create_form(FormSpec(name="frmM", record_source="Kunden", layout="stacked",
                                        fields=["Vorname", "Nachname", "Ort"]))
    app = engine._s.application() if hasattr(engine, "_s") else engine.session.application()  # type: ignore[attr-defined]
    app.DoCmd.OpenForm("frmM", c.AC_DESIGN, "", "", -1, c.AC_HIDDEN)
    form = app.Forms("frmM")
    app.DoCmd.SelectObject(c.AC_FORM, "frmM", False)
    for ctl in form.Controls:
        ctl.InSelection = str(ctl.Name) == "Ort"
    app.DoCmd.RunCommand(INSERT_ROW_BELOW)  # leere Zellen unter „Ort“ wie in der te.comp-Vorlage
    app.DoCmd.Close(c.AC_FORM, "frmM", c.AC_SAVE_YES)


def test_merge_with_cells_below_and_dashed_border_are_real_and_verified(engine: AccessEngine) -> None:
    _form_with_empty_row(engine)
    before = {x.name: x for x in engine.objects.inspect_form("frmM").controls}
    assert _cells_covered(before["Ort"], list(before.values()), "rows") == 1
    changes = [ControlChange(name="Ort", merge_down=1), ControlChange(name="Vorname", border_style=2)]
    engine.objects.modify_form("frmM", FormChangeSpec(control_changes=changes))
    info = engine.objects.inspect_form("frmM")
    by_name = {x.name: x for x in info.controls}
    assert _cells_covered(by_name["Ort"], info.controls, "rows") == 2, "echte verbundene Zelle über 2 Zeilen"
    assert not [x for x in info.controls if x.control_type == c.AC_EMPTY_CELL and x.left == by_name["Ort"].left], \
        "die leere Zelle unter „Ort“ ist im Feld aufgegangen"
    assert by_name["Vorname"].border_style == 2 and by_name["Nachname"].border_style != 2, "nur das verlangte Feld"
    v = Verification(step_id=1)
    ResultVerifier._check_control_edits(info.controls, [], changes, [], v)
    names = {ch.name: ch.passed for ch in v.checks}
    assert names["„Ort“ über 2 Zeilen verbunden"] and names["Rahmenart „Vorname“"], v.checks


def test_merge_without_empty_cells_changes_nothing(engine: AccessEngine) -> None:
    engine.objects.create_form(FormSpec(name="frmN", record_source="Kunden", layout="stacked", fields=["Vorname", "Ort"]))
    with pytest.raises(AccessError, match="nicht genug leere Layoutzellen"):
        engine.objects.modify_form("frmN", FormChangeSpec(control_changes=[ControlChange(name="Vorname", merge_down=1)]))
    by_name = {x.name: x for x in engine.objects.inspect_form("frmN").controls}
    assert _cells_covered(by_name["Vorname"], list(by_name.values()), "rows") == 1


def test_height_alone_is_not_a_merged_cell(engine: AccessEngine) -> None:
    """Live-Fund Golfclub: das Bildfeld wurde nur höher gemacht – die Prüfung darf das nicht als verbunden werten."""
    _form_with_empty_row(engine)
    info = engine.objects.inspect_form("frmM")
    v = Verification(step_id=1)
    ResultVerifier._check_control_edits(info.controls, [], [ControlChange(name="Ort", merge_down=1)], [], v)
    assert not next(ch for ch in v.checks if ch.name == "„Ort“ über 2 Zeilen verbunden").passed

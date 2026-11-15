"""Namensunabhängigkeit im Kleinen: Pfade (auch UNC), te.comp-Aufgabentext nur für echte te.comp-Dateien,
tokenbewusster Vergleich von Datenquellen/Ausdrücken, SQL-Erkennung mit Zeilenumbruch (Codex-Befunde 14.11.)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.verifier import _normalize_source, _same_source
from app.vision import capture, tecomp


def test_database_path_is_read_from_any_access_title_including_unc() -> None:
    title = "Access - {0} : Datenbank- {1} (Access 2007 - 2016-Dateiformat)"
    for path in (r"C:\Schule\Datenbank1.accdb", r"D:\x y\Übung (Kopie) #2.accdb", r"\\server\freigabe\Aufgabe.accdb",
                 r"\\nas01\Schüler\1A\Golfclub.mdb"):
        assert tecomp.database_in_title(title.format(Path(path).stem, path)) == path
    assert tecomp.database_in_title("Access - Datenbank1 : Datenbank") == ""


def test_numbered_name_outside_tecomp_never_gets_tecomp_task_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine eigene Datei, die zufällig wie eine te.comp-Übung heißt, bekommt nie deren Aufgabentext."""
    workdir = Path.home() / "te.comp" / "te.comp-training"
    own = r"C:\Eigene\02011_7.accdb"
    real = str(workdir / "02011_12345678.accdb")

    def windows(paths: list[str]) -> list[capture.WindowInfo]:
        return [capture.WindowInfo(i, f"Access - x : Datenbank- {p}", "OMain", "MSACCESS.EXE", (0, 0, 1, 1))
                for i, p in enumerate(paths)]

    monkeypatch.setattr(tecomp, "visible_windows", lambda: windows([own]))
    assert tecomp.open_exercise() is None and tecomp.open_access_database() == own
    monkeypatch.setattr(tecomp, "visible_windows", lambda: windows([real, own]))
    found = tecomp.open_exercise()
    assert found is not None and found[0] == real
    # das VORDERSTE Access-Fenster zählt: liegt eine andere Datenbank (z. B. ein Workshop) vorne, gilt diese
    monkeypatch.setattr(tecomp, "visible_windows", lambda: windows([str(workdir / "CD-Sammlung_12345678.accdb"), real]))
    assert tecomp.open_exercise() is None and tecomp.open_access_database().endswith("CD-Sammlung_12345678.accdb")
    monkeypatch.setattr(tecomp, "visible_windows", lambda: windows([str(workdir / "Kopie 02011_7.accdb")]))
    assert tecomp.open_exercise() is None, "nur der exakte te.comp-Dateiname"


@pytest.mark.parametrize(("actual", "wanted", "same"), [
    ("=[Preis]*[Menge]", "=Preis * Menge", True),
    ("SELECT * FROM Kunden;", "select *  from  kunden", True),
    ("SELECT * FROM Kunden ;", "SELECT * FROM Kunden", True),
    ('="Ort: " & [Ort]', '="Ort: "&[Ort]', True),
    ('="A B"', '="AB"', False),
    ('="[Ort]"', '="Ort"', False),
    ("[Familien Name]", "[FamilienName]", False),
    ("Imster", "Imst", False),
    ("=[A-B]", "=[A]-[B]", False),
    ("[O'Brien]", "[o'brien]", True),
    ("=Summe([Gebühr])", "=summe(Gebühr)", True),
    ("=1+1", "=[1]+1", False),
    ("[A  B]", "[A B]", False),
    ("[Kunden].[Nachname]", "Kunden.Nachname", True),
])
def test_source_comparison_respects_literals_and_identifiers(actual: str, wanted: str, same: bool) -> None:
    assert _same_source(actual, wanted) is same, (_normalize_source(actual), _normalize_source(wanted))


def test_new_record_source_select_with_line_break_is_accepted() -> None:
    from app.access.models import DatabaseSchema, TableInfo
    from app.agent.planner import PlanValidator
    from app.tasks import models as m

    schema = DatabaseSchema(path="", name="x", tables=[TableInfo(name="Kunden")])
    plan = m.ExecutionPlan(steps=[
        m.PlanStep(id=1, title="a", action=m.ModifyForm.model_validate(
            {"action": "modify_form", "name": "frm", "changes": {"record_source": "SELECT\n* FROM Kunden"}})),
        m.PlanStep(id=2, title="b", action=m.ModifyReport.model_validate(
            {"action": "modify_report", "name": "rpt", "changes": {"record_source": "GibtEsNicht"}}))])
    errors = [e.message for e in PlanValidator().validate(plan, schema).errors]
    assert not any("SELECT" in e for e in errors) and any("„GibtEsNicht“ existiert nicht" in e for e in errors), errors


def _ctl(name: str, left_cm: float, width_cm: float, section: int = 0, top_cm: float = 0.0) -> Any:
    from app.access.models import ControlInfo

    return ControlInfo(name=name, control_type=109, section=section, left=round(left_cm * 567), top=round(top_cm * 567),
                       width=round(width_cm * 567), height=300, parent="Bericht")


def test_overlap_and_order_rules_of_the_layout_check() -> None:
    """Live-Fall 0501/U4 als reine Geometrie: Überdeckung erkannt (auch ohne Neuordnung, z. B. in Formularen);
    nach der Neuordnung zählt die Reihenfolge, nicht der exakte Zentimeterwert."""
    from app.agent.verifier import _overlaps, _same_row_rank

    broken = [_ctl("Tag", 2.7, 2.09), _ctl("TeeTime", 4.89, 2.31), _ctl("MID", 5.0, 1.8), _ctl("Flight", 13.2, 1.5)]
    assert _overlaps(broken, {"mid"}) == [("MID", "TeeTime")]
    assert _overlaps(broken, {"flight"}) == [], "nur geänderte Steuerelemente zählen"
    other_row = [_ctl("A", 0, 3), _ctl("B", 1, 3, top_cm=2.0), _ctl("C", 1, 3, section=3)]
    assert _overlaps(other_row, {"a"}) == [], "andere Zeile/anderer Bereich überdeckt nicht"
    fixed = [_ctl("Tag", 0.34, 2.09), _ctl("TeeTime", 2.53, 2.31), _ctl("MID", 4.94, 1.8), _ctl("Flight", 12.84, 1.5)]
    mid = fixed[2]
    assert _same_row_rank(mid, round(5.0 * 567), fixed), "5,0 cm angefordert → steht nach TeeTime, vor Flight"
    assert not _same_row_rank(mid, round(1.0 * 567), fixed), "1,0 cm angefordert hätte VOR TeeTime bedeutet"
    assert not _same_row_rank(broken[2], round(5.0 * 567), broken), "überdeckt → nie bestanden"

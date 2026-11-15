"""Zeilen-Neuordnung im Berichtslayout ohne Access (nachgebautes Berichtsobjekt) – Codex-Befunde zur Layout-Korrektur."""
from __future__ import annotations

from typing import Any

from app.access import constants as c
from app.access.forms_reports import _geometry, _reflow_overlapping_rows
from app.agent.verifier import Verification, _check_width_fit, _same_row_rank

CM = 567


class _Ctl:
    def __init__(self, name: str, section: int, left: float, top: float, width: float, height: float = 0.55,
                 kind: int = c.AC_TEXT_BOX) -> None:
        self.Name, self.Section, self.ControlType = name, section, kind
        self.Left, self.Top, self.Width, self.Height = round(left * CM), round(top * CM), round(width * CM), round(height * CM)


class _Controls(list):  # type: ignore[type-arg]
    def __call__(self, name: str) -> _Ctl:
        return next(x for x in self if x.Name.lower() == name.lower())


class _Report:
    def __init__(self, *controls: _Ctl, width: float = 16.5) -> None:
        self.Controls = _Controls(controls)
        self.Width = round(width * CM)


def _left(report: _Report, name: str) -> float:
    return round(report.Controls(name).Left / CM, 2)


def test_tall_control_does_not_merge_separate_rows() -> None:
    """Ein hohes Feld links und zwei untereinander stehende Felder rechts (gleicher Left) sind KEINE Überdeckung –
    eine reine Formatänderung an einem davon verschiebt nichts."""
    report = _Report(_Ctl("Bild", c.AC_DETAIL, 0.3, 0.0, 4.0, height=2.0), _Ctl("A", c.AC_DETAIL, 5.0, 0.0, 3.0),
                     _Ctl("B", c.AC_DETAIL, 5.0, 1.0, 3.0))
    before = _geometry(report)
    _reflow_overlapping_rows(report, before, {"b"})
    assert (_left(report, "A"), _left(report, "B"), _left(report, "Bild")) == (5.0, 5.0, 0.3)


def test_header_labels_stay_aligned_when_widths_differ() -> None:
    """Kopf-Beschriftung breiter als ihr Feld: beide wandern gemeinsam, die nächste Spalte beginnt nach der breiteren."""
    report = _Report(
        _Ctl("Tag", c.AC_DETAIL, 2.7, 0, 2.0), _Ctl("TeeTime", c.AC_DETAIL, 4.8, 0, 2.3), _Ctl("MID", c.AC_DETAIL, 5.0, 0, 1.8),
        _Ctl("Tag_Bezeichnungsfeld", c.AC_PAGE_HEADER, 2.7, 0, 2.0, kind=c.AC_LABEL),
        _Ctl("TeeTime_Bezeichnungsfeld", c.AC_PAGE_HEADER, 4.8, 0, 3.0, kind=c.AC_LABEL),  # breiter als das Feld
        _Ctl("MID_Bezeichnungsfeld", c.AC_PAGE_HEADER, 5.0, 0, 1.8, kind=c.AC_LABEL))
    before = {**_geometry(report), "mid": (c.AC_DETAIL, round(0.3 * CM), 0, round(1.8 * CM), round(0.55 * CM))}
    _reflow_overlapping_rows(report, before, {"mid", "mid_bezeichnungsfeld"})
    for name in ("Tag", "TeeTime", "MID"):
        assert _left(report, name) == _left(report, f"{name}_Bezeichnungsfeld"), name
    assert _left(report, "Tag") == 0.3, "Beginn am alten linken Rand"
    assert _left(report, "MID") >= _left(report, "TeeTime") + 3.0, "nach der breiteren Kopf-Beschriftung"


def test_rank_rule_needs_a_real_reflow() -> None:
    """Codex-Befund: gleiche Rangzahl allein reicht nicht – ohne Anschluss an den Nachbarn gilt die exakte Position."""
    from app.access.models import ControlInfo

    def info(name: str, left: float, width: float) -> ControlInfo:
        return ControlInfo(name=name, control_type=c.AC_TEXT_BOX, left=round(left * CM), width=round(width * CM),
                           height=300, parent="R")

    far = [info("Nachbar", 0.0, 2.0), info("Feld", 10.0, 2.0)]
    assert not _same_row_rank(far[1], round(20.0 * CM), far), "10 cm statt 20 cm, ohne Neuordnung → nicht bestanden"
    packed = [info("Nachbar", 0.0, 2.0), info("Feld", 2.1, 2.0)]
    assert _same_row_rank(packed[1], round(2.3 * CM), packed), "angeschlossen nach Neuordnung → bestanden"


def test_width_fit_detects_report_wider_than_the_page() -> None:
    values: dict[str, Any] = {"PaperSize": 9, "Orientation": 1, "LeftMargin": 360, "RightMargin": 360, "Width": 13000,
                              "ItemsAcross": 1, "DefaultSize": True, "ColumnSpacing": 0}
    too_wide = Verification(step_id=1)
    _check_width_fit(values, too_wide)
    assert not too_wide.passed, "13000 Twips > 21 cm − 2 × 0,635 cm"
    ok = Verification(step_id=1)
    _check_width_fit({**values, "Width": 9000}, ok)
    assert ok.passed

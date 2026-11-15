"""Access muss mit bereits generierten makepy-Wrappern funktionieren."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.access, pytest.mark.access_app]


def test_form_and_report_with_generated_access_cache(sample_db_path: Path, tmp_path: Path) -> None:
    # Eigenen Python-Prozess und Cache verwenden. Die Umgebung des Benutzers bleibt unberührt.
    script = """
import sys
from pathlib import Path

import pywintypes
import win32com

cache = sys.argv[2]
Path(cache).mkdir()
win32com.__gen_path__ = cache
win32com.gen_py.__path__ = [cache]

from win32com.client import gencache

access_lib = pywintypes.IID('{4AFFC9A0-5F99-101B-AF4E-00AA003F0F07}')
module = gencache.EnsureModule(access_lib, 0, 9, 0)
assert module is not None and Path(module.__file__).is_relative_to(cache)

from app.access import constants as c
from app.access.engine import AccessEngine
from app.access.specs import FormSpec, GroupSpec, ReportSpec, ReportTotalSpec

with AccessEngine.open(sys.argv[1], prefer_running=False) as engine:
    engine.objects.create_report(ReportSpec(
        name='rptUmsatz', record_source='Kunden', title='Umsatz je Bundesland',
        fields=['Bundesland', 'Nachname', 'Umsatz'], group_by=[GroupSpec(field='Bundesland')],
        totals=[ReportTotalSpec(field='Umsatz', function='sum', scope='both')],
    ))
    report = engine.objects.inspect_report('rptUmsatz')
    assert report.group_levels[0].expression == '[Bundesland]'
    assert any(control.caption == 'Umsatz je Bundesland' for control in report.controls)

    engine.objects.create_form(FormSpec(name='frmKunden', record_source='Kunden', fields=['Nachname'], title='Kunden'))
    form = engine.objects.inspect_form('frmKunden')
    assert any(control.caption == 'Kunden' for control in form.controls)
    app = engine.session.application()
    app.DoCmd.OpenForm('frmKunden', c.AC_DESIGN, '', '', -1, c.AC_HIDDEN)
    controls = app.Forms('frmKunden').Controls
    assert not type(controls[0]).__module__.startswith('win32com.gen_py')
    assert all(not type(control).__module__.startswith('win32com.gen_py') for control in controls)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(sample_db_path), str(tmp_path / "gen_py")],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

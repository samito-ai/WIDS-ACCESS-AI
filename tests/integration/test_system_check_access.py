"""„System & KI testen“, kostenloser Teil, mit echtem Access – nur auf einer temporären Testdatenbank."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.access


def test_local_system_check_passes_and_cleans_up() -> None:
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from app.core import system_check

    before = set(Path(tempfile.gettempdir()).glob("wids-systemtest-*"))
    # wie im Dashboard: in einem fremden Thread ohne COM-Initialisierung
    with ThreadPoolExecutor(1) as pool:
        results = pool.submit(system_check.run_local).result(timeout=300)
    assert [r.name for r in results if r.status != "ok"] == [], results
    assert system_check.summary(results)[0] == "bereit"
    assert set(Path(tempfile.gettempdir()).glob("wids-systemtest-*")) == before, "temporärer Ordner gelöscht"

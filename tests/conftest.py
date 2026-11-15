"""Gemeinsame Test-Fixtures.

Alle Tests arbeiten ausschließlich mit frisch erzeugten Testdatenbanken in temporären
Verzeichnissen. Das Datenverzeichnis der Anwendung wird per ``WIDS_DATA_DIR`` umgeleitet,
damit weder Einstellungen noch Verlauf des Benutzers berührt werden.
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _dao_available() -> bool:
    try:
        import win32com.client

        win32com.client.Dispatch("DAO.DBEngine.120")
        return True
    except Exception:
        return False


def _access_available() -> bool:
    try:
        from app.access.session import is_access_installed

        return is_access_installed()
    except Exception:
        return False


DAO_AVAILABLE = _dao_available()
ACCESS_AVAILABLE = DAO_AVAILABLE and _access_available()


class OfflineNetworkBlocked(RuntimeError):
    """Ein Offline-Test hat versucht, eine echte HTTP-Verbindung (z. B. zur KI-API) aufzubauen."""


def pytest_configure(config: pytest.Config) -> None:
    """Offline-Läufe verursachen nachweislich keine KI-Kosten: ohne ``WIDS_LIVE_AI=1`` ist der echte
    Netzwerktransport von httpx (den die Anthropic- und OpenAI-SDKs verwenden) gesperrt. Test-Transporte wie
    ``httpx.MockTransport`` bleiben nutzbar."""
    if os.environ.get("WIDS_LIVE_AI") == "1":
        return
    import importlib

    def blocked(self: object, request: object) -> object:
        raise OfflineNetworkBlocked(f"Netzwerkzugriff in Offline-Tests gesperrt: {request!r}")

    async def blocked_async(self: object, request: object) -> object:
        raise OfflineNetworkBlocked(f"Netzwerkzugriff in Offline-Tests gesperrt: {request!r}")

    # die aktuellen Anthropic-/OpenAI-SDKs verwenden „httpx2“ (gemessen), ältere „httpx“ – beide sperren
    for name in ("httpx", "httpx2"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        module.HTTPTransport.handle_request = blocked
        module.AsyncHTTPTransport.handle_async_request = blocked_async


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_dao = pytest.mark.skip(reason="DAO/ACE (Microsoft Access Database Engine) nicht installiert")
    skip_app = pytest.mark.skip(reason="Microsoft Access (Vollversion) nicht installiert")
    skip_live = pytest.mark.skip(reason="Live-KI-Tests nur mit WIDS_LIVE_AI=1")
    skip_tecomp = pytest.mark.skip(reason="kein laufendes te.comp mit geöffneter Access-Übung")
    tecomp_open = False
    if any("live_tecomp" in item.keywords for item in items):
        from app.vision import tecomp

        tecomp_open = tecomp.open_exercise() is not None
    for item in items:
        if "live_tecomp" in item.keywords and not tecomp_open:
            item.add_marker(skip_tecomp)
        if "access" in item.keywords and not DAO_AVAILABLE:
            item.add_marker(skip_dao)
        if "access_app" in item.keywords and not ACCESS_AVAILABLE:
            item.add_marker(skip_app)
        if "live_ai" in item.keywords and os.environ.get("WIDS_LIVE_AI") != "1":
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data = tmp_path / "wids-data"
    data.mkdir()
    monkeypatch.setenv("WIDS_DATA_DIR", str(data))
    yield data
    # COM-Objekte, die noch in Ausnahme-Tracebacks des Tests hängen, sofort im Test-Thread freigeben
    gc.collect()


@pytest.fixture(scope="session")
def _sample_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Erzeugt die Beispieldatenbank einmal pro Testlauf; Tests arbeiten auf Kopien."""
    from app.utils.sampledb import create_sample_database

    target = tmp_path_factory.mktemp("template") / "uebung_vorlage.accdb"
    return create_sample_database(target)


@pytest.fixture
def sample_db_path(_sample_template: Path, tmp_path: Path) -> Path:
    target = tmp_path / "uebung.accdb"
    shutil.copy2(_sample_template, target)
    return target


@pytest.fixture
def engine(sample_db_path: Path) -> Iterator[object]:
    from app.access.engine import AccessEngine

    eng = AccessEngine.open(sample_db_path, prefer_running=False)
    try:
        yield eng
    finally:
        eng.close()


@pytest.fixture
def empty_engine(tmp_path: Path) -> Iterator[object]:
    from app.access.engine import AccessEngine

    eng = AccessEngine.create(tmp_path / "leer.accdb")
    try:
        yield eng
    finally:
        eng.close()

"""Schwierige Fehlerfälle in echtem Access: beschädigte Datei, gesperrte Datei, Access-Verbindung bricht ab.

Erwartet wird jeweils: eine verständliche Meldung, keine hängende Instanz, keine Änderung ohne Sicherung, keine
fremde Access-Instanz beendet und nie ein falscher Erfolg.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil
import pytest

from app.access.engine import AccessEngine
from app.core.errors import BackupError, WidsError
from app.security.backup import BackupManager
from app.storage.database import Storage

pytestmark = [pytest.mark.access]


def _access_pids() -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "msaccess.exe"}


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Hält die Datei ohne Freigabe offen – wie ein anderes Programm, das sie exklusiv geöffnet hat."""
    import win32file

    handle = win32file.CreateFile(str(path), win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,
                                  win32file.OPEN_EXISTING, 0, None)
    try:
        yield
    finally:
        handle.Close()


def test_corrupt_database_is_rejected_cleanly(tmp_path: Path) -> None:
    broken = tmp_path / "kaputt.accdb"
    broken.write_bytes(b"Das ist keine Access-Datenbank. " * 200)
    before = _access_pids()
    with pytest.raises(WidsError) as info:
        AccessEngine.open(broken, prefer_running=False)
    assert info.value.user_message, "verständliche Meldung"
    assert _access_pids() <= before, "keine Access-Instanz bleibt hängen"


def test_locked_database_blocks_backup_and_open(sample_db_path: Path, tmp_path: Path) -> None:
    """Gesperrte Übungsdatei: keine Sicherung → keine Ausführung; Öffnen meldet die Sperre statt zu hängen."""
    backups = BackupManager(Storage(tmp_path / "wids.sqlite"), tmp_path / "b", tmp_path / "w")
    with _exclusive_lock(sample_db_path):
        with pytest.raises(BackupError):
            backups.create_backup(sample_db_path)
        with pytest.raises(WidsError) as info:
            AccessEngine.open(sample_db_path, prefer_running=False)
        assert info.value.user_message
    assert backups.list_backups() == [] and not list((tmp_path / "b").rglob("*.accdb"))
    engine = AccessEngine.open(sample_db_path, prefer_running=False)  # nach dem Freigeben wieder normal nutzbar
    try:
        assert engine.tables.count_records("Kunden") == 12
    finally:
        engine.close()


@pytest.mark.access_app
def test_access_connection_loss_is_reported_and_only_own_instance_touched(sample_db_path: Path) -> None:
    """Access verschwindet mitten im Lauf (abgestürzt/geschlossen): klare Meldung statt „AttributeError“, keine
    Endlosschleife – und andere Access-Instanzen (z. B. die von te.comp) bleiben unberührt."""
    from app.access.service import AccessService
    from app.access.specs import FormSpec
    from app.core.errors import AccessError

    service = AccessService()
    try:
        service.connect(str(sample_db_path), prefer_running=False)
        own = service.run(lambda e: (e.session.application(), e.session._owned_pid)[1])
        others = _access_pids() - {own}
        assert own and service.run(lambda e: e.session.kill_owned_instance())
        with pytest.raises(AccessError, match="Verbindung zu Access wurde getrennt"):
            service.run(lambda e: e.objects.create_form(FormSpec(name="frmNach", record_source="Kunden",
                                                                 fields=["Nachname"])))
        assert others <= _access_pids(), "fremde Access-Instanzen laufen weiter"
    finally:
        service.shutdown()


def test_connection_loss_during_pipeline_is_never_success(sample_db_path: Path, tmp_path: Path) -> None:
    """Ganzer Ablauf: Access bricht vor dem Formularschritt weg → Schritt scheitert ehrlich, Sicherung vorhanden,
    Verlauf nicht „läuft“, Status nicht erfolgreich."""
    from app.agent.orchestrator import AutoInteraction
    from app.tasks.models import CapturedTask
    from tests.helpers import build_harness, solution

    steps = [{"action": "create_form", "form": {"name": "frmK", "record_source": "Kunden", "fields": ["Nachname"]}}]
    h = build_harness(sample_db_path, [solution(steps, requirements=["Formular frmK"])], tmp_path)
    try:
        prepared = h.pipeline.prepare(CapturedTask(text="Erstellen Sie das Formular frmK."))

        def crash(e: Any) -> None:
            e.session.application()
            assert e.session.kill_owned_instance()

        h.access.run(crash)
        outcome = h.pipeline.execute(prepared, AutoInteraction())
        assert outcome.status in ("fehlgeschlagen", "teilweise"), (outcome.status, outcome.error)
        assert outcome.backup_path and Path(outcome.backup_path).is_file()
        assert h.storage.get_task(prepared.task_id).status == outcome.status
    finally:
        h.close()


@pytest.mark.access_app
def test_form_property_change_is_read_back_from_access(engine: AccessEngine) -> None:
    """Eine reine Eigenschaftsänderung (PopUp, Filter) hatte früher null Prüfpunkte und galt damit als bestanden.
    Jetzt wird jede angeforderte Eigenschaft aus dem gespeicherten Formular gelesen – auch wenn sie NICHT gesetzt wurde."""
    from app.access.specs import FormChangeSpec, FormSpec
    from app.agent.executor import StepResult
    from app.agent.verifier import ResultVerifier
    from app.tasks import models as m

    engine.objects.create_form(FormSpec(name="frmEig", record_source="Kunden", fields=["Nachname", "Ort"]))
    change = FormChangeSpec(properties={"PopUp": True, "Filter": '[Ort]="Imst"', "FilterOn": True})
    step = m.PlanStep(id=1, title="Eigenschaften", action=m.ModifyForm(name="frmEig", changes=change))

    def verify() -> Any:
        return ResultVerifier().verify(engine, step, StepResult(step_id=1, message=""))

    before = verify()
    assert not before.passed and {c.name for c in before.failed_checks} >= {"Formulareigenschaft PopUp"}
    engine.objects.modify_form("frmEig", change)
    after = verify()
    assert after.passed, [(c.name, c.detail) for c in after.checks]
    assert {c.name for c in after.checks} == {"Formulareigenschaft PopUp", "Formulareigenschaft Filter",
                                               "Formulareigenschaft FilterOn"}

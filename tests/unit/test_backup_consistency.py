"""Sicherung vor der Ausführung: nie eine Sicherung als gültig führen, die einen Zwischenstand enthalten kann."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import BackupError
from app.security.backup import BackupManager
from app.storage.database import Storage


def _manager(tmp_path: Path) -> BackupManager:
    return BackupManager(Storage(tmp_path / "wids.sqlite"), tmp_path / "backups", tmp_path / "work")


def _touch_during_copy(monkeypatch: pytest.MonkeyPatch, src: Path, times: int) -> list[int]:
    """Ändert die Quelldatei während der ersten ``times`` Kopiervorgänge (wie ein gleichzeitig schreibendes Access)."""
    real = shutil.copy2
    calls: list[int] = []

    def copy(a: Any, b: Any, *args: Any, **kwargs: Any) -> Any:
        result = real(a, b, *args, **kwargs)
        calls.append(1)
        if len(calls) <= times:
            src.write_bytes(src.read_bytes() + b"x")
            os.utime(src, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns + 10_000_000 * len(calls)))
        return result

    monkeypatch.setattr("app.security.backup.shutil.copy2", copy)
    return calls


def test_backup_retries_once_when_source_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "uebung.accdb"
    src.write_bytes(b"A" * 100)
    calls = _touch_during_copy(monkeypatch, src, times=1)
    info = _manager(tmp_path).create_backup(src)
    assert len(calls) == 2 and Path(info.backup_path).read_bytes() == src.read_bytes()


def test_backup_fails_when_source_keeps_changing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ändert sich die Datei auch während der Wiederholung, gibt es KEINE Sicherung (und damit keine Ausführung)."""
    src = tmp_path / "uebung.accdb"
    src.write_bytes(b"A" * 100)
    _touch_during_copy(monkeypatch, src, times=2)
    manager = _manager(tmp_path)
    with pytest.raises(BackupError, match="laufend verändert"):
        manager.create_backup(src)
    assert list((tmp_path / "backups").rglob("*.accdb")) == [], "keine halbe Sicherung liegen gelassen"
    assert manager.list_backups() == []

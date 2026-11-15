"""Sicherungskopien, Übungskopien und Wiederherstellung von Access-Dateien."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app.core import paths
from app.core.errors import BackupError, DatabaseNotFoundError
from app.core.logging_setup import get_logger
from app.storage.database import Storage

log = get_logger(__name__)

ACCESS_EXTENSIONS = {".accdb", ".mdb"}


class FileState(BaseModel):
    """Momentaufnahme des Dateizustands einer Datenbank."""

    path: str
    exists: bool
    size_bytes: int = 0
    modified: float = 0.0
    read_only: bool = False
    lock_file_present: bool = False
    writable_dir: bool = True
    extension_ok: bool = True

    @property
    def issues(self) -> list[str]:
        problems: list[str] = []
        if not self.exists:
            problems.append("Die Datei existiert nicht.")
            return problems
        if not self.extension_ok:
            problems.append("Die Datei ist keine Access-Datenbank (.accdb/.mdb).")
        if self.read_only:
            problems.append("Die Datei ist schreibgeschützt.")
        if not self.writable_dir:
            problems.append("Im Ordner der Datei kann nicht geschrieben werden.")
        return problems


class BackupInfo(BaseModel):
    id: int
    created_at: str
    db_path: str
    backup_path: str
    reason: str
    size_bytes: int
    sha256: str


def inspect_file(path: str | Path) -> FileState:
    p = Path(path)
    if not p.exists():
        return FileState(path=str(p), exists=False)
    st = p.stat()
    lock = p.with_suffix(".laccdb" if p.suffix.lower() == ".accdb" else ".ldb")
    return FileState(
        path=str(p),
        exists=True,
        size_bytes=st.st_size,
        modified=st.st_mtime,
        read_only=not bool(st.st_mode & stat.S_IWRITE),
        lock_file_present=lock.exists(),
        writable_dir=os.access(p.parent, os.W_OK),
        extension_ok=p.suffix.lower() in ACCESS_EXTENSIONS,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_stem(path: Path) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in path.stem)[:60]


class BackupManager:
    def __init__(self, storage: Storage, backup_root: Path | None = None, workcopy_root: Path | None = None) -> None:
        self._storage = storage
        self._root = backup_root or paths.backups_dir()
        self._work_root = workcopy_root or paths.workcopies_dir()
        self._root.mkdir(parents=True, exist_ok=True)
        self._work_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ Backups
    def create_backup(self, db_path: str | Path, reason: str = "", *, max_keep: int = 20) -> BackupInfo:
        src = Path(db_path)
        if not src.exists():
            raise DatabaseNotFoundError(f"Datei nicht gefunden: {src}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        target_dir = self._root / _safe_stem(src)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{_safe_stem(src)}_{stamp}{src.suffix}"
        before = src.stat()
        try:
            shutil.copy2(src, target)
            after = src.stat()
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                # Datei wurde während des Kopierens verändert – einmal wiederholen (rein lesend)
                shutil.copy2(src, target)
                again = src.stat()
                if (again.st_size, again.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    target.unlink(missing_ok=True)  # keine Sicherung, die womöglich einen Zwischenstand enthält
                    raise BackupError(f"„{src.name}“ wird gerade laufend verändert – keine verlässliche Sicherung "
                                      "möglich. Bitte kurz warten (Datensatz speichern) und erneut versuchen.")
        except OSError as exc:
            raise BackupError(f"Sicherung von „{src.name}“ fehlgeschlagen: {exc.strerror or exc}") from exc
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        checksum = _sha256(target)
        backup_id = self._storage.record_backup(
            db_path=str(src),
            backup_path=str(target),
            reason=reason,
            size_bytes=target.stat().st_size,
            sha256=checksum,
        )
        log.info("Backup erstellt: %s -> %s", src.name, target.name)
        self._prune(str(src), max_keep)
        row = next(b for b in self._storage.list_backups(str(src)) if b["id"] == backup_id)
        return BackupInfo(**row)

    def _prune(self, db_path: str, max_keep: int) -> None:
        if max_keep <= 0:
            return
        backups = self._storage.list_backups(db_path)
        for old in backups[max_keep:]:
            Path(old["backup_path"]).unlink(missing_ok=True)
            self._storage.delete_backup_record(int(old["id"]))

    def list_backups(self, db_path: str | Path | None = None) -> list[BackupInfo]:
        rows = self._storage.list_backups(str(db_path) if db_path else None)
        return [BackupInfo(**row) for row in rows]

    def verify_backup(self, backup: BackupInfo) -> bool:
        file = Path(backup.backup_path)
        return file.exists() and _sha256(file) == backup.sha256

    def restore(self, backup: BackupInfo, *, target: str | Path | None = None) -> BackupInfo | None:
        """Stellt ein Backup wieder her. Der aktuelle Stand wird zuvor selbst gesichert.

        Gibt das Sicherheits-Backup des überschriebenen Stands zurück (oder ``None``, wenn das
        Ziel nicht existierte).
        """
        source = Path(backup.backup_path)
        if not source.exists():
            raise BackupError("Die Sicherungsdatei existiert nicht mehr.")
        if _sha256(source) != backup.sha256:
            raise BackupError("Die Sicherungsdatei ist beschädigt (Prüfsumme stimmt nicht).")
        dest = Path(target) if target else Path(backup.db_path)
        safety: BackupInfo | None = None
        if dest.exists():
            safety = self.create_backup(dest, reason="Automatisch vor Wiederherstellung", max_keep=0)
        tmp = dest.with_name(dest.name + ".wids-restore.tmp")
        try:
            shutil.copy2(source, tmp)
            os.replace(tmp, dest)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise BackupError(
                "Wiederherstellung fehlgeschlagen – ist die Datenbank noch in Access geöffnet? "
                f"({exc.strerror or exc})"
            ) from exc
        log.info("Backup wiederhergestellt: %s -> %s", source.name, dest)
        return safety

    # ------------------------------------------------------------------ Übungskopien
    def create_working_copy(self, db_path: str | Path) -> Path:
        """Erstellt eine unabhängige Übungskopie; das Original bleibt unverändert."""
        src = Path(db_path)
        if not src.exists():
            raise DatabaseNotFoundError(f"Datei nicht gefunden: {src}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self._work_root / f"{_safe_stem(src)}_Uebungskopie_{stamp}{src.suffix}"
        counter = 1
        while target.exists():  # Schutz vor versehentlichem Überschreiben
            counter += 1
            target = self._work_root / f"{_safe_stem(src)}_Uebungskopie_{stamp}_{counter}{src.suffix}"
        try:
            shutil.copy2(src, target)
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        except OSError as exc:
            raise BackupError(f"Übungskopie konnte nicht erstellt werden: {exc.strerror or exc}") from exc
        log.info("Übungskopie erstellt: %s", target)
        return target

    def is_working_copy(self, db_path: str | Path) -> bool:
        try:
            return Path(db_path).resolve().is_relative_to(self._work_root.resolve())
        except OSError:
            return False

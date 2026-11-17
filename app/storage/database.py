"""Lokale SQLite-Datenhaltung: Einstellungen, Aufgabenverlauf, Tokenverbrauch, Backups."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core import paths
from app.core.logging_setup import get_logger
from app.core.settings import AppSettings

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    source        TEXT NOT NULL DEFAULT '',
    capture_method TEXT NOT NULL DEFAULT '',
    raw_text      TEXT NOT NULL DEFAULT '',
    task_type     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    db_path       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'erkannt',
    analysis_json TEXT,
    plan_json     TEXT,
    result_json   TEXT,
    report_md     TEXT,
    error         TEXT,
    backup_path   TEXT,
    duration_s    REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE TABLE IF NOT EXISTS task_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL,
    data     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    purpose       TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL,
    task_id       INTEGER
);
CREATE TABLE IF NOT EXISTS backups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    db_path     TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    sha256      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


class TaskRecord(BaseModel):
    id: int
    created_at: str
    finished_at: str | None = None
    source: str = ""
    capture_method: str = ""
    raw_text: str = ""
    task_type: str = ""
    title: str = ""
    db_path: str = ""
    status: str = "erkannt"
    analysis_json: str | None = None
    plan_json: str | None = None
    result_json: str | None = None
    report_md: str | None = None
    error: str | None = None
    backup_path: str | None = None
    duration_s: float | None = None


class UsageSummary(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_requests: int = 0
    #: verwendete Modelle (kommagetrennt)
    models: str = ""


class Storage:
    """Thread-sichere Fassade über eine SQLite-Datei."""

    def __init__(self, db_file: Path | None = None) -> None:
        self._path = db_file or paths.database_file()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._tx() as cur:
            cur.execute("PRAGMA foreign_keys = ON")
            cur.executescript(_SCHEMA)
            # Verlauf älterer Versionen: Kosten je Aufgabe nachrüsten (bestehende Einträge bleiben ohne Aufgabe)
            if "task_id" not in {row[1] for row in cur.execute("PRAGMA table_info(usage)")}:
                cur.execute("ALTER TABLE usage ADD COLUMN task_id INTEGER")

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ Einstellungen
    def load_settings(self) -> AppSettings:
        with self._tx() as cur:
            row = cur.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
        if row is None:
            return AppSettings()
        try:
            return AppSettings.model_validate_json(row["value"])
        except ValidationError as exc:
            log.warning("Gespeicherte Einstellungen ungültig, verwende Standardwerte: %s", exc)
            try:
                # Teilweise Übernahme: gültige Abschnitte behalten
                raw = json.loads(row["value"])
                merged = AppSettings().model_dump()
                for section, values in raw.items():
                    if section in merged and isinstance(values, dict):
                        candidate = {**merged, section: {**merged[section], **values}}
                        try:
                            AppSettings.model_validate(candidate)
                            merged = candidate
                        except ValidationError:
                            continue
                return AppSettings.model_validate(merged)
            except Exception:
                return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO settings(key, value) VALUES('app', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (settings.model_dump_json(),),
            )


    # ------------------------------------------------------------------ Aufgabenverlauf
    def create_task(
        self, *, raw_text: str, source: str = "", capture_method: str = "", db_path: str = ""
    ) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO tasks(created_at, source, capture_method, raw_text, db_path) VALUES(?,?,?,?,?)",
                (now_iso(), source, capture_method, raw_text, db_path),
            )
            return int(cur.lastrowid or 0)

    def update_task(self, task_id: int, **fields: Any) -> None:
        allowed = set(TaskRecord.model_fields) - {"id", "created_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unbekannte Felder: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._tx() as cur:
            cur.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id))

    def step_done_before(self, db_path: str, fingerprint: str, exclude_task: int) -> int | None:
        """Aufgabe, in der genau dieser Schritt an genau dieser Datenbank schon erfolgreich lief (oder ``None``)."""
        with self._tx() as cur:
            row = cur.execute(
                "SELECT e.task_id FROM task_events e JOIN tasks t ON t.id = e.task_id WHERE t.db_path = ? "
                "COLLATE NOCASE AND e.level = 'step_done' AND e.data LIKE ? AND e.task_id <> ? ORDER BY e.id DESC LIMIT 1",
                (db_path, f'%"{fingerprint}"%', exclude_task)).fetchone()
        return int(row["task_id"]) if row else None

    def add_task_event(self, task_id: int, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO task_events(task_id, ts, level, message, data) VALUES(?,?,?,?,?)",
                (
                    task_id,
                    now_iso(),
                    level,
                    message,
                    json.dumps(data, ensure_ascii=False, default=str) if data else None,
                ),
            )

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return TaskRecord(**dict(row)) if row else None

    def get_task_events(self, task_id: int) -> list[dict[str, Any]]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT ts, level, message, data FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_interrupted_tasks(self) -> list[TaskRecord]:
        """Aufgaben, die beim letzten Beenden noch liefen (Absturz, Abschalten, Prozess beendet), als „abgebrochen“
        kennzeichnen – mit Hinweis auf die Sicherung. Nur von der einzigen laufenden Instanz aufrufen."""
        interrupted = self.list_tasks(status="läuft", limit=1000)
        for task in interrupted:
            hint = (f" Die Sicherung von vor der Ausführung liegt unter „{task.backup_path}“ und kann im Verlauf "
                    "wiederhergestellt werden." if task.backup_path else "")
            self.update_task(task.id, status="abgebrochen", finished_at=now_iso(),
                             error="WIDS wurde während der Ausführung beendet (Absturz oder Abschalten). Bereits "
                                   "ausgeführte Schritte können in der Datenbank sein – bitte prüfen." + hint)
        return interrupted

    def list_tasks(self, *, search: str = "", limit: int = 200, status: str = "") -> list[TaskRecord]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if search:
            sql += " AND (raw_text LIKE ? OR title LIKE ? OR task_type LIKE ? OR db_path LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like, like]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._tx() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [TaskRecord(**dict(r)) for r in rows]

    def delete_task(self, task_id: int) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def task_type_statistics(self) -> list[tuple[str, int, int]]:
        """Liefert (Aufgabentyp, Anzahl, davon erfolgreich) – erkennt wiederkehrende Aufgabentypen."""
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT task_type, COUNT(*) AS n, "
                "SUM(CASE WHEN status = 'erfolgreich' THEN 1 ELSE 0 END) AS ok "
                "FROM tasks WHERE task_type <> '' GROUP BY task_type ORDER BY n DESC"
            ).fetchall()
        return [(r["task_type"], int(r["n"]), int(r["ok"] or 0)) for r in rows]


    # ------------------------------------------------------------------ Tokenverbrauch
    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        task_id: int | None = None,
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO usage(ts, provider, model, purpose, input_tokens, output_tokens, cost_usd, task_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (now_iso(), provider, model, purpose, input_tokens, output_tokens, cost_usd, task_id),
            )

    def usage_summary(self, period: str = "total", *, since: str | None = None,
                      task_id: int | None = None) -> UsageSummary:
        """Verbrauch je Zeitraum (``day``/``week``/``month``/``total``), seit einem Zeitpunkt oder für EINE Aufgabe."""
        sql = (
            "SELECT COUNT(*) AS n, COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
            "COALESCE(SUM(cost_usd),0) AS c, SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS u, "
            "GROUP_CONCAT(DISTINCT model) AS models FROM usage"
        )
        params: list[Any] = []
        now = datetime.now(tz=UTC)
        if task_id is not None:
            sql += " WHERE task_id = ?"
            params.append(task_id)
        elif since is not None:
            sql += " WHERE ts >= ?"
            params.append(since)
        elif period == "day":
            sql += " WHERE ts >= ?"
            params.append(now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"))
        elif period == "month":
            sql += " WHERE ts >= ?"
            params.append(
                now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
            )
        elif period == "week":
            sql += " WHERE ts >= ?"
            params.append((now - timedelta(days=7)).isoformat(timespec="seconds"))
        with self._tx() as cur:
            row = cur.execute(sql, params).fetchone()
        return UsageSummary(
            requests=int(row["n"]),
            input_tokens=int(row["i"]),
            output_tokens=int(row["o"]),
            cost_usd=float(row["c"]),
            unpriced_requests=int(row["u"] or 0),
            models=str(row["models"] or ""),
        )

    def last_task_with_usage(self) -> int | None:
        with self._tx() as cur:
            row = cur.execute("SELECT MAX(task_id) AS t FROM usage WHERE task_id IS NOT NULL").fetchone()
        return int(row["t"]) if row and row["t"] is not None else None


    # ------------------------------------------------------------------ Backups
    def record_backup(self, *, db_path: str, backup_path: str, reason: str, size_bytes: int, sha256: str) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO backups(created_at, db_path, backup_path, reason, size_bytes, sha256) "
                "VALUES(?,?,?,?,?,?)",
                (now_iso(), _norm(db_path), backup_path, reason, size_bytes, sha256),
            )
            return int(cur.lastrowid or 0)

    def list_backups(self, db_path: str | None = None) -> list[dict[str, Any]]:
        with self._tx() as cur:
            if db_path:
                rows = cur.execute(
                    "SELECT * FROM backups WHERE db_path = ? ORDER BY id DESC", (_norm(db_path),)
                ).fetchall()
            else:
                rows = cur.execute("SELECT * FROM backups ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_backup_record(self, backup_id: int) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM backups WHERE id = ?", (backup_id,))

    # ------------------------------------------------------------------ Chat
    def add_chat_message(self, role: str, content: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO chat_messages(ts, role, content) VALUES(?,?,?)", (now_iso(), role, content)
            )

    def list_chat_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT * FROM (SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_chat(self) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM chat_messages")


def _norm(path: str) -> str:
    return str(Path(path).resolve()).lower() if path else ""

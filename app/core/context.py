"""Anwendungskontext: erzeugt und verbindet alle Dienste (ohne Oberfläche)."""
from __future__ import annotations

from pathlib import Path

from app.access import importer
from app.access.service import AccessService, ConnectionStatus
from app.agent.analyzer import TaskAnalyzer
from app.agent.orchestrator import TaskPipeline
from app.agent.task_reader import TaskReader
from app.ai import prompts
from app.ai.base import ChatMessage, ImageInput
from app.ai.manager import ProviderManager
from app.core import logging_setup
from app.core.errors import WidsError
from app.core.settings import AppSettings
from app.security.backup import BackupManager
from app.security.policy import SafetyPolicy
from app.security.secrets import SecretStore
from app.storage.database import Storage
from app.utils import sampledb
from app.vision import capture


class AppContext:
    def __init__(self, storage: Storage | None = None, secrets: SecretStore | None = None) -> None:
        self.storage = storage or Storage()
        self.settings: AppSettings = self.storage.load_settings()
        importer.set_user_folders(self.settings.automation.source_folders)
        self.log_file = logging_setup.configure_logging(self.settings.logging.level)
        self.secrets = secrets or SecretStore()
        self.access = AccessService(operation_timeout_s=self.settings.access.operation_timeout_s)
        self.ai = ProviderManager(self.settings, self.secrets, self.storage)
        self.backups = BackupManager(self.storage)
        self.policy = SafetyPolicy(self.settings.security)
        self.reader = TaskReader(self.settings, self.ai)
        self.pipeline = TaskPipeline(
            settings=self.settings, storage=self.storage, access=self.access, analyzer=TaskAnalyzer(self.ai),
            backups=self.backups, policy=self.policy, environment_probe=capture.environment,
        )

    def save_settings(self) -> None:
        self.storage.save_settings(self.settings)
        importer.set_user_folders(self.settings.automation.source_folders)
        self.access.operation_timeout_s = self.settings.access.operation_timeout_s
        logging_setup.set_level(self.settings.logging.level)
        self.ai.reset()

    # ------------------------------------------------------------------ Datenbank
    def open_database(self, path: str, *, work_on_copy: bool) -> ConnectionStatus:
        """Öffnet eine Datei – auf Wunsch (Standard) als unabhängige Übungskopie."""
        target = str(self.backups.create_working_copy(path)) if work_on_copy else path
        return self.access.connect(target, prefer_running=self.settings.access.prefer_running_instance)

    def create_sample_database(self) -> ConnectionStatus:
        from app.core import paths

        target = paths.workcopies_dir() / "WIDS_Beispiel.accdb"
        counter = 1
        while target.exists():
            counter += 1
            target = paths.workcopies_dir() / f"WIDS_Beispiel_{counter}.accdb"
        return self.access.create_database(str(target), sampledb.populate)

    def restore_backup(self, backup_path: str) -> None:
        """Stellt eine Sicherung wieder her und verbindet die Datenbank danach erneut."""
        backup = next((b for b in self.backups.list_backups() if b.backup_path == backup_path), None)
        if backup is None or not Path(backup_path).exists():
            raise WidsError("Die Sicherungsdatei wurde nicht gefunden.")
        reconnect = self.access.is_connected and self.access.path.lower() == str(Path(backup.db_path)).lower()
        if reconnect:
            self.access.disconnect()
        try:
            self.backups.restore(backup)
        finally:
            if reconnect:
                self.access.connect(backup.db_path, prefer_running=self.settings.access.prefer_running_instance)

    # ------------------------------------------------------------------ Tutor
    def ask(self, question: str, history: list[ChatMessage], image_png: bytes | None = None) -> str:
        """Beantwortet eine Frage; die Struktur der verbundenen Datenbank wird als Kontext mitgegeben."""
        schema_text = None
        if self.access.is_connected:
            try:
                schema_text = self.access.schema().to_prompt_text()
            except WidsError:
                schema_text = None
        images = [ImageInput(data=image_png)] if image_png else []
        messages = [*history[-12:], ChatMessage(role="user", content=question, images=images)]
        answer = self.ai.complete(system=prompts.tutor_context(schema_text), messages=messages, purpose="frage").text
        self.storage.add_chat_message("user", question)
        self.storage.add_chat_message("assistant", answer)
        return answer

    def shutdown(self) -> None:
        self.access.shutdown()
        self.storage.close()

"""Typisierte Anwendungseinstellungen (Pydantic), persistiert in SQLite."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ConflictPolicy = Literal["ask", "overwrite", "rename", "skip"]


class ModelPrice(BaseModel):
    """Preis in US-Dollar pro 1 Mio. Tokens."""

    input: float = 0.0
    output: float = 0.0


class AISettings(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-5"
    #: Günstigeres Modell für einfache Schritte (Klassifikation, kurze Fragen); leer = Hauptmodell
    anthropic_fast_model: str = "claude-haiku-5-5"
    openai_fast_model: str = ""
    auto_model_selection: bool = False
    request_timeout_s: float = 300.0
    max_retries: int = 3
    max_output_tokens: int = 16000
    use_vision: bool = True
    cost_limit_usd: float = 0.0
    cost_limit_period: Literal["day", "month", "total"] = "month"
    #: Preise je Modell; unbekannte Modelle werden ohne Kosten ausgewiesen
    prices: dict[str, ModelPrice] = Field(
        default_factory=lambda: {
            "claude-fable-5-2": ModelPrice(input=8.0, output=40.0),
            "claude-opus-5-5": ModelPrice(input=4.0, output=20.0),
            "claude-sonnet-5-5": ModelPrice(input=2.0, output=10.0),
            "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50),
        }
    )


class HotkeySettings(BaseModel):
    enabled: bool = True
    solve: str = "Alt+W"
    analyze: str = "Alt+T"
    ask: str = "Alt+Q"
    status: str = "Alt+S"
    settings: str = "Ctrl+Alt+S"
    dashboard: str = "Ctrl+Alt+D"
    cancel: str = "Ctrl+Alt+X"

    @field_validator("cancel")
    @classmethod
    def _migrate_cancel(cls, value: str) -> str:
        # frühere Voreinstellung: Esc nur während einer Automatisierung – blockierte Esc in allen Programmen
        return "Ctrl+Alt+X" if value.strip().lower() in ("esc", "escape") else value


class AccessSettings(BaseModel):
    #: Bevorzugt die bereits laufende Access-Instanz (te.comp-Arbeitsablauf)
    prefer_running_instance: bool = True
    operation_timeout_s: float = 60.0
    #: Beim manuellen Öffnen einer Datei standardmäßig in einer Übungskopie arbeiten
    work_on_copy_default: bool = True
    lock_retry_count: int = 3
    lock_retry_delay_s: float = 1.0


class CaptureSettings(BaseModel):
    ocr_language: str = "de"
    min_text_length: int = 25


class AutomationSettings(BaseModel):
    conflict_policy: ConflictPolicy = "ask"
    verify_after_each_step: bool = True
    max_recovery_attempts: int = 2
    #: Nach der Ausführung jede Anforderung von der KI gegen den Datenbankzustand prüfen lassen (1 Anfrage mehr)
    check_requirements: bool = True
    #: Diagnose im Abschlussbericht: Anforderung → Schritte → Schrittprüfung → Anforderungsprüfung, Korrekturzyklen
    diagnostic_report: bool = True


class ProgramApproval(BaseModel):
    """24-Stunden-Freigabe eines te.comp-Lernprogramms (siehe ``app.security.programs``). Abgelaufene und widerrufene
    Einträge bleiben zur Nachvollziehbarkeit gespeichert."""

    program_id: str  # z. B. "Kap.02"
    caption: str  # z. B. "DA - Tabellen"
    approved_at: str  # ISO-Zeitpunkt (UTC)
    expires_at: str  # ISO-Zeitpunkt (UTC)
    revoked: bool = False

    def active(self, now: datetime | None = None) -> bool:
        return not self.revoked and datetime.fromisoformat(self.expires_at) > (now or datetime.now(tz=UTC))


class SecuritySettings(BaseModel):
    always_backup: bool = True
    max_backups_per_db: int = 20
    #: Aktionsabfragen mit mehr als so vielen betroffenen Datensätzen brauchen eine Extra-Bestätigung
    mass_change_threshold: int = 25
    #: Fenstertitel-/Text-Schlüsselwörter, bei denen die Automatisierung gesperrt wird
    exam_keywords: list[str] = Field(
        default_factory=lambda: [
            "prüfung",
            "pruefung",
            "prüfungsmodus",
            "test-modus",
            "testmodus",
            "lernzielkontrolle",
            "zertifizierung",
            "exam",
            "safe exam browser",
        ]
    )
    block_in_exam_mode: bool = True
    #: Ausdrücklich freigegebene eigene Übungsdatenbanken: nicht löschende Pläne ohne erneute Freigabe
    trusted_databases: list[str] = Field(default_factory=list)
    #: 24-Stunden-Freigaben je te.comp-Lernprogramm (gilt für alle eindeutig zugehörigen Übungsdateien)
    program_approvals: list[ProgramApproval] = Field(default_factory=list)
    #: vom Benutzer im Dashboard bestätigte Zuordnung Datei (voller Pfad) → Lernprogramm/Workshop
    program_assignments: dict[str, str] = Field(default_factory=dict)


class UISettings(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    first_run_done: bool = False
    # window = Hauptfenster, compact = kleines Statusfenster, background = nur Infobereich (wie der Autostart)
    start_mode: Literal["window", "compact", "background"] = "window"
    #: kleine Statusanzeige oben links (Working…/Done/Review/Error)
    show_overlay: bool = True
    window_width: int = 1280
    window_height: int = 820


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class AppSettings(BaseModel):
    ai: AISettings = Field(default_factory=AISettings)
    hotkeys: HotkeySettings = Field(default_factory=HotkeySettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ui: UISettings = Field(default_factory=UISettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    def active_model(self) -> str:
        return self.ai.anthropic_model if self.ai.provider == "anthropic" else self.ai.openai_model

    def fast_model(self) -> str:
        fast = self.ai.anthropic_fast_model if self.ai.provider == "anthropic" else self.ai.openai_fast_model
        return fast or self.active_model()

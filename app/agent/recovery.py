"""Recovery Manager: entscheidet nach einem Fehler, welche Wiederherstellungsstrategie zulässig ist.

Grundsatz: Automatisch wiederholt wird nur, wenn sicher ist, dass der fehlgeschlagene Versuch
nichts verändert hat. Ist der Zustand unklar (z. B. Zeitüberschreitung), wird abgebrochen –
eine potenziell destruktive Operation wird nie „auf Verdacht“ ein zweites Mal ausgeführt.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.core.errors import (
    AccessBusyError,
    AccessTimeoutError,
    ApprovalRequiredError,
    DatabaseLockedError,
    ObjectExistsError,
    SqlSyntaxError,
    TaskCancelledError,
    WidsError,
)
from app.tasks import models as m

RecoveryKind = Literal["retry", "repair_sql", "resolve_conflict", "confirm_mass", "abort"]


class RecoveryDecision(BaseModel):
    kind: RecoveryKind
    reason: str = ""
    wait_s: float = 0.0


class RecoveryState(BaseModel):
    """Zähler je Schritt, damit keine Strategie endlos wiederholt wird."""

    lock_retries: int = 0
    sql_repairs: int = 0
    conflict_resolved: bool = False
    mass_confirmed: bool = False


class RecoveryManager:
    def __init__(self, *, max_sql_repairs: int = 2, max_lock_retries: int = 3, lock_delay_s: float = 1.0) -> None:
        self.max_sql_repairs = max_sql_repairs
        self.max_lock_retries = max_lock_retries
        self.lock_delay_s = lock_delay_s

    def decide(
        self, error: WidsError, step: m.PlanStep, state: RecoveryState, *, ai_available: bool
    ) -> RecoveryDecision:
        if isinstance(error, TaskCancelledError):
            return RecoveryDecision(kind="abort", reason="Abbruch durch den Benutzer.")
        if isinstance(error, AccessTimeoutError):
            return RecoveryDecision(
                kind="abort",
                reason="Zeitüberschreitung – es ist unklar, ob die Operation ausgeführt wurde. Keine Wiederholung.",
            )
        if isinstance(error, ObjectExistsError):
            if state.conflict_resolved:
                return RecoveryDecision(kind="abort", reason="Der Namenskonflikt besteht nach der Auflösung weiter.")
            return RecoveryDecision(kind="resolve_conflict", reason=error.user_message)
        if isinstance(error, ApprovalRequiredError):
            if state.mass_confirmed:
                return RecoveryDecision(kind="abort", reason=error.user_message)
            # Die Engine hat die Transaktion zurückgerollt – es wurde nichts verändert.
            return RecoveryDecision(kind="confirm_mass", reason=error.user_message)
        if isinstance(error, AccessBusyError | DatabaseLockedError):
            # Access hat den Aufruf abgewiesen bzw. die Sperre verhinderte die Änderung.
            if state.lock_retries < self.max_lock_retries:
                wait = self.lock_delay_s * (state.lock_retries + 1)
                return RecoveryDecision(kind="retry", reason=error.user_message, wait_s=wait)
            return RecoveryDecision(kind="abort", reason=error.user_message)
        if isinstance(error, SqlSyntaxError) and self._sql_repairable(step):
            if ai_available and state.sql_repairs < self.max_sql_repairs:
                return RecoveryDecision(kind="repair_sql", reason=error.user_message)
            return RecoveryDecision(kind="abort", reason=error.user_message)
        return RecoveryDecision(kind="abort", reason=error.user_message)

    @staticmethod
    def _sql_repairable(step: m.PlanStep) -> bool:
        """SQL wird nur dort automatisch korrigiert, wo ein Syntaxfehler garantiert nichts verändert hat."""
        return isinstance(step.action, m.CreateQuery)

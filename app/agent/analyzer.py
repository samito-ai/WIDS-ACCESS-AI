"""Task Analyzer: lässt die KI eine Aufgabenstellung in Analyse + validierten Lösungsplan übersetzen."""
from __future__ import annotations

import hashlib
import threading
import time

from pydantic import BaseModel

from app.access.models import DatabaseSchema
from app.agent.planner import PlanValidator
from app.ai import prompts
from app.ai.base import ChatMessage, ImageInput
from app.ai.manager import ProviderManager
from app.core.errors import CaptureError, ResponseFormatError
from app.core.logging_setup import get_logger
from app.tasks.classifier import Classification, classify_task
from app.tasks.models import (
    AgentSolution,
    CapturedTask,
    ExecutionPlan,
    PlanStep,
    RequirementReport,
    RequirementResult,
)

log = get_logger(__name__)


class SqlRepair(BaseModel):
    sql: str
    reason: str = ""


#: Eine Analyse desselben unveränderten Inhalts (Aufgabe, Abbildungen, Datenbankstruktur, Kontext, Modell) wird so
#: lange wiederverwendet – z. B. Alt+W nach einer Sperre, Planfreigabe im Dashboard nach einem Hintergrund-Halt
ANALYSIS_CACHE_SECONDS = 30 * 60
_ANALYSIS_CACHE_SIZE = 8


class TaskAnalyzer:
    def __init__(self, ai: ProviderManager, validator: PlanValidator | None = None) -> None:
        self._ai = ai
        self._validator = validator or PlanValidator()
        self._cache: dict[str, tuple[float, AgentSolution]] = {}
        self._cache_lock = threading.Lock()

    def clear_cache(self) -> None:
        """Nach einem nicht erfolgreichen Lauf: dieselbe Aufgabe soll neu (evtl. anders) geplant werden."""
        with self._cache_lock:
            self._cache.clear()

    def _cache_key(self, message: ChatMessage, schema: DatabaseSchema | None) -> str:
        digest = hashlib.sha256()
        # vollständige Struktur (der Prompt kürzt lange Objektlisten) – jede Änderung der Datenbank = neue Analyse
        full_schema = schema.model_dump_json() if schema is not None else ""
        for part in (self._ai.settings.ai.provider, self._ai.model_for("analyse"), prompts.ANALYSIS_SYSTEM,
                     message.content, full_schema):
            digest.update(str(part).encode("utf-8", "replace") + b"\0")
        for image in message.images:
            digest.update(hashlib.sha256(image.data).digest())
        return digest.hexdigest()

    @property
    def available(self) -> bool:
        """Ist für den aktiven Anbieter ein Schlüssel hinterlegt?"""
        try:
            return self._ai.is_configured()
        except Exception:
            return False

    @staticmethod
    def classify(text: str) -> Classification:
        """Schnelle, lokale Klassifikation ohne KI."""
        return classify_task(text)

    def analyze(
        self,
        task: CapturedTask,
        schema: DatabaseSchema | None,
        *,
        cancel: threading.Event | None = None,
        extra_context: str = "",
    ) -> AgentSolution:
        """Analysiert die Aufgabe und liefert Analyse und Plan.

        Schemaverstöße und fachliche Planfehler (unbekannte Tabellen/Felder, ungültiges SQL)
        werden dem Modell zur Korrektur zurückgegeben. Namenskonflikte mit vorhandenen Objekten
        gelten nicht als Fehler – über sie entscheidet der Benutzer bzw. die Konfliktrichtlinie.
        """
        text = task.text.strip()
        if len(text) < 5:
            raise CaptureError("Die Aufgabenstellung ist leer oder zu kurz.")
        schema_text = schema.to_prompt_text() if schema is not None else None
        # Bildschirmfotos werden nie angehängt – nur Abbildungen der te.comp-Aufgabenseite (Übungsmaterial)
        message = ChatMessage(
            role="user",
            content=prompts.analysis_user_prompt(text, schema_text, extra_context, images=len(task.images)),
            images=[ImageInput(data=png) for png in task.images],
        )

        def check_plan(solution: AgentSolution) -> None:
            report = self._validator.validate(solution.plan, schema)
            if report.errors:
                raise ValueError(
                    "Der Plan passt nicht zur Datenbank:\n"
                    + report.error_text()
                    + "\nKorrigiere die betroffenen Schritte. Wenn die Aufgabe so nicht lösbar ist, entferne den "
                    "Schritt und beschreibe das Problem in analysis.open_questions."
                )
            missing = solution.uncovered_requirements()
            if missing:
                requirements = solution.analysis.requirements
                raise ValueError(
                    "Diese Anforderungen deckt kein Schritt ab: "
                    + "; ".join(f"{n}. {requirements[n - 1]}" for n in missing)
                    + ". Ergänze die fehlenden Schritte (Feld covers). Braucht eine Anforderung keine Änderung an der "
                    "Datenbank, trage ihre Nummer in analysis.manual_requirements ein."
                )

        key = self._cache_key(message, schema)
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
        if hit is not None and now - hit[0] < ANALYSIS_CACHE_SECONDS:
            log.info("Analyse unveränderten Inhalts wiederverwendet (keine KI-Anfrage)")
            return hit[1].model_copy(deep=True)
        try:
            solution = self._ai.complete_json(
                AgentSolution,
                messages=[message],
                system=prompts.ANALYSIS_SYSTEM,
                purpose="analyse",
                max_repairs=max(0, self._ai.settings.automation.max_recovery_attempts),
                cancel=cancel,
                post_validate=check_plan,
            )
        except ResponseFormatError:
            log.warning("Analyse lieferte auch nach Korrekturen keinen gültigen Plan")
            raise
        with self._cache_lock:
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < ANALYSIS_CACHE_SECONDS}
            while len(self._cache) >= _ANALYSIS_CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (now, solution.model_copy(deep=True))
        return solution

    def repair_sql(
        self, *, schema: DatabaseSchema, requirement: str, sql: str, error: str, cancel: threading.Event | None = None
    ) -> SqlRepair:
        """Bittet die KI um eine Korrektur einer von Access abgelehnten Abfrage."""
        return self._ai.complete_json(
            SqlRepair,
            messages=[
                ChatMessage(
                    role="user",
                    content=prompts.sql_repair_prompt(schema.to_prompt_text(), requirement, sql, error),
                )
            ],
            system=prompts.SQL_REPAIR_SYSTEM,
            purpose="reparatur",
            max_repairs=1,
            cancel=cancel,
        )

    def check_requirements(
        self,
        *,
        task_text: str,
        requirements: list[str],
        schema_after: DatabaseSchema,
        changes: list[str],
        cancel: threading.Event | None = None,
        verified: list[str] | None = None,
        schema_before: DatabaseSchema | None = None,
        images: list[bytes] | None = None,
        sources: str = "",
    ) -> RequirementReport:
        """Lässt jede Anforderung gegen den tatsächlichen Datenbankzustand prüfen (unabhängig vom Plan).
        ``verified``: vom Verifier in der Datenbank bestätigte Datenschritte (Belege für Datenanforderungen)."""

        content = prompts.requirements_prompt(
            task_text, requirements, schema_after.to_prompt_text(max_queries=80), changes, verified or [])
        if schema_before is not None:
            content += "\n\nFORMULARE UND BERICHTE VOR DER BEARBEITUNG:\n" + _object_prompt(schema_before)
        if sources:  # z. B. „Feldnamen aus Excel übernehmen“ ist nur mit den Spaltenüberschriften der Datei prüfbar
            content += "\n\n" + sources
        if images:
            content += "\n\nDie angehängten te.comp-Abbildungen zeigen das erwartete Ergebnis der Aufgabenstellung."
        report = self._ai.complete_json(
            RequirementReport,
            messages=[ChatMessage(role="user", content=content, images=[ImageInput(data=png) for png in images or []])],
            system=prompts.REQUIREMENTS_SYSTEM,
            purpose="analyse",
            max_repairs=1,
            cancel=cancel,
        )
        # Jede Nummer muss genau einmal belegt sein. Die Texte im Ergebnis stammen aus der ursprünglichen Analyse.
        by_number: dict[int, list[RequirementResult]] = {}
        for item in report.results:
            by_number.setdefault(item.number, []).append(item)
        results = []
        for number, requirement in enumerate(requirements, start=1):
            found = by_number.get(number, [])
            if len(found) == 1 and found[0].evidence.strip():
                results.append(found[0].model_copy(update={"requirement": requirement}))
            else:
                reason = ("Mehrere widersprüchliche oder doppelte Prüfergebnisse." if len(found) > 1 else
                          "Die Anforderungsprüfung hat keinen Beleg geliefert.")
                results.append(RequirementResult(number=number, requirement=requirement, evidence=reason))
        return RequirementReport(results=results)

    def repair_step(
        self, *, task_text: str, step: PlanStep, error: str, schema: DatabaseSchema, cancel: threading.Event | None = None
    ) -> ExecutionPlan:
        """Ersatzschritte für einen gescheiterten Schritt – nur nicht destruktiv und gegen die aktuelle Struktur geprüft."""

        def check(plan: ExecutionPlan) -> None:
            if len(plan.steps) > 3:
                raise ValueError("Höchstens 3 Ersatzschritte.")
            destructive = [s.title for s in plan.steps if s.risk == "destructive"]
            if destructive:
                raise ValueError(f"Destruktive Ersatzschritte sind nicht erlaubt: {destructive}")
            report = self._validator.validate(plan, schema)
            if report.errors or report.conflicts:
                raise ValueError("Die Ersatzschritte passen nicht zur Datenbank:\n"
                                 + "\n".join(issue.message for issue in [*report.errors, *report.conflicts]))

        return self._ai.complete_json(
            ExecutionPlan,
            messages=[ChatMessage(role="user", content=prompts.step_repair_prompt(
                task_text, step.model_dump_json(include={"title", "rationale", "action"}), error, schema.to_prompt_text()))],
            system=prompts.STEP_REPAIR_SYSTEM,
            purpose="reparatur",
            max_repairs=1,
            cancel=cancel,
            post_validate=check,
        )


    def fix_requirements(
        self, *, task_text: str, unmet: list[str], schema: DatabaseSchema, own_objects: set[str],
        attempts: list[str] | None = None, images: list[bytes] | None = None, cancel: threading.Event | None = None,
    ) -> ExecutionPlan:
        """Korrekturschritte für offene Anforderungen („nicht erfüllt“ oder „unklar“) samt Ursache.

        Nur nicht destruktiv, höchstens 5 Schritte, gegen den aktuellen Zustand geprüft. Ein Namenskonflikt ist nur bei
        Objekten erlaubt, die diese Aufgabe selbst eben erst angelegt hat (``own_objects``) – sie dürfen ersetzt werden.
        ``attempts`` sind die bisherigen Korrekturzyklen; die KI soll sie nicht wiederholen, sondern anders vorgehen.
        """
        own = {name.lower() for name in own_objects}

        def check(plan: ExecutionPlan) -> None:
            if len(plan.steps) > 5:
                raise ValueError("Höchstens 5 Korrekturschritte.")
            destructive = [s.title for s in plan.steps if s.risk == "destructive"]
            if destructive:
                raise ValueError(f"Destruktive Korrekturschritte sind nicht erlaubt: {destructive}")
            report = self._validator.validate(plan, schema)
            foreign = [c for c in report.conflicts if c.object_name.lower() not in own]
            if report.errors or foreign:
                raise ValueError("Die Korrekturschritte passen nicht zur Datenbank:\n"
                                 + "\n".join(issue.message for issue in [*report.errors, *foreign]))

        return self._ai.complete_json(
            ExecutionPlan,
            messages=[ChatMessage(role="user", content=prompts.requirement_fix_prompt(
                task_text, unmet, sorted(own_objects), schema.to_prompt_text(max_queries=80), attempts or []),
                images=[ImageInput(data=png) for png in images or []])],
            system=prompts.REQUIREMENT_FIX_SYSTEM,
            purpose="reparatur",
            max_repairs=1,
            cancel=cancel,
            post_validate=check,
        )


def _object_prompt(schema: DatabaseSchema) -> str:
    text = schema.model_copy(update={"tables": [], "queries": [], "relations": []}).to_prompt_text()
    return text[text.index("FORMULARE:"):]

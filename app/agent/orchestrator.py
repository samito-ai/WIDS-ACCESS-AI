"""Ablaufsteuerung: Aufgabe erkennen → verstehen → planen → freigeben → ausführen → prüfen → berichten."""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.access.engine import AccessEngine
from app.access.models import DatabaseSchema
from app.access.naming import unique_name
from app.access.service import AccessService
from app.access.sqlbuilder import classify_sql, ensure_single_statement
from app.agent import calc
from app.agent.analyzer import TaskAnalyzer
from app.agent.diff import missing_named_objects, schema_diff
from app.agent.executor import AccessExecutor, ExecOptions, StepResult
from app.agent.explainer import build_report
from app.agent.planner import PlanIssue, PlanValidator, ValidationReport
from app.agent.recovery import RecoveryManager, RecoveryState
from app.agent.verifier import ResultVerifier, Verification
from app.core.errors import (
    ApprovalRequiredError,
    PolicyViolationError,
    ProviderError,
    TaskCancelledError,
    WidsError,
)
from app.core.logging_setup import get_logger
from app.core.settings import AppSettings
from app.security.backup import BackupManager, FileState, inspect_file
from app.security.policy import SafetyPolicy
from app.storage.database import Storage, now_iso
from app.tasks import models as m
from app.tasks.classifier import Classification, classify_task

log = get_logger(__name__)

#: Höchstzahl der Selbstkorrektur-Zyklen nach der Anforderungsprüfung
CORRECTION_ROUNDS = 3

#: Einordnung, WARUM eine Aufgabe nicht vollständig gelöst ist – bestimmt, welchen anderen Weg die Korrektur sucht
FAILURE_HINTS = {
    "Access-Ausführung": "ein Schritt ließ sich in Access nicht ausführen – eine andere Access-Aktion bzw. andere "
                         "Parameter wählen, nicht denselben Schritt wiederholen",
    "Ergebnisprüfung": "der gespeicherte Zustand weicht von der Vorgabe ab – das vorhandene Objekt gezielt korrigieren "
                       "(Eigenschaft, Bereich, Objekt prüfen), Richtiges unverändert lassen",
    "KI-Plan": "alle Schritte liefen und bestanden ihre Prüfung, die Anforderung ist aber nicht erfüllt – der Plan "
               "deckte sie nicht ab: fehlende bzw. andere Schritte ergänzen",
    "Aufgabenerkennung": "der Aufgabentext ist vermutlich unvollständig erfasst – nur eindeutig Ableitbares planen und "
                         "Unklares in den Notizen nennen",
}


def classify_failure(results: list[StepResult], failing_checks: list[str], pending: list[m.RequirementResult],
                     task: m.CapturedTask) -> str:
    """Ursache offener Punkte: Access-Ausführung, Ergebnisprüfung, KI-Plan oder Aufgabenerkennung."""
    if any(r.status == "failed" for r in results):
        return "Access-Ausführung"
    if failing_checks:
        return "Ergebnisprüfung"
    if pending and task.method in ("ocr", "vision") and len(task.text.strip()) < 120 \
            and all(r.status == "unklar" for r in pending):
        return "Aufgabenerkennung"
    return "KI-Plan"
#: Datenschritte: nach einer nicht bestandenen Prüfung kein automatischer zweiter Weg (keine doppelten Datensätze)
_DATA_ACTIONS = (m.InsertRecords, m.ImportData, m.ImportOdbc, m.UpdateRecords, m.DeleteRecords, m.RunActionQuery,
                 m.AttachFiles, m.TransferObjects)
#: Datenanlegende Schritte: in der Selbstkorrektur nie ein zweites Mal, wenn sie schon erfolgreich liefen
_DATA_CREATE = (m.InsertRecords, m.ImportData, m.ImportOdbc, m.AttachFiles, m.TransferObjects)

ConflictChoice = Literal["overwrite", "rename", "skip", "abort"]
OutcomeStatus = Literal[
    "erfolgreich", "unklar", "ausgeführt", "teilweise", "fehlgeschlagen", "abgebrochen", "abgelehnt", "beantwortet",
    "blockiert"
]


class PipelineEvent(BaseModel):
    kind: Literal["status", "step_started", "step_done", "step_failed", "step_skipped", "verification", "backup", "log"]
    message: str = ""
    step_id: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    task_id: int
    db_path: str
    db_name: str
    attached: bool
    is_working_copy: bool
    file_state: FileState
    solution: m.AgentSolution
    validation: ValidationReport
    warnings: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    approved: bool = False
    #: Der Benutzer bestätigt, dass es sich um eine Übungsdatenbank (keine Prüfung) handelt
    practice_confirmed: bool = False


def _object_names(schema: DatabaseSchema) -> set[str]:
    """Namen aller Tabellen, Abfragen, Formulare und Berichte."""
    return {o.name for group in (schema.tables, schema.queries, schema.forms, schema.reports) for o in group}


def _cause(result: m.RequirementResult) -> str:
    """Anforderung samt konkreter Ursache aus der Prüfung (für die Abschlussmeldung)."""
    evidence = " ".join(result.evidence.split())
    return f"{result.number}. {result.requirement}" + (f" ({evidence[:160]})" if evidence else "")


def _data_target(step: m.PlanStep) -> str:
    """Zieltabelle eines datenanlegenden Schritts (ohne Feld- oder Pfeilzusatz), klein geschrieben."""
    return step.action.target().split(" → ")[-1].split(".")[0].strip().lower()


#: Fehler des Zugriffs (nicht des Plans) – keine KI-Ersatzschritte
_ACCESS_PROBLEMS = {"DatabaseLockedError", "AccessBusyError", "AccessTimeoutError", "AccessNotRunningError",
                    "DatabaseReadOnlyError", "DatabaseNotFoundError"}
_NO_DIALOG = "Wartet auf Bestätigung (Hintergrundbetrieb, keine Rückfrage möglich – im Dashboard erneut starten): "


_DATA_WRITES = (m.InsertRecords, m.UpdateRecords, m.DeleteRecords, m.ImportData, m.RunActionQuery, m.CopyTable)


def _tables_in(action: Any, tables: list[str]) -> set[str]:
    """Tabellen, die ein Schritt nennt – Ziel, Quelle oder in seinem SQL (als ganzes Wort)."""
    text = action.model_dump_json().lower()
    return {t for t in tables if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", text)}


def _held_by(action: Any, held: dict[str, int], tables: list[str]) -> int | None:
    """Schritt der nicht ausgeführten Löschung, von der dieser Schritt abhängt (oder ``None``).

    Abhängig sind alle späteren DATEN-Schritte an derselben Tabelle: Hinzufügen/Importieren ergäbe doppelte Daten,
    bedingte Änderungen träfen auch die zu löschenden Datensätze, Kopien enthielten sie. Formulare, Berichte, Filter
    und Abfrage-Definitionen ändern keine Daten – sie laufen weiter (Live-Fund 17.11.: Formularfilter „Golfclubs“)."""
    if not isinstance(action, _DATA_WRITES):
        return None
    return next((held[t] for t in sorted(_tables_in(action, tables)) if t in held), None)


def _can_ask(interaction: Any) -> bool:
    """Darf jetzt ein Dialog erscheinen? Im Hintergrundbetrieb (Dashboard geschlossen) nicht."""
    can_ask = getattr(interaction, "can_ask", None)
    return bool(can_ask()) if callable(can_ask) else True


class Interaction(Protocol):
    """Rückfragen an den Benutzer. Alle Methoden blockieren, bis eine Entscheidung vorliegt."""

    def approve_plan(self, request: ApprovalRequest) -> ApprovalDecision: ...

    def confirm_step(self, step: m.PlanStep, reason: str) -> bool: ...

    def resolve_conflict(self, step: m.PlanStep, message: str, suggestion: str) -> ConflictChoice: ...

    def on_event(self, event: PipelineEvent) -> None: ...


class PreparedTask(BaseModel):
    task_id: int
    task: m.CapturedTask
    classification: Classification
    solution: m.AgentSolution
    validation: ValidationReport
    db_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: Gründe, warum eine reine Antwort (Theorie/Multiple Choice) nicht als gesichert gilt – leer = belegt
    answer_issues: list[str] = Field(default_factory=list)


class TaskOutcome(BaseModel):
    task_id: int
    status: OutcomeStatus
    results: list[StepResult] = Field(default_factory=list)
    verifications: list[Verification] = Field(default_factory=list)
    report_md: str = ""
    backup_path: str = ""
    error: str = ""
    duration_s: float = 0.0
    #: Tatsächliche Änderungen (Vorher/Nachher-Vergleich der Datenbank)
    changes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    requirements: m.RequirementReport | None = None
    #: Verlauf der Selbstkorrektur (je Zyklus: Schritte → danach noch offen)
    corrections: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status in ("erfolgreich", "beantwortet")


class TaskPipeline:
    def __init__(
        self,
        *,
        settings: AppSettings,
        storage: Storage,
        access: AccessService,
        analyzer: TaskAnalyzer,
        backups: BackupManager,
        policy: SafetyPolicy,
        environment_probe: Any = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.settings = settings
        self._storage = storage
        self._access = access
        self._analyzer = analyzer
        self._backups = backups
        self._policy = policy
        self._validator = PlanValidator()
        self._executor = AccessExecutor()
        self._verifier = ResultVerifier()
        #: liefert (Fenstertitel, Prozessnamen) für den Prüfungsschutz; in Tests austauschbar
        self._environment_probe = environment_probe
        self._sleep = sleep

    def _reverify_touched(self, steps: list[m.PlanStep], results: list[StepResult]) -> list[Verification]:
        by_id = {r.step_id: r for r in results}
        fresh: list[Verification] = []
        for index, step in enumerate(steps):
            result = by_id.get(step.id)
            if isinstance(step.action, _DATA_ACTIONS) or result is None or result.status != "ok":
                continue  # Datenschritte prüfen Anzahlen von damals – eine spätere Änderung verfälscht sie nicht
            # Objekt = Teil vor dem Punkt („frmA.Umsatz“ → „frmA“; Access-Namen enthalten keinen Punkt)
            target, kind = step.action.target().lower().split(".")[0], step.action.action
            # nur wenn ein späterer Schritt das Objekt NEU ANLEGT – gewollte Änderungen (modify_*, Felder löschen)
            # dürfen frühere Prüfungen nicht nachträglich zu Fehlern machen
            if not any(later.action.target().lower().split(".")[0] == target and later.action.action != kind
                       and later.action.action.startswith("create_") and "." not in later.action.target()
                       for later in steps[index + 1:]):
                continue
            try:
                fresh.append(self._access.run(partial(self._verifier.verify, step=step, result=result)))
            except WidsError as exc:
                log.warning("Neuprüfung von Schritt %s nicht möglich: %s", step.id, exc)
        return fresh

    def _risk(self, step: m.PlanStep) -> m.Risk:
        """Risikostufe mit Blick auf den TATSÄCHLICHEN Feldtyp: eine verlustfreie Typerweiterung (z. B. Kurzer → Langer
        Text) ist keine löschende Änderung. Unbekannter Ausgangstyp = die vorsichtige Einstufung bleibt."""
        action = step.action
        if isinstance(action, m.ChangeFieldType) and action.size is None:
            try:
                table = self._access.schema().table(action.table)
                field = table.field(action.field) if table else None
            except Exception:
                field = None
            if field is not None and (field.type, action.new_type) in m.LOSSLESS_TYPE_CHANGES:
                return "modify"
        return step.risk

    @staticmethod
    def _data_evidence(solution: m.AgentSolution, verifications: list[Verification]) -> list[str]:
        """Belege für Datenanforderungen: Die Anforderungsprüfung sieht keine Datensätze (Datenschutz), wohl aber,
        welche Datenschritte der Verifier direkt in der Datenbank bestätigt hat – mit den Werten aus dem Plan."""
        steps = {s.id: s for s in solution.plan.steps}
        lines = []
        for v in verifications:
            step = steps.get(v.step_id)
            action = step.action if step else None
            if v.passed and isinstance(action, m.AnswerQuery | m.CalculateValue):
                answer = next((c.detail for c in v.checks if c.name == "Antwort erneut ermittelt"), "")
                lines.append(f"Schritt {v.step_id}: Frage „{action.question}“ per Abfrage beantwortet – Ergebnis: {answer}")
                continue
            if isinstance(action, m.CreateForm):  # gemessene Layoutbreiten mit Standardbreite (z. B. „verschmälern“)
                widths = next((c for c in v.checks if c.name == "Spaltenbreiten im Layout" and c.passed), None)
                if widths is not None:
                    lines.append(f"Schritt {v.step_id} (create_form „{action.target()}“): in Access gemessen – {widths.detail}")
                continue
            if not v.passed or not isinstance(action, m.InsertRecords | m.UpdateRecords | m.ImportData):
                continue
            checks = ", ".join(c.name + (f" ({c.detail})" if c.detail else "") for c in v.checks if c.passed)
            values = ""
            if isinstance(action, m.InsertRecords):
                values = " – eingefügte Werte: " + json.dumps(action.records[:5], ensure_ascii=False, default=str)[:600]
            elif isinstance(action, m.UpdateRecords):
                values = f" – gesetzt: {json.dumps(action.values, ensure_ascii=False, default=str)[:300]}" + \
                         (f" wo {action.where}" if action.where else " (alle Datensätze)")
            lines.append(f"Schritt {v.step_id} „{step.title if step else ''}“ ({action.action} in „{action.target()}“): "
                         f"bestätigt – {checks}{values}")
        return lines

    @staticmethod
    def _source_files(task_text: str, db_path: str) -> str:
        """Vorschau der in der Aufgabe genannten Quelldateien aus dem te.comp-Übungsordner (für Importaufgaben)."""
        from app.access import importer

        previews = []
        for path in importer.mentioned_sources(task_text, db_path)[:4]:
            try:
                previews.append(importer.preview(path))
            except Exception as exc:  # eine unlesbare Datei darf die Analyse nicht verhindern
                log.warning("Vorschau von %s nicht möglich: %s", path.name, exc)
        materials = []
        for path in importer.mentioned_sources(task_text, db_path, importer.MATERIAL_SUFFIXES)[:2]:
            try:
                materials.append(f"--- {path.name} ---\n{importer.material_text(path)}")
            except Exception as exc:
                log.warning("Lernunterlage %s nicht lesbar: %s", path.name, exc)
        parts = []
        if previews:
            parts.append("QUELLDATEIEN (Übungs-/Lernordner, für import_data):\n" + "\n".join(previews))
        if materials:
            parts.append("LERNUNTERLAGEN (in der Aufgabe genannt, nur lesend – Antworten daraus belegen):\n"
                         + "\n".join(materials))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ Phase 1: verstehen und planen
    def prepare(self, task: m.CapturedTask, *, cancel: threading.Event | None = None) -> PreparedTask:
        """Analysiert die Aufgabe und erstellt einen geprüften Plan. Verändert die Datenbank nicht."""
        try:
            return self._prepare(task, cancel=cancel)
        finally:
            self._analyzer.bind_task(None)  # spätere Fragen/Tests gehören zu keiner Aufgabe

    def _prepare(self, task: m.CapturedTask, *, cancel: threading.Event | None = None) -> PreparedTask:
        db_path = self._access.path if self._access.is_connected else ""
        task_id = self._storage.create_task(
            raw_text=task.text, source=task.source or task.window_title, capture_method=task.method, db_path=db_path
        )
        self._analyzer.bind_task(task_id)
        warnings = [*task.warnings, *self._policy.task_text_warnings(task.text)]
        classification = classify_task(task.text)
        self._storage.update_task(task_id, task_type=classification.task_type.value, status="erkannt")
        try:
            schema: DatabaseSchema | None = None
            if self._access.is_connected:
                schema = self._access.schema(refresh=True)
                lowered = task.text.lower()
                named = [o.name.lower() for o in schema.forms] + [o.name.lower() for o in schema.reports]
                # E-01 SI: die Aufgabe nannte den Bericht nicht – ohne Bereiche/Farben wählte die KI das Datenblatt.
                # Bei wenigen Formularen/Berichten deshalb immer die Details mitgeben (kostet je Objekt < 1 s).
                if named and (len(named) <= 6 or any(f"„{n}“" in lowered or f'"{n}"' in lowered for n in named)):
                    # „Bearbeiten Sie den Bericht …“: die Planung braucht Bereiche, Steuerelemente und Gruppen
                    schema = self._access.schema(refresh=True, detailed_objects=True)
            solution =self._analyzer.analyze(task, schema, cancel=cancel, extra_context=self._source_files(task.text, db_path))
            validation = self._validator.validate(solution.plan, schema)
        except WidsError as exc:
            self._storage.update_task(task_id, status="fehlgeschlagen", error=exc.user_message, finished_at=now_iso())
            self._storage.add_task_event(task_id, "error", exc.user_message, {"detail": exc.detail or ""})
            raise
        for step in solution.plan.steps:  # Rechenfrage: die Zahlen müssen wörtlich aus DIESER Aufgabe stammen
            if isinstance(step.action, m.CalculateValue) and not calc.quoted_in(step.action.given, task.text):
                validation.issues.append(PlanIssue(step_id=step.id, severity="error", message=(
                    "Die angegebenen Zahlen stehen so nicht in der Aufgabe – die Rechnung wird nicht ausgeführt.")))
        issues = m.answer_issues(solution.analysis) if solution.plan.is_empty else []
        status = "geplant" if not solution.plan.is_empty else ("unklar" if issues else "beantwortet")
        report = ""
        if solution.plan.is_empty:
            report = build_report(
                task_text=task.text, solution=solution, validation=validation, results=[], verifications=[],
                status=status, db_path=db_path, capture_method=task.method,
            )
        self._storage.update_task(
            task_id,
            task_type=solution.analysis.task_type.value,
            title=solution.analysis.title[:200],
            status=status,
            analysis_json=solution.analysis.model_dump_json(),
            plan_json=solution.plan.model_dump_json(),
            report_md=report or None,
            finished_at=now_iso() if solution.plan.is_empty else None,
            error=("Antwort nicht gesichert: " + "; ".join(issues)) if issues else None,
        )
        return PreparedTask(
            task_id=task_id,
            task=task,
            classification=classification,
            solution=solution,
            validation=validation,
            db_path=db_path,
            warnings=[*warnings, *(f"Antwort nicht gesichert: {i}" for i in issues)],
            answer_issues=issues,
        )

    # ------------------------------------------------------------------ Phase 2: freigeben, ausführen, prüfen
    def execute(
        self, prepared: PreparedTask, interaction: Interaction, *, cancel: threading.Event | None = None
    ) -> TaskOutcome:
        try:
            outcome = self._execute(prepared, interaction, cancel=cancel)
            if outcome.status not in ("erfolgreich", "blockiert", "beantwortet"):
                self._analyzer.clear_cache()  # nicht gelöst/abgelehnt: beim nächsten Mal neu planen
            return outcome
        except Exception as exc:
            self._analyzer.clear_cache()
            # Unerwarteter Abbruch: der Verlauf darf nie auf „läuft“ stehen bleiben (und nie „erfolgreich“ zeigen)
            try:
                record = self._storage.get_task(prepared.task_id)
                if record is not None and record.status == "läuft":
                    message = (exc.user_message if isinstance(exc, WidsError)
                               else f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")
                    hint = f" Sicherung vor der Ausführung: {record.backup_path}" if record.backup_path else ""
                    self._storage.update_task(prepared.task_id, status="fehlgeschlagen", error=message + hint,
                                              finished_at=now_iso())
            except Exception:  # z. B. Verlauf gesperrt/Datenträger voll: den ursprünglichen Fehler nicht verdecken
                log.exception("Status der abgebrochenen Aufgabe %s nicht gespeichert", prepared.task_id)
            raise
        finally:
            self._analyzer.bind_task(None)

    def _execute(
        self, prepared: PreparedTask, interaction: Interaction, *, cancel: threading.Event | None = None
    ) -> TaskOutcome:
        started = time.perf_counter()
        cancel = cancel or threading.Event()
        task_id = prepared.task_id
        self._analyzer.bind_task(task_id)
        solution = prepared.solution
        results: list[StepResult] = []
        verifications: list[Verification] = []
        changes: list[str] = []
        notes: list[str] = []
        requirements: list[m.RequirementReport | None] = [None]
        corrections: list[str] = []
        correction_steps: list[m.PlanStep] = []
        backup_path = ""

        def finish(status: OutcomeStatus, error: str = "") -> TaskOutcome:
            duration = round(time.perf_counter() - started, 2)
            report = build_report(
                task_text=prepared.task.text, solution=solution, validation=prepared.validation, results=results,
                verifications=verifications, status=status, db_path=self._access.path or prepared.db_path,
                backup_path=backup_path, error=error, duration_s=duration, capture_method=prepared.task.method,
                changes=changes, notes=notes, requirements=requirements[0],
                corrections=corrections, correction_steps=correction_steps,
                diagnostics=self.settings.automation.diagnostic_report,
            )
            outcome = TaskOutcome(
                task_id=task_id, status=status, results=results, verifications=verifications, report_md=report,
                backup_path=backup_path, error=error, duration_s=duration, changes=changes, notes=notes,
                requirements=requirements[0], corrections=corrections,
            )
            self._storage.update_task(
                task_id,
                status=status,
                result_json=outcome.model_dump_json(exclude={"report_md"}),
                report_md=report,
                error=error or None,
                backup_path=backup_path or None,
                duration_s=duration,
                finished_at=now_iso(),
                db_path=self._access.path or prepared.db_path,
            )
            interaction.on_event(PipelineEvent(kind="status", message=status, data={"error": error}))
            return outcome

        def event(kind: Any, message: str, step_id: int = 0, **data: Any) -> None:
            self._storage.add_task_event(task_id, kind, message, data or None)
            interaction.on_event(PipelineEvent(kind=kind, message=message, step_id=step_id, data=data))

        if solution.plan.is_empty:
            if prepared.answer_issues:
                return finish("unklar", "Antwort nicht gesichert: " + "; ".join(prepared.answer_issues))
            return finish("beantwortet")
        record = self._storage.get_task(task_id)
        if record is not None and record.status not in ("geplant", "erkannt"):
            # derselbe Plan darf nicht ein zweites Mal laufen (doppelte Datensätze, Konflikte); Verlauf bleibt unverändert
            raise WidsError(f"Dieser Plan wurde bereits ausgeführt (Status „{record.status}“). Für eine neue Ausführung "
                            "die Aufgabe bitte neu analysieren.")

        # --- Sicherheitscheck ------------------------------------------------------------------
        try:
            self._safety_check(prepared)
        except PolicyViolationError as exc:
            event("log", exc.user_message)
            return finish("blockiert", exc.user_message)
        if not prepared.validation.ok:
            return finish(
                "fehlgeschlagen",
                "Der Plan enthält Fehler und kann nicht ausgeführt werden:\n" + prepared.validation.error_text(),
            )
        db_path = self._access.path
        request = ApprovalRequest(
            task_id=task_id,
            db_path=db_path,
            db_name=db_path.replace("/", "\\").rsplit("\\", 1)[-1],
            attached=self._access.mode == "attached",
            is_working_copy=self._backups.is_working_copy(db_path),
            file_state=inspect_file(db_path),
            solution=solution,
            validation=prepared.validation,
            warnings=prepared.warnings,
        )
        destructive = [s for s in solution.plan.steps if self._risk(s) == "destructive"]
        if not destructive:
            # Der Benutzer hat die Bearbeitung selbst gestartet (Alt+W bzw. Dashboard): nicht löschende Schritte ohne
            # weitere Rückfrage. Prüfungsschutz (oben) und Sicherung (unten) gelten unverändert.
            event("log", "Vom Benutzer gestartet – nicht löschende Schritte ohne Rückfrage.", db=db_path)
        elif not _can_ask(interaction):
            # Hintergrundbetrieb: kein Dialog öffnet sich von selbst – löschende Schritte werden NIE automatisch
            # ausgeführt. Unabhängige, nicht löschende Schritte laufen trotzdem (Live-Fund „Golfclubs“: ein einzelner
            # Löschschritt sperrte den ganzen Plan); das Ergebnis bleibt „teilweise“, bis die Löschung bestätigt ist.
            reason = "Der Plan enthält löschende Schritte – die werden nur bei geöffnetem Dashboard bestätigt."
            if len(destructive) == len(solution.plan.steps):
                event("log", reason)
                return finish("blockiert", reason)
            event("log", f"{len(destructive)} löschende(r) Schritt(e) warten auf Bestätigung im Dashboard – die übrigen "
                         "Schritte werden ausgeführt.")
        else:
            decision = interaction.approve_plan(request)
            if not decision.approved:
                event("log", "Plan wurde nicht freigegeben.")
                return finish("abgelehnt")
            # Ohne gemerkte Freigabe gilt die Übungs-Bestätigung nur für diesen einen Plan
            if not decision.practice_confirmed:
                return finish("blockiert", "Ohne Bestätigung, dass es sich um eine Übungsdatenbank handelt, wird nichts geändert.")
            event("log", "Plan freigegeben.", db=db_path)

        # --- Sicherung -------------------------------------------------------------------------
        if self.settings.security.always_backup:
            try:
                backup = self._backups.create_backup(
                    db_path, reason=f"Vor Aufgabe {task_id}: {solution.analysis.title[:80]}",
                    max_keep=self.settings.security.max_backups_per_db,
                )
                backup_path = backup.backup_path
                event("backup", "Sicherungskopie erstellt.", path=backup_path)
            except WidsError as exc:
                return finish("fehlgeschlagen", f"Ohne Sicherungskopie wird nichts geändert: {exc.user_message}")

        # --- Ausführung ------------------------------------------------------------------------
        self._storage.update_task(task_id, status="läuft", backup_path=backup_path or None)
        recovery = RecoveryManager(
            max_sql_repairs=self.settings.automation.max_recovery_attempts,
            max_lock_retries=self.settings.access.lock_retry_count,
            lock_delay_s=self.settings.access.lock_retry_delay_s,
        )
        detail_actions = (m.CreateForm | m.CreateReport | m.SetConditionalFormat | m.ModifyReport | m.ModifyForm
                          | m.CreateNavigationForm)
        needs_detail = any(isinstance(s.action, detail_actions) for s in solution.plan.steps)
        try:
            # mit Details, wenn Formulare/Berichte geändert werden: sonst fehlen deren Änderungen im Bericht
            before = self._access.schema(refresh=True, detailed_objects=needs_detail)
            #: Objekt → Schritt einer NICHT ausgeführten Löschung: spätere Schritte am selben Objekt setzen sie voraus
            #: (z. B. „alle löschen, dann neu importieren“ ergäbe sonst doppelte Datensätze)
            held: dict[str, int] = {}
            for step in solution.plan.steps:
                if cancel.is_set():
                    raise TaskCancelledError()
                event("step_started", step.title, step.id)
                # Gegen den TATSÄCHLICHEN Zustand prüfen: verhindert Folgefehler nach einem gescheiterten Schritt
                current = self._access.schema(refresh=True)
                check = self._validator.validate(m.ExecutionPlan.model_construct(steps=[step], notes=[]), current)
                tables = [t.name.lower() for t in current.tables]
                waits_for = _held_by(step.action, held, tables)
                if waits_for is not None:
                    result = StepResult(step_id=step.id, status="skipped", message=(
                        f"Übersprungen: setzt die nicht ausgeführte Löschung in Schritt {waits_for} voraus"))
                elif check.errors:
                    result = StepResult(step_id=step.id, status="skipped",
                                        message="Übersprungen, passt nicht mehr zur Datenbank: " + check.errors[0].message)
                else:
                    result = self._run_step(step, interaction, recovery, cancel, event)
                verification: Verification | None = None
                if result.status == "failed":
                    repaired = self._repair(prepared, step, result, current, interaction, recovery, cancel, event)
                    if repaired is not None:
                        result, verification = repaired
                elif result.status == "ok" and self.settings.automation.verify_after_each_step:
                    self._access.invalidate_schema()
                    verification = self._access.run(partial(self._verifier.verify, step=step, result=result))
                results.append(result)
                self._access.invalidate_schema()
                if result.status == "skipped":
                    if self._risk(step) == "destructive":
                        for table in _tables_in(step.action, tables):
                            held.setdefault(table, step.id)
                    event("step_skipped", result.message, step.id)
                    continue
                if result.status == "failed":
                    event("step_failed", result.message, step.id, error_type=result.error_type)
                    continue  # unabhängige Folgeschritte trotzdem bearbeiten
                if verification is not None:
                    verifications.append(verification)
                    event(
                        "verification",
                        "Prüfung bestanden" if verification.passed
                        else "Prüfung nicht bestanden: " + "; ".join(c.name for c in verification.failed_checks),
                        step.id,
                        passed=verification.passed,
                    )
                event("step_done", result.message, step.id)
        except TaskCancelledError:
            self._access.invalidate_schema()
            return finish("abgebrochen", "Die Automatisierung wurde abgebrochen. Bereits ausgeführte Schritte bleiben erhalten; "
                          "die Sicherungskopie kann wiederhergestellt werden.")
        except WidsError as exc:
            log.exception("Unerwarteter Fehler in der Ausführung")
            self._access.invalidate_schema()
            return finish("fehlgeschlagen", exc.user_message)

        # --- Ergebnis gegen die Aufgabenstellung prüfen ------------------------------------------
        self._access.invalidate_schema()
        try:
            after = self._access.schema(refresh=True, detailed_objects=needs_detail)
            changes[:] = schema_diff(before, after)
            notes[:] = [f"„{n}“ wird in der Aufgabe genannt, existiert aber nicht in der Datenbank (falls es ein "
                        "Objektname ist, fehlt diese Teilaufgabe)." for n in missing_named_objects(prepared.task.text, after)]
        except WidsError as exc:
            after = None
            notes.append(f"Endzustand nicht lesbar: {exc.user_message}")
        failed = [r for r in results if r.status == "failed"]
        skipped = [r for r in results if r.status == "skipped"]
        unverified = [v for v in verifications if not v.passed]
        if failed:
            done = sum(1 for r in results if r.status == "ok")
            return finish("teilweise" if done else "fehlgeschlagen", failed[0].message)
        checkable = bool(self.settings.automation.check_requirements and solution.analysis.requirements and after is not None)
        plan_steps = {s.id: s for s in solution.plan.steps}
        # Schrittprüfung nicht bestanden: nicht sofort aufgeben, sondern in der Selbstkorrektur einen anderen Weg suchen
        # – außer bei Datenschritten (ein zweiter Versuch könnte Datensätze doppelt anlegen) oder übersprungenen Schritten
        correctable = (checkable and not skipped and self._analyzer.available
                       and not any(isinstance(plan_steps[v.step_id].action, _DATA_ACTIONS)
                                   for v in unverified if v.step_id in plan_steps))
        if unverified and not correctable:
            names = "; ".join(f"Schritt {v.step_id}: {', '.join(c.name for c in v.failed_checks)}" for v in unverified)
            return finish("teilweise", f"Die Ergebnisprüfung hat Abweichungen gefunden – {names}")
        if skipped:
            return finish("teilweise", f"{len(skipped)} Schritt(e) nicht ausgeführt – "
                          + "; ".join(f"Schritt {r.step_id}: {r.message}" for r in skipped))
        if not checkable or after is None:
            return finish("ausgeführt", "Die Anforderungen der Aufgabe wurden nicht einzeln geprüft.")

        def open_checks() -> list[str]:
            """Nicht bestandene kritische Prüfpunkte – auch die von Korrekturschritten. Je Aktion, Objekt und PRÜFPUNKT
            zählt das jüngste Ergebnis: behoben ist eine Abweichung erst, wenn genau dieser Prüfpunkt derselben Aktion am
            selben Objekt später besteht. Eine andere Aktion oder ein Schritt, der den Punkt gar nicht prüft, behebt nichts."""
            steps = {s.id: s for s in [*solution.plan.steps, *correction_steps]}
            latest: dict[tuple[str, str, str], tuple[int, bool, str]] = {}
            for v in sorted(verifications, key=lambda x: x.step_id):
                step = steps.get(v.step_id)
                if step is None:
                    continue
                for check in v.checks:
                    if check.critical:
                        key = (step.action.action, step.action.target().lower(), check.name)
                        latest[key] = (v.step_id, check.passed, check.detail)
            failing: dict[int, list[str]] = {}
            for (_, _, name), (step_id, passed, detail) in latest.items():
                if not passed:
                    failing.setdefault(step_id, []).append(f"{name} ({detail})" if detail else name)
            return [f"Schritt {sid} „{steps[sid].title}“: Prüfung nicht bestanden – " + ", ".join(names)
                    for sid, names in sorted(failing.items())]

        if unverified:
            event("log", f"{len(unverified)} Schrittprüfung(en) nicht bestanden – WIDS prüft die Anforderungen und sucht "
                         "einen anderen Lösungsweg …")
        event("log", "Prüfe jede Anforderung gegen den tatsächlichen Zustand der Datenbank …", phase="check")
        try:
            report = self._analyzer.check_requirements(
                task_text=prepared.task.text, requirements=solution.analysis.requirements,
                schema_after=after, changes=changes, cancel=cancel,
                verified=self._data_evidence(solution, verifications),
                sources=self._source_files(prepared.task.text, prepared.db_path or self._access.path),
            )
        except TaskCancelledError:
            return finish("ausgeführt", "Anforderungsprüfung abgebrochen.")
        except WidsError as exc:
            return finish("ausgeführt", f"Die Anforderungsprüfung war nicht möglich: {exc.user_message}")
        requirements[0] = report
        # Selbstkorrektur: begrenzte Zyklen Ursache → Korrektur → erneute Prüfung, nur solange es Fortschritt gibt
        after, report = self._correct(prepared, report, before, after, results, verifications, interaction,
                                      recovery, cancel, event, corrections, correction_steps, open_checks)
        # Ein späterer Schritt ANDERER Art am selben Objekt (z. B. Formular neu angelegt) kann ein früher bestätigtes
        # Ergebnis (z. B. bedingte Formatierung) zerstört haben → gegen den Endzustand neu prüfen
        for fresh in self._reverify_touched([*solution.plan.steps, *correction_steps], results):
            verifications.append(fresh)
            if not fresh.passed:
                event("verification", "Neuprüfung gegen den Endzustand nicht bestanden", fresh.step_id, passed=False)
        changes[:] = schema_diff(before, after)
        requirements[0] = report
        if report.unmet:
            return finish("teilweise", "Nicht erfüllt: " + "; ".join(_cause(r) for r in report.unmet))
        if still_open := open_checks():  # die unabhängige Schrittprüfung zählt – nie Erfolg trotz Abweichung
            return finish("teilweise", "Die Ergebnisprüfung hat Abweichungen gefunden – " + "; ".join(still_open))
        # „Erfolgreich“ nur, wenn JEDE Anforderung tatsächlich bestätigt ist – auch manuell markierte und nicht
        # automatisch prüfbare (z. B. Formularlayout) zählen sonst als nicht abgeschlossen, nie als Erfolg
        if report.unclear:
            return finish("unklar", "Nicht nachweisbar: " + "; ".join(_cause(r) for r in report.unclear))
        confirmed = {r.number for r in report.results if r.status == "erfüllt"}
        missing = [n for n in range(1, len(solution.analysis.requirements) + 1) if n not in confirmed]
        if missing:
            return finish("unklar", "Nicht bestätigt: " + ", ".join(str(n) for n in missing))
        return finish("erfolgreich")

    # ------------------------------------------------------------------ Einzelschritt
    def _run_step(
        self,
        step: m.PlanStep,
        interaction: Interaction,
        recovery: RecoveryManager,
        cancel: threading.Event,
        event: Any,
        *,
        replace: bool = False,
    ) -> StepResult:
        # replace=True nur in der Korrekturrunde für Objekte, die diese Aufgabe selbst angelegt hat
        options = ExecOptions(max_affected=self._policy.mass_change_threshold, replace=replace)
        state = RecoveryState()

        # Destruktive Schritte: eigene Bestätigung zusätzlich zur Planfreigabe
        if self._risk(step) == "destructive":
            if not _can_ask(interaction):
                return StepResult(step_id=step.id, status="skipped", message=_NO_DIALOG + "löschender Schritt nicht ausgeführt.")
            reason, exact = self._impact(step)
            if not interaction.confirm_step(step, reason):
                return StepResult(step_id=step.id, status="skipped", message="Vom Benutzer nicht bestätigt")
            if exact:
                options.max_affected = None  # der Benutzer hat die genaue Anzahl gesehen und bestätigt
                state.mass_confirmed = True

        attempts = 0
        while True:
            attempts += 1
            if cancel.is_set():
                raise TaskCancelledError()
            try:
                result = self._access.run(partial(self._executor.execute, step=step, options=options))
                result.attempts = attempts
                return result
            except TaskCancelledError:
                raise
            except WidsError as exc:
                decision = recovery.decide(exc, step, state, ai_available=self._analyzer_available())
                log.warning("Schritt %d fehlgeschlagen (%s): %s -> %s", step.id, type(exc).__name__, exc.detail or exc, decision.kind)
                if decision.kind == "retry":
                    state.lock_retries += 1
                    event("log", f"{exc.user_message} Neuer Versuch in {decision.wait_s:.0f} s …", step.id)
                    self._sleep(decision.wait_s)
                    continue
                if decision.kind == "resolve_conflict":
                    state.conflict_resolved = True
                    choice, new_name = self._resolve_conflict(step, exc, interaction)
                    if choice == "abort":
                        raise TaskCancelledError() from exc
                    if choice == "skip":
                        return StepResult(step_id=step.id, status="skipped", message=f"{exc.user_message} – übersprungen", attempts=attempts)
                    if choice == "overwrite":
                        options.replace = True
                        event("log", f"{exc.user_message} – vorhandenes Objekt wird ersetzt.", step.id)
                    else:
                        options.rename_to = new_name
                        event("log", f"{exc.user_message} – wird als „{new_name}“ angelegt.", step.id)
                    continue
                if decision.kind == "confirm_mass":
                    state.mass_confirmed = True
                    if not _can_ask(interaction):
                        return StepResult(step_id=step.id, status="skipped", attempts=attempts,
                                          message=f"{_NO_DIALOG}umfangreiche Änderung nicht ausgeführt ({exc.user_message})")
                    if interaction.confirm_step(step, exc.user_message):
                        options.max_affected = None
                        continue
                    return StepResult(step_id=step.id, status="skipped", message="Umfangreiche Änderung nicht bestätigt", attempts=attempts)
                if decision.kind == "repair_sql":
                    state.sql_repairs += 1
                    repaired = self._repair_sql(step, options, exc, cancel, event)
                    if repaired:
                        options.sql_override = repaired
                        continue
                return StepResult(
                    step_id=step.id, status="failed", message=decision.reason or exc.user_message,
                    error_type=type(exc).__name__, attempts=attempts, sql=options.sql_override or "",
                )

    def _correct(
        self,
        prepared: PreparedTask,
        report: m.RequirementReport,
        before: DatabaseSchema,
        after: DatabaseSchema,
        results: list[StepResult],
        verifications: list[Verification],
        interaction: Interaction,
        recovery: RecoveryManager,
        cancel: threading.Event,
        event: Any,
        history: list[str],
        extra_steps: list[m.PlanStep],
        open_checks: Any = None,
    ) -> tuple[DatabaseSchema, m.RequirementReport]:
        """Offene Anforderungen in höchstens ``CORRECTION_ROUNDS`` Zyklen nachbessern und jedes Mal neu prüfen.

        Offen = „nicht erfüllt“ oder „unklar“ (ohne die von der Analyse als manuell markierten) sowie nicht bestandene
        Schrittprüfungen (``open_checks``). Schluss ist, wenn die KI keine Korrektur findet, dieselben Schritte noch
        einmal vorschlägt, ein Zyklus das Ergebnis verschlechtert oder zwei Zyklen nacheinander keinen Fortschritt
        bringen – nach dem ersten Zyklus ohne Fortschritt wird ausdrücklich ein ANDERER Lösungsweg verlangt.
        Rückgabe: aktueller Zustand und letzte Prüfung.
        """
        manual = set(prepared.solution.analysis.manual_requirements)
        all_steps = list(prepared.solution.plan.steps)
        tried: set[str] = set()
        checks_open: Callable[[], list[str]] = open_checks or (lambda: [])
        stalled = 0
        for round_no in range(1, CORRECTION_ROUNDS + 1):
            pending = [r for r in report.results
                       if r.status == "nicht erfüllt" or (r.status == "unklar" and r.number not in manual)]
            failing = checks_open()
            if not (pending or failing) or not self._analyzer.available:
                break
            own = _object_names(after) - _object_names(before)
            cause = classify_failure(results, failing, pending, prepared.task)
            event("log", f"Korrekturzyklus {round_no}/{CORRECTION_ROUNDS}: {len(pending) + len(failing)} offene "
                         f"Anforderung(en)/Prüfung(en), Ursache: {cause} – die KI sucht einen anderen Weg …",
                  correction=round_no, cause=cause)
            try:
                plan = self._analyzer.fix_requirements(
                    task_text=prepared.task.text, unmet=[f"WIDS-Einordnung der Ursache: {cause} – {FAILURE_HINTS[cause]}"]
                    + [f"{r.number}. {r.requirement} [{r.status}] – {r.evidence}" for r in pending] + failing,
                    schema=after, own_objects=own, attempts=history, images=prepared.task.images, cancel=cancel)
            except TaskCancelledError:
                raise
            except WidsError as exc:
                event("log", f"Keine Korrektur gefunden: {exc.user_message}")
                break
            if plan.is_empty:
                event("log", "Keine weitere Korrektur möglich: " + " ".join(plan.notes))
                break
            # bereits erfolgreich angelegte Daten nie ein zweites Mal einfügen/importieren (keine doppelten Datensätze)
            # je ZIELTABELLE, über alle datenanlegenden Aktionen (Einfügen, Import, …) – auch nach einer _repair-Ersetzung
            ok_ids = {r.step_id for r in results if r.status == "ok"}
            done_data = {_data_target(s) for s in all_steps if s.id in ok_ids and isinstance(s.action, _DATA_CREATE)}
            repeated = [s for s in plan.steps if isinstance(s.action, _DATA_CREATE) and _data_target(s) in done_data]
            if repeated:
                event("log", "Nicht wiederholt (bereits ausgeführt, sonst doppelte Daten): "
                      + ", ".join(s.title for s in repeated))
                plan = m.ExecutionPlan.model_construct(steps=[s for s in plan.steps if s not in repeated], notes=plan.notes)
                if plan.is_empty:
                    break
            signature = json.dumps([s.action.model_dump(mode="json") for s in plan.steps], sort_keys=True, default=str)
            if signature in tried:
                event("log", "Die KI schlägt dieselbe Korrektur noch einmal vor – keine identische Wiederholung.")
                break
            tried.add(signature)
            outcomes = []
            next_id = max((r.step_id for r in results), default=0)
            for offset, step in enumerate(plan.steps, start=1):
                step.id = next_id + offset
                event("step_started", f"Korrektur: {step.title}", step.id)
                result = self._run_step(step, interaction, recovery, cancel, event,
                                        replace=step.action.target().lower() in {n.lower() for n in own})
                results.append(result)
                self._access.invalidate_schema()
                if result.status != "ok":
                    event("step_failed" if result.status == "failed" else "step_skipped", result.message, step.id)
                    outcomes.append(f"{step.title}: {result.status} – {result.message}")
                    continue
                verification = self._access.run(partial(self._verifier.verify, step=step, result=result))
                verifications.append(verification)
                event("verification", "Prüfung bestanden" if verification.passed else
                      "Prüfung nicht bestanden: " + "; ".join(c.name for c in verification.failed_checks),
                      step.id, passed=verification.passed)
                event("step_done", result.message, step.id)
                outcomes.append(f"{step.title}: ausgeführt" + ("" if verification.passed else " (Prüfung nicht bestanden)"))
            all_steps.extend(plan.steps)
            extra_steps.extend(plan.steps)
            needs_detail = any(isinstance(s.action, m.CreateForm | m.CreateReport | m.SetConditionalFormat | m.ModifyReport
                                          | m.ModifyForm | m.CreateNavigationForm) for s in all_steps)
            try:
                new_after = self._access.schema(refresh=True, detailed_objects=needs_detail)
                event("log", "Prüfe die Anforderungen nach der Korrektur erneut …")
                new_report = self._analyzer.check_requirements(
                    task_text=prepared.task.text, requirements=prepared.solution.analysis.requirements,
                    schema_after=new_after, changes=schema_diff(before, new_after), cancel=cancel,
                    verified=self._data_evidence(m.AgentSolution.model_construct(
                        analysis=prepared.solution.analysis,
                        plan=m.ExecutionPlan.model_construct(steps=all_steps, notes=[])), verifications),
                    sources=self._source_files(prepared.task.text, prepared.db_path or self._access.path),
                )
            except TaskCancelledError:
                raise
            except WidsError as exc:
                event("log", f"Erneute Prüfung nicht möglich: {exc.user_message}")
                break
            # Fortschritt = erfüllte Anforderungen minus offene Schrittprüfungen
            score_before = sum(r.status == "erfüllt" for r in report.results) - len(failing)
            failing_after = checks_open()
            score_after = sum(r.status == "erfüllt" for r in new_report.results) - len(failing_after)
            after, report = new_after, new_report  # die Datenbank hat sich geändert – nur die neue Prüfung gilt
            still = ", ".join([str(r.number) for r in report.results if r.status != "erfüllt"]
                              + [f"Prüfung Schritt {line.split(' ', 2)[1]}" for line in failing_after]) or "keine"
            history.append(f"Zyklus {round_no} (Ursache: {cause}): " + "; ".join(outcomes)
                           + f" → danach noch offen: {still}")
            if score_after < score_before:
                event("log", f"Korrekturzyklus {round_no} hat das Ergebnis verschlechtert – keine weiteren Versuche.")
                break
            if score_after == score_before:
                stalled += 1
                if stalled >= 2:
                    event("log", f"Auch Korrekturzyklus {round_no} ohne Fortschritt – weitere Versuche werden nicht "
                                 "unternommen.")
                    break
                history[-1] += " – KEIN FORTSCHRITT: im nächsten Zyklus einen grundlegend anderen Lösungsweg wählen"
                event("log", f"Kein Fortschritt im Korrekturzyklus {round_no} – nächster Versuch mit einem anderen "
                             "Lösungsweg.")
            else:
                stalled = 0
        return after, report

    def _analyzer_available(self) -> bool:
        return self._analyzer.available

    def _repair(
        self,
        prepared: PreparedTask,
        step: m.PlanStep,
        failed: StepResult,
        before: DatabaseSchema,
        interaction: Interaction,
        recovery: RecoveryManager,
        cancel: threading.Event,
        event: Any,
    ) -> tuple[StepResult, Verification] | None:
        """Gescheiterten Schritt durch geprüfte, nicht destruktive Ersatzschritte erreichen.

        Nur wenn der Fehlversuch nachweislich nichts verändert hat – sonst könnte eine zweite
        Ausführung Änderungen doppelt anlegen.
        """
        if not self._analyzer.available:
            return None
        if failed.error_type in _ACCESS_PROBLEMS:
            # Sperre/Verbindung: ein anderer Lösungsweg der KI ändert daran nichts – kostet nur Zeit und Geld
            event("log", "Keine KI-Korrektur: Ursache ist der Datenbankzugriff, nicht der Lösungsweg.", step.id)
            return None
        self._access.invalidate_schema()
        current = self._access.schema(refresh=True)
        if current.model_dump() != before.model_dump():
            event("log", "Der Fehlversuch hat die Datenbank teilweise verändert – keine automatische Korrektur, "
                         "damit nichts doppelt angelegt wird.", step.id)
            return None
        event("log", f"Ursache: {failed.message} – die KI sucht einen anderen Weg …", step.id)
        try:
            plan = self._analyzer.repair_step(task_text=prepared.task.text, step=step, error=failed.message,
                                              schema=current, cancel=cancel)
        except TaskCancelledError:
            raise
        except WidsError as exc:
            event("log", f"Keine Korrektur gefunden: {exc.user_message}", step.id)
            return None
        if plan.is_empty:
            event("log", "Keine Korrektur möglich: " + " ".join(plan.notes), step.id)
            return None
        merged = Verification(step_id=step.id)
        messages: list[str] = []
        for replacement in plan.steps:
            replacement.id = step.id
            result = self._run_step(replacement, interaction, recovery, cancel, event)
            if result.status != "ok":
                event("log", f"Ersatzschritt „{replacement.title}“ nicht ausgeführt: {result.message}", step.id)
                return None
            self._access.invalidate_schema()
            verification = self._access.run(partial(self._verifier.verify, step=replacement, result=result))
            merged.checks += verification.checks
            messages.append(result.message)
        event("log", "Korrektur erfolgreich: " + "; ".join(messages), step.id)
        return StepResult(
            step_id=step.id, status="ok", attempts=failed.attempts + 1,
            message=f"Nach Korrektur ausgeführt: {'; '.join(messages)} (ursprünglicher Fehler: {failed.message})",
            details={"repair_steps": [s.title for s in plan.steps], "original_error": failed.message},
        ), merged

    def _impact(self, step: m.PlanStep) -> tuple[str, bool]:
        """Beschreibt die Auswirkung eines destruktiven Schritts; ``True``, wenn die Anzahl exakt ist."""
        action = step.action
        try:
            if isinstance(action, m.DeleteRecords):
                count = self._access.run(lambda e: e.tables.count_records(action.table, action.where))
                scope = f"mit der Bedingung {action.where}" if action.where else "– ALLE Datensätze"
                return f"{count} Datensätze aus „{action.table}“ werden gelöscht ({scope}).", True
            if isinstance(action, m.UpdateRecords):
                count = self._access.run(lambda e: e.tables.count_records(action.table, action.where))
                return f"{count} Datensätze in „{action.table}“ werden überschrieben (keine Bedingung angegeben).", True
            if isinstance(action, m.DeleteTable):
                count = self._access.run(lambda e: e.tables.count_records(action.name))
                return f"Die Tabelle „{action.name}“ wird samt {count} Datensätzen gelöscht.", False
            if isinstance(action, m.RemoveField):
                return f"Das Feld „{action.table}.{action.field}“ wird samt Inhalt gelöscht.", False
            if isinstance(action, m.ChangeFieldType):
                return (
                    f"Der Datentyp von „{action.table}.{action.field}“ wird geändert – nicht umwandelbare Werte gehen verloren.",
                    False,
                )
            if isinstance(action, m.RunActionQuery):
                return f"Die Aktionsabfrage „{action.target()}“ wird ausgeführt und verändert Daten.", False
        except WidsError as exc:
            return f"{step.title} (Auswirkung nicht ermittelbar: {exc.user_message})", False
        return f"{step.title} – dieser Schritt löscht oder überschreibt Bestehendes.", False

    def _resolve_conflict(
        self, step: m.PlanStep, error: WidsError, interaction: Interaction
    ) -> tuple[ConflictChoice, str]:
        base = step.action.target().split(".")[-1].split(" → ")[0] or "Objekt"
        try:
            existing = self._access.run(self._all_names)
        except WidsError:
            existing = set()
        suggestion = unique_name(base, existing)
        policy = self.settings.automation.conflict_policy
        renamable = isinstance(
            step.action, m.CreateTable | m.CreateQuery | m.CreateForm | m.CreateReport | m.CopyTable
        )
        replaceable = isinstance(
            step.action,
            m.CreateTable | m.CreateQuery | m.CreateForm | m.CreateReport | m.CopyTable | m.CreateIndex | m.CreateRelationship,
        )
        choice: ConflictChoice
        if policy == "ask" and not _can_ask(interaction):
            choice = "skip"  # Hintergrund: Ersetzen/Umbenennen ist eine Entscheidung des Benutzers – nie automatisch
        elif policy == "ask":
            choice = interaction.resolve_conflict(step, error.user_message, suggestion if renamable else "")
        else:
            choice = policy  # type: ignore[assignment]
        if choice == "rename" and not renamable:
            choice = "skip"
        if choice == "overwrite" and (not replaceable or not _can_ask(interaction)):
            choice = "skip"  # Ersetzen löscht das Vorhandene – im Hintergrund nie ohne Bestätigung (auch nicht per Einstellung)
        return choice, suggestion

    @staticmethod
    def _all_names(engine: AccessEngine) -> set[str]:
        schema = engine.schema()
        names = {t.name for t in schema.tables} | {q.name for q in schema.queries}
        names |= {f.name for f in schema.forms} | {r.name for r in schema.reports}
        return names

    def _repair_sql(
        self, step: m.PlanStep, options: ExecOptions, error: WidsError, cancel: threading.Event, event: Any
    ) -> str | None:
        """Lässt die KI eine abgelehnte Abfrage korrigieren und prüft das Ergebnis, bevor es verwendet wird."""
        action = step.action
        if not isinstance(action, m.CreateQuery):
            return None
        try:
            schema = self._access.schema(refresh=True)
            current = options.sql_override or self._executor.resolve_sql(action, schema)
            original_kind = classify_sql(current)
            event("log", "Access hat die Abfrage abgelehnt – die KI korrigiert das SQL …", step.id)
            repair = self._analyzer.repair_sql(
                schema=schema, requirement=f"{step.title}. {step.rationale}", sql=current,
                error=error.user_message, cancel=cancel,
            )
            candidate = ensure_single_statement(repair.sql)
            if classify_sql(candidate) != original_kind:
                event("log", "Die Korrektur würde die Abfrageart ändern und wird verworfen.", step.id)
                return None
            check = self._access.run(lambda e: e.queries.check_sql(candidate))
            if not check.ok:
                event("log", f"Auch die korrigierte Abfrage wird abgelehnt: {check.error}", step.id)
                return None
            event("log", f"SQL korrigiert: {repair.reason}", step.id)
            return candidate
        except (ProviderError, WidsError) as exc:
            event("log", f"SQL-Korrektur nicht möglich: {exc.user_message}", step.id)
            return None

    # ------------------------------------------------------------------ Sicherheit
    def _safety_check(self, prepared: PreparedTask) -> None:
        if not self._access.is_connected:
            raise PolicyViolationError("Es ist keine Übungsdatenbank verbunden. Bitte zuerst eine Datenbank öffnen.")
        problems = self._policy.validate_target(self._access.path)
        if problems:
            raise PolicyViolationError("Die Datenbank kann nicht verändert werden: " + " ".join(problems))
        if prepared.db_path and prepared.db_path.lower() != self._access.path.lower():
            raise PolicyViolationError(
                "Der Plan wurde für eine andere Datenbank erstellt. Bitte die Aufgabe neu analysieren."
            )
        if self._environment_probe is not None:
            titles, processes = self._environment_probe()
            exam = self._policy.check_environment(titles, processes)
            if exam.blocked:
                raise PolicyViolationError(
                    "Es gibt Anzeichen für eine laufende Prüfung – die Automatisierung ist gesperrt. "
                    + " ".join(exam.reasons)
                )


class AutoInteraction:
    """Nicht-interaktive Umsetzung (für Tests und Skripte): feste, vorab festgelegte Antworten."""

    def __init__(
        self,
        *,
        approve: bool = True,
        practice_confirmed: bool = True,
        confirm_destructive: bool = False,
        conflict: ConflictChoice = "skip",
        interactive: bool = True,
    ) -> None:
        self.approve = approve
        self.practice_confirmed = practice_confirmed
        self.confirm_destructive = confirm_destructive
        self.conflict = conflict
        self.interactive = interactive  # False = wie der Hintergrundbetrieb: keine Rückfrage möglich
        self.events: list[PipelineEvent] = []
        self.requests: list[ApprovalRequest] = []
        self.confirmations: list[tuple[int, str]] = []
        self.conflicts: list[tuple[int, str, str]] = []

    def can_ask(self) -> bool:
        return self.interactive

    def approve_plan(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(approved=self.approve, practice_confirmed=self.practice_confirmed)

    def confirm_step(self, step: m.PlanStep, reason: str) -> bool:
        self.confirmations.append((step.id, reason))
        return self.confirm_destructive

    def resolve_conflict(self, step: m.PlanStep, message: str, suggestion: str) -> ConflictChoice:
        self.conflicts.append((step.id, message, suggestion))
        return self.conflict

    def on_event(self, event: PipelineEvent) -> None:
        self.events.append(event)


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalRequiredError",
    "AutoInteraction",
    "ConflictChoice",
    "Interaction",
    "PipelineEvent",
    "PreparedTask",
    "TaskOutcome",
    "TaskPipeline",
]

"""Explanation Engine: erzeugt das nachvollziehbare Ergebnisprotokoll einer Aufgabe."""
from __future__ import annotations

from datetime import datetime

from app.agent.executor import StepResult
from app.agent.planner import ValidationReport
from app.agent.verifier import Verification
from app.tasks.models import (
    TASK_TYPE_LABELS,
    AgentSolution,
    ExecutionPlan,
    PlanStep,
    RequirementReport,
    TaskAnalysis,
)

STATUS_LABELS = {
    "erfolgreich": "Erfolgreich abgeschlossen",
    # intern getrennt (unklar/ausgeführt/teilweise), angezeigt immer als „Nicht abgeschlossen“ mit Ursache
    "unklar": "Nicht abgeschlossen – nicht alle Anforderungen nachweisbar erfüllt",
    "ausgeführt": "Nicht abgeschlossen – Anforderungen nicht geprüft",
    "teilweise": "Nicht abgeschlossen – Anforderungen nicht erfüllt",
    "fehlgeschlagen": "Fehlgeschlagen",
    "abgebrochen": "Abgebrochen",
    "abgelehnt": "Nicht freigegeben",
    "beantwortet": "Beantwortet",
    "geplant": "Geplant (nicht ausgeführt)",
    "blockiert": "Aus Sicherheitsgründen blockiert",
}
_RISK = {"safe": "", "modify": " _(ändert Bestehendes)_", "destructive": " **(löscht – nur mit Sicherung und eindeutigem Ziel)**"}


def analysis_markdown(analysis: TaskAnalysis) -> str:
    lines: list[str] = []
    types = [analysis.task_type, *analysis.additional_types]
    labels = ", ".join(dict.fromkeys(TASK_TYPE_LABELS.get(t, t.value) for t in types))
    lines.append(f"**Aufgabentyp:** {labels}  ")
    lines.append(f"**Sicherheit der Einschätzung:** {round(analysis.confidence * 100)} %")
    if analysis.summary:
        lines += ["", analysis.summary]
    if analysis.requirements:
        lines += ["", "**Anforderungen**"] + [f"- {r}" for r in analysis.requirements]
    if analysis.assumptions:
        lines += ["", "**Annahmen**"] + [f"- {a}" for a in analysis.assumptions]
    if analysis.open_questions:
        lines += ["", "**Offene Punkte**"] + [f"- {q}" for q in analysis.open_questions]
    if analysis.multiple_choice:
        mc = analysis.multiple_choice
        lines += ["", "**Multiple Choice**"]
        if mc.question:
            lines.append(mc.question)
        for option in mc.options:
            mark = "✅" if option.correct else "❌"
            label = f"{option.label}) " if option.label else ""
            reason = f" – {option.reason}" if option.reason else ""
            lines.append(f"- {mark} {label}{option.text}{reason}")
        if mc.explanation:
            lines += ["", mc.explanation]
        if mc.uncertainty:
            lines += ["", f"⚠️ **Unsicherheit:** {mc.uncertainty}"]
    if analysis.answer:
        lines += ["", "**Antwort**", analysis.answer]
    if analysis.explanation:
        lines += ["", "**So löst man es selbst in Access**", analysis.explanation]
    return "\n".join(lines)


def plan_markdown(plan: ExecutionPlan, validation: ValidationReport | None = None) -> str:
    if plan.is_empty:
        return "_Für diese Aufgabe sind keine Änderungen an der Datenbank nötig._"
    lines: list[str] = []
    for step in plan.steps:
        covers = f" _(Anforderung {', '.join(map(str, step.covers))})_" if step.covers else ""
        lines.append(f"{step.id}. **{step.title}**{_RISK[step.risk]}{covers}")
        if step.rationale:
            lines.append(f"   - {step.rationale}")
        if validation and step.id in validation.sql_by_step:
            lines.append("   ```sql")
            lines += [f"   {line}" for line in validation.sql_by_step[step.id].splitlines()]
            lines.append("   ```")
        if validation:
            for issue in validation.issues:
                if issue.step_id == step.id:
                    icon = {"error": "⛔", "warning": "⚠️", "conflict": "🔁"}[issue.severity]
                    lines.append(f"   - {icon} {issue.message}")
    for note in plan.notes:
        lines.append(f"- ℹ️ {note}")
    return "\n".join(lines)


def build_report(
    *,
    task_text: str,
    solution: AgentSolution,
    validation: ValidationReport | None,
    results: list[StepResult],
    verifications: list[Verification],
    status: str,
    db_path: str = "",
    backup_path: str = "",
    error: str = "",
    duration_s: float | None = None,
    capture_method: str = "",
    changes: list[str] | None = None,
    notes: list[str] | None = None,
    requirements: RequirementReport | None = None,
    corrections: list[str] | None = None,
    correction_steps: list[PlanStep] | None = None,
    diagnostics: bool = False,
) -> str:
    """Abschlussbericht in Markdown (wird im Verlauf gespeichert und kann exportiert werden)."""
    by_step_result = {r.step_id: r for r in results}
    by_step_verification = {v.step_id: v for v in verifications}
    lines = [f"# {solution.analysis.title or 'Aufgabe'}", ""]
    lines.append(f"**Status:** {STATUS_LABELS.get(status, status)}  ")
    lines.append(f"**Zeitpunkt:** {datetime.now().strftime('%d.%m.%Y %H:%M')}  ")
    if db_path:
        lines.append(f"**Datenbank:** `{db_path}`  ")
    if backup_path:
        lines.append(f"**Sicherung vor der Ausführung:** `{backup_path}`  ")
    if capture_method:
        lines.append(f"**Erfassung:** {capture_method}  ")
    if duration_s is not None:
        lines.append(f"**Dauer:** {duration_s:.1f} s")
    if error:
        lines += ["", f"> ⛔ {error}"]
    lines += ["", "## Aufgabenstellung", "", task_text.strip() or "_(leer)_", "", "## Analyse", "", analysis_markdown(solution.analysis)]

    def render(step: PlanStep) -> None:
        """Ein Schritt mit Ergebnis, SQL und Prüfung."""
        nonlocal lines
        result = by_step_result.get(step.id)
        verification = by_step_verification.get(step.id)
        if result is None:
            icon, state = "⏸️", "nicht ausgeführt"
        elif result.status == "failed":
            icon, state = "❌", f"fehlgeschlagen – {result.message}"
        elif result.status == "skipped":
            icon, state = "⏭️", f"übersprungen – {result.message}"
        elif verification is not None and not verification.passed:
            icon, state = "⚠️", f"ausgeführt, Prüfung nicht bestanden – {result.message}"
        else:
            icon, state = "✅", result.message
        lines.append(f"### {icon} Schritt {step.id}: {step.title}")
        lines.append(state)
        sql = (result.sql if result else "") or (validation.sql_by_step.get(step.id, "") if validation else "")
        if sql:
            lines += ["", "```sql", sql, "```"]
        if result is not None and result.attempts > 1:
            lines.append(f"_Versuche: {result.attempts}_")
        if verification is not None and verification.checks:
            lines.append("")
            lines.append("Prüfung des Ergebnisses:")
            for check in verification.checks:
                mark = "✓" if check.passed else ("✗" if check.critical else "•")
                detail = f" ({check.detail})" if check.detail else ""
                lines.append(f"- {mark} {check.name}{detail}")
            rows = verification.info.get("sample_rows")
            columns = verification.info.get("columns")
            if rows and columns:
                lines += ["", f"Vorschau ({verification.info.get('row_count', len(rows))} Datensätze):", ""]
                lines.append("| " + " | ".join(str(c) for c in columns) + " |")
                lines.append("|" + "---|" * len(columns))
                for row in rows:
                    lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        lines.append("")

    if not solution.plan.is_empty:
        lines += ["", "## Durchgeführte Schritte", ""]
        for step in solution.plan.steps:
            render(step)
    if correction_steps:  # Selbstkorrektur: diese Schritte stehen nicht im ursprünglichen Plan
        lines += ["", "## Korrekturschritte (Selbstkorrektur)", ""]
        for step in correction_steps:
            render(step)
    if requirements is not None:
        marks = {"erfüllt": "✅", "nicht erfüllt": "❌", "unklar": "❔"}
        lines += ["", "## Prüfung der Anforderungen (gegen den tatsächlichen Datenbankzustand)", ""]
        lines += [f"- {marks[r.status]} {r.number}. {r.requirement} – {r.evidence}" for r in requirements.results]
    if changes:
        lines += ["", "## Tatsächliche Änderungen an der Datenbank", ""] + [f"- {c}" for c in changes]
    if notes:
        lines += ["", "## Hinweise", ""] + [f"- ⚠️ {n}" for n in notes]
    if diagnostics:
        lines += ["", diagnosis_markdown(solution, results, verifications, requirements, corrections or [],
                                         correction_steps or [], status)]
    return "\n".join(lines).rstrip() + "\n"


def diagnosis_markdown(
    solution: AgentSolution,
    results: list[StepResult],
    verifications: list[Verification],
    requirements: RequirementReport | None,
    corrections: list[str],
    correction_steps: list[PlanStep],
    status: str,
) -> str:
    """Nachvollziehbarkeit ohne KI-Anfrage: welche Anforderung erkannt, durch welche Schritte ausgeführt und wie
    (Schrittprüfung, Anforderungsprüfung) überprüft wurde. Geheimnisse werden geschwärzt."""
    from app.core.logging_setup import redact

    by_result = {r.step_id: r for r in results}
    by_check = {v.step_id: v for v in verifications}
    steps = [*solution.plan.steps, *correction_steps]
    manual = set(solution.analysis.manual_requirements)
    checked = {r.number: r for r in requirements.results} if requirements is not None else {}
    marks = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}

    def cell(text: str) -> str:
        return text.replace("|", "/").replace("\n", " ")

    def step_state(step: PlanStep) -> tuple[str, str]:
        result = by_result.get(step.id)
        verification = by_check.get(step.id)
        run = f"{step.id} {marks.get(result.status, '?')}" if result else f"{step.id} ⏸️"
        if verification is None:
            return run, "–"
        critical = [c for c in verification.checks if c.critical]
        passed = sum(c.passed for c in critical)
        return run, f"{step.id}: {passed}/{len(critical)} ✓" if critical else f"{step.id}: keine Prüfpunkte"

    ok = sum(r.status == "ok" for r in results)
    lines = ["## Diagnose (Anforderung → Schritte → Prüfung)", "",
             f"Anforderungen erkannt: {len(solution.analysis.requirements)} · Planschritte: {len(solution.plan.steps)}"
             f" · Korrekturschritte: {len(correction_steps)} · ausgeführt: {ok}/{len(results)}"
             f" · Schrittprüfungen bestanden: {sum(v.passed for v in verifications)}/{len(verifications)}"
             f" · Status: {STATUS_LABELS.get(status, status)}", "",
             "| Nr. | Anforderung (erkannt) | Schritte (ausgeführt) | Schrittprüfung | Anforderungsprüfung |",
             "|---|---|---|---|---|"]
    for number, text in enumerate(solution.analysis.requirements, start=1):
        covering = [s for s in steps if number in s.covers]
        states = [step_state(s) for s in covering]
        executed = ", ".join(r for r, _ in states) or ("manuell (kein Schritt)" if number in manual else "kein Schritt")
        verified = ", ".join(v for _, v in states) or "–"
        req = checked.get(number)
        verdict = f"{req.status} – {req.evidence}" if req else "nicht geprüft"
        lines.append(f"| {number} | {cell(text)} | {executed} | {verified} | {cell(verdict)} |")
    failed = [(v.step_id, c) for v in verifications for c in v.checks if c.critical and not c.passed]
    if failed:
        lines += ["", "Nicht bestandene Prüfpunkte:", ""]
        lines += [f"- Schritt {sid}: {c.name}" + (f" ({c.detail})" if c.detail else "") for sid, c in failed]
    if corrections:
        lines += ["", "Selbstkorrektur:", ""] + [f"- {c}" for c in corrections]
    return redact("\n".join(lines))

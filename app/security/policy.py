"""Sicherheitsregeln: Prüfungsschutz, Zielprüfung, Bestätigungspflichten."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.settings import SecuritySettings
from app.security.backup import ACCESS_EXTENSIONS, inspect_file

#: Prozesse von Prüfungs-/Sperrbrowsern – läuft einer davon, wird nichts automatisiert
LOCKDOWN_PROCESSES = frozenset(
    {
        "safeexambrowser.exe",
        "safeexambrowser.client.exe",
        "seb.exe",
        "lockdownbrowser.exe",
        "lockdownbrowseroem.exe",
        "respondus.exe",
        "examplify.exe",
        "proctorio.exe",
        "exam.net.exe",
    }
)
#: Prüfungsumgebungen, deren Programmname je Version variiert (z. B. Next Exam: „Next-Exam-Student.exe“).
#: Safe Exam Browser NICHT per Präfix: sein Windows-Dienst „SafeExamBrowser.Service.exe“ läuft nach der Installation
#: dauerhaft (Starttyp automatisch, gemessen 14.11.) – eine Prüfung läuft nur, wenn SafeExamBrowser(.Client).exe läuft
LOCKDOWN_PROCESS_PREFIXES = ("next-exam", "nextexam", "next exam")
#: Fenstertitel echter Prüfungsumgebungen – immer geprüft, unabhängig von den einstellbaren Schlüsselwörtern
EXAM_ENVIRONMENT_TITLES = ("next-exam", "next exam", "nextexam", "safe exam browser")
#: Wörter, die im Unterricht auch Übungen bezeichnen („Schularbeit“ = Übungsdatenbank des Lehrers) – sie sperren
#: nicht mehr; echte Schularbeiten laufen in Next Exam und werden über dessen Programm/Fenster erkannt
AMBIGUOUS_KEYWORDS = frozenset({"schularbeit"})

#: Fenstertitel von te.comp-training, die im normalen Übungsbetrieb vorkommen und das Wort
#: „Prüfung“ enthalten, ohne dass eine benotete Prüfung läuft („Lösung prüfen“-Funktion).
TRAINING_TITLE_WHITELIST = ("automatische prüfung", "lösung überprüfen", "ihre lösung überprüfen")


class ExamCheck(BaseModel):
    blocked: bool = False
    reasons: list[str] = Field(default_factory=list)


class SafetyPolicy:
    def __init__(self, settings: SecuritySettings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------ Prüfungsschutz
    def check_environment(self, window_titles: Iterable[str], process_names: Iterable[str]) -> ExamCheck:
        """Sucht nach Anzeichen einer laufenden Prüfung (Sperrbrowser, eindeutige Fenstertitel).

        Das ist eine Heuristik: Sie erkennt bekannte Prüfungsumgebungen, kann eine Prüfung aber
        nicht mit Sicherheit ausschließen. Deshalb verlangt die Anwendung zusätzlich je Datenbank
        die ausdrückliche Bestätigung, dass es sich um eine Übung handelt.
        """
        if not self._settings.block_in_exam_mode:
            return ExamCheck()
        reasons: list[str] = []
        running = {name.lower() for name in process_names}
        hits = sorted(n for n in running if n in LOCKDOWN_PROCESSES or n.startswith(LOCKDOWN_PROCESS_PREFIXES))
        if hits:
            reasons.append(f"Prüfungsumgebung/Sperrbrowser aktiv: {', '.join(hits)}")
        keywords = [k.lower() for k in self._settings.exam_keywords if k.strip() and k.lower() not in AMBIGUOUS_KEYWORDS]
        keywords += [t for t in EXAM_ENVIRONMENT_TITLES if t not in keywords]
        for title in window_titles:
            lowered = title.lower().strip()
            for white in TRAINING_TITLE_WHITELIST:  # nur die harmlose Phrase entschärfen, nie den ganzen Titel
                lowered = lowered.replace(white, " ")
            if not lowered.strip():
                continue
            for keyword in keywords:
                if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", lowered):
                    reasons.append(f"Fenster „{title[:80]}“ enthält „{keyword}“")
                    break
        return ExamCheck(blocked=bool(reasons), reasons=reasons)

    @staticmethod
    def task_text_warnings(text: str) -> list[str]:
        """Hinweise aus dem Aufgabentext selbst (blockiert nicht, wird aber angezeigt)."""
        lowered = text.lower()
        warnings: list[str] = []
        # „Schularbeit“ fehlt bewusst: so heißen im Unterricht auch gewöhnliche Übungen
        if re.search(r"\b(abschlusspr[üu]fung|pr[üu]fungsaufgabe|leistungsfeststellung|klausur)\b", lowered):
            warnings.append(
                "Der Aufgabentext deutet auf eine benotete Prüfung hin. WIDS ACCESS AI ist nur für Übungen gedacht."
            )
        return warnings

    # ------------------------------------------------------------------ Ziel
    @staticmethod
    def validate_target(db_path: str) -> list[str]:
        """Prüft, ob die Zieldatei eine veränderbare Access-Datenbank ist."""
        if not db_path:
            return ["Es ist keine Datenbank verbunden."]
        path = Path(db_path)
        if path.suffix.lower() not in ACCESS_EXTENSIONS:
            return [f"„{path.name}“ ist keine Access-Datenbank (.accdb/.mdb)."]
        return inspect_file(path).issues


    @property
    def mass_change_threshold(self) -> int:
        return max(0, self._settings.mass_change_threshold)

"""Live-Fortschritt einer Aufgabe – nur aus tatsächlichen Arbeitsschritten (Ereignisse der Pipeline und der
Oberfläche), keine geschätzten Prozentwerte und keine künstliche Ladeanimation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

PHASES = ("Aufgabenstellung erkannt", "Datenbank verbunden", "KI analysiert Anforderungen", "Lösungsplan erstellt",
          "Schritte ausführen", "Ergebnisprüfung")


@dataclass
class ProgressTracker:
    #: Phase → "pending" | "active" | "done" | "failed"
    phases: dict[str, str] = field(default_factory=lambda: dict.fromkeys(PHASES, "pending"))
    total_steps: int = 0
    done_steps: int = 0
    current: str = ""
    #: Schritt → Titel; eine spätere nicht bestandene Prüfung desselben Schritts nimmt die Bestätigung zurück
    confirmed_steps: dict[int, str] = field(default_factory=dict)
    correction: str = ""
    result: str = ""
    started: float | None = None
    finished: float | None = None
    _titles: dict[int, str] = field(default_factory=dict)

    @property
    def confirmed(self) -> list[str]:
        return list(self.confirmed_steps.values())

    @property
    def running(self) -> bool:
        return self.started is not None and self.finished is None

    def start(self) -> None:
        """Neue Aufgabe (Alt+W oder Dashboard) – alles zurück auf Anfang."""
        vars(self).update(vars(ProgressTracker()))
        self.started = time.monotonic()

    def resume(self) -> None:
        """Ausführung eines bereits erstellten Plans (Klick auf „Plan ausführen“): Zeitmessung läuft weiter."""
        if self.started is None or self.finished is not None:
            self.started, self.finished, self.result = time.monotonic(), None, ""

    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or time.monotonic()) - self.started

    def mark(self, phase: str, state: str = "done") -> None:
        if self.started is None:
            self.started = time.monotonic()
        self.phases[phase] = state

    def plan(self, steps: int) -> None:
        self.total_steps, self.done_steps = steps, 0
        self.mark("Lösungsplan erstellt")
        self.mark("Schritte ausführen", "active")

    def on_event(self, kind: str, message: str, step_id: int = 0, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        if kind == "step_started":
            self._titles[step_id] = message
            self.total_steps = max(self.total_steps, step_id)  # Korrekturschritte kommen hinzu
            self.current = f"Schritt {step_id} von {self.total_steps}: {message}"
            self.mark("Schritte ausführen", "active")
        elif kind in ("step_done", "step_failed", "step_skipped"):
            self.done_steps = min(self.done_steps + 1, self.total_steps or self.done_steps + 1)
            if kind == "step_failed":
                self.current = f"Schritt {step_id} fehlgeschlagen: {message}"
        elif kind == "verification" and data.get("passed"):
            self.confirmed_steps[step_id] = self._titles.get(step_id, f"Schritt {step_id}")
        elif kind == "verification":
            self.confirmed_steps.pop(step_id, None)
        elif kind == "log" and data.get("phase") == "check":
            self.mark("Schritte ausführen")
            self.mark("Ergebnisprüfung", "active")
            self.current = "Ergebnisprüfung läuft"
        elif kind == "log" and data.get("correction"):
            self.correction = f"Selbstkorrektur, Zyklus {data['correction']} von 3 – Ursache: {data.get('cause', '?')}"

    def finish(self, text: str, state: str) -> None:
        """Ende der Aufgabe: ``state`` wie der Statuspunkt (ok = alle Prüfungen bestanden)."""
        if self.started is None:
            return
        self.finished = time.monotonic()
        self.result = text
        self.current = ""
        for phase, value in self.phases.items():
            if value == "active":
                self.phases[phase] = "done" if state == "ok" else "failed"
        if state == "ok" and self.correction:
            self.correction += " → erfolgreich"

    def lines(self) -> list[str]:
        icons = {"pending": "○", "active": "▶", "done": "✓", "failed": "✗"}
        out = [f"{icons[state]} {phase}" + (f" ({self.done_steps}/{self.total_steps})" if phase == "Schritte ausführen"
                                             and self.total_steps else "")
               for phase, state in self.phases.items()]
        if self.current:
            out.append(f"→ {self.current}")
        if self.correction:
            out.append(self.correction)
        return out

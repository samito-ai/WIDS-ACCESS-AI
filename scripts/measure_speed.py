"""Misst die Laufzeit der lokalen Arbeitsschritte (ohne KI-Wartezeit: skriptierte KI-Antworten, eigene Testdatenbank).

    python scripts/measure_speed.py [--profile]

Ausgabe: Sekunden je Phase (Vorbereitung, Ausführung inkl. Sicherung und Prüfung, Freigabe-/Programmsuche).
Verändert keine echten Dateien und startet ein eigenes, unsichtbares Access.
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import AutoInteraction
from app.ai import prompts
from app.tasks.models import CapturedTask
from app.utils.sampledb import create_sample_database
from app.vision import tecomp
from tests.helpers import build_harness, solution

QUERY = {"action": "create_query", "name": "qryMessung", "sql": "SELECT Nachname, Ort FROM Kunden ORDER BY Nachname"}


def timed(label: str, func, results: dict[str, float]):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    value = func()
    results[label] = time.perf_counter() - start
    return value


def main() -> None:
    results: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        db = create_sample_database(Path(tmp) / "messung.accdb")
        h = build_harness(db, [solution([QUERY]), solution([QUERY | {"name": "qryMessung2"}])], Path(tmp))
        task = CapturedTask(text="Erstellen Sie eine Abfrage mit Nachname und Ort.", method="manual")
        try:
            for n in (1, 2):
                prepared = timed(f"Vorbereitung {n} (Lesen, Struktur, Plan prüfen)",
                                 lambda: h.pipeline.prepare(task), results)
                if n == 1:  # dieselbe unveränderte Aufgabe noch einmal (z. B. Alt+W nach einem Halt)
                    timed("Vorbereitung 1 wiederholt (unverändert)", lambda: h.pipeline.prepare(task), results)
                outcome = timed(f"Ausführung {n} (Sicherung, Schritte, Prüfung)",
                                lambda p=prepared: h.pipeline.execute(p, AutoInteraction()), results)
                assert outcome.status == "erfolgreich", outcome.error
            analyses = sum(r.system == prompts.ANALYSIS_SYSTEM for r in h.provider.requests)
            timed("Arbeitsordner inkl. W: (10×)", lambda: [tecomp.workdirs() for _ in range(10)], results)
        finally:
            h.close()
    width = max(map(len, results))
    for label, seconds in results.items():
        print(f"{label:<{width}}  {seconds:7.3f} s")
    print(f"KI-Analysen für 3 Vorbereitungen: {analyses}")


if __name__ == "__main__":
    if "--profile" in sys.argv:
        cProfile.run("main()", sort="cumulative", filename=str(Path(tempfile.gettempdir()) / "wids-profile.out"))
        pstats.Stats(str(Path(tempfile.gettempdir()) / "wids-profile.out")).sort_stats("cumulative").print_stats(35)
    else:
        main()

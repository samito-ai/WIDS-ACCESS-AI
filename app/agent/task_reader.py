"""Task Reader: erfasst Aufgabenstellungen aus Text, Bild oder dem Aufgabenfenster."""
from __future__ import annotations

import os
from pathlib import Path

from app.ai import prompts
from app.ai.base import ChatMessage, ImageInput
from app.ai.manager import ProviderManager
from app.core.errors import CaptureError, OcrUnavailableError, ProviderError
from app.core.logging_setup import get_logger
from app.core.settings import AppSettings
from app.tasks.models import CapturedTask
from app.vision import capture, tecomp

log = get_logger(__name__)


class TaskReader:
    def __init__(self, settings: AppSettings, ai: ProviderManager) -> None:
        self._settings = settings
        self._ai = ai

    def _workshop(self, db_path: str) -> Path | None:
        """Workshop-Ordner: aus der im Dashboard bestätigten Zuordnung dieser Datei, sonst eindeutig automatisch –
        automatisch nur für te.comp-Dateien im Arbeitsordner (eine eigene gleichnamige Datei bekommt keinen Kontext)."""
        from app.security import programs

        assigned = self._settings.security.program_assignments.get(programs.file_key(db_path), "")
        base = tecomp.install_dir()
        if assigned.startswith(programs.WORKSHOP_PREFIX) and base is not None:
            folder = base / "access" / "workshops" / assigned[len(programs.WORKSHOP_PREFIX):]
            return folder if folder.is_dir() else None
        return tecomp.workshop_for(db_path) if tecomp.in_tecomp_workdir(db_path) else None

    @staticmethod
    def from_text(text: str) -> CapturedTask:
        if not text.strip():
            raise CaptureError("Bitte eine Aufgabenstellung eingeben.")
        return CapturedTask(text=text.strip(), method="manual")

    def from_image(self, path: str | Path) -> CapturedTask:
        data = Path(path).read_bytes()
        return self._from_png([data], method="image", source=str(path), allow_vision=True)

    def from_window(self) -> CapturedTask:
        """Liest die aktuelle Aufgabe.

        1. te.comp-Access-Übung geöffnet: Aufgabenseite von te.comp (vollständiger Originaltext).
        2. Sonst: Bild des te.comp- bzw. Vordergrundfensters per OCR. Das Bild eines fremden Fensters
           wird nur lokal gelesen und nie an die KI gesendet.
        """
        exercise = tecomp.open_exercise()
        if exercise is not None:
            db_path, title = exercise
            page = tecomp.task_page(title)
            if page is not None:
                text = tecomp.html_to_text(page.read_text(encoding="utf-8", errors="replace"))
                if len(text) >= 5:
                    images: list[bytes] = []
                    try:
                        images = tecomp.task_images(page)
                    except Exception as exc:  # Abbildungen sind eine Ergänzung – ohne sie geht es trotzdem weiter
                        log.warning("Abbildungen der Aufgabenseite nicht lesbar: %s", exc)
                    return CapturedTask(text=text, method="tecomp", source=capture.TECOMP_PROCESS,
                                        window_title=title, db_path=db_path, images=images)
        windows = capture.find_task_windows(own_pid=os.getpid())
        is_tecomp = windows[0].process.lower() == capture.TECOMP_PROCESS
        task = self._from_png([capture.grab(w) for w in windows], method="ocr", source=windows[0].process,
                              allow_vision=is_tecomp)
        task.window_title = windows[0].title
        # Datenbank der Aufgabe: te.comp-Übung, sonst die in Access geöffnete Datei – egal wie sie heißt (Workshops,
        # eigene Übungsdatenbanken)
        task.db_path = exercise[0] if exercise is not None else (tecomp.open_access_database() if is_tecomp else "")
        workshop = self._workshop(task.db_path) if is_tecomp and task.db_path and exercise is None else None
        if workshop is not None and (context := tecomp.workshop_context(workshop)):
            task.text += (f"\n\nHINTERGRUND DES TE.COMP-WORKSHOPS „{workshop.name}“ (aus den Workshop-Dateien – nur zum "
                          f"Verständnis; bearbeite ausschließlich die oben gezeigte Aufgabe):\n{context}")
        return task

    def _from_png(self, pngs: list[bytes], *, method: str, source: str, allow_vision: bool) -> CapturedTask:
        warnings: list[str] = []
        text = ""
        try:
            text = "\n".join(t for t in (capture.ocr(png, self._settings.capture.ocr_language) for png in pngs) if t)
        except OcrUnavailableError as exc:
            warnings.append(exc.user_message)
        png = max(pngs, key=len)  # für die KI-Bilderkennung das inhaltsreichste Bild
        # OCR liefert zu wenig -> multimodales KI-Modell als Rückfall
        if (allow_vision and len(text) < self._settings.capture.min_text_length
                and self._settings.ai.use_vision and self._ai.is_configured()):
            try:
                answer = self._ai.complete(
                    system=prompts.VISION_SYSTEM,
                    messages=[ChatMessage(role="user", content="Lies die Aufgabenstellung.", images=[ImageInput(data=png)])],
                    purpose="vision",
                ).text.strip()
                if answer and "KEINE_AUFGABE" not in answer:
                    text, method = answer, "vision"
            except ProviderError as exc:
                warnings.append(f"KI-Bilderkennung nicht möglich: {exc.user_message}")
        if len(text) < 5:
            raise CaptureError(
                "Es wurde kein Aufgabentext erkannt. Bitte die Aufgabe als Text einfügen. " + " ".join(warnings)
            )
        return CapturedTask(text=text, method=method, source=source, warnings=warnings)

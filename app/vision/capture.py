"""Fenstererkennung, Screenshot und Texterkennung (Windows-OCR)."""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from app.core.errors import CaptureError, OcrUnavailableError

TECOMP_PROCESS = "tecomptraining.exe"
#: te.comp-Aufgabenleiste und eingebettete Aufgabenseite (beide eigengezeichnet – liefern keinen Text, daher OCR)
TECOMP_TASK_CLASSES = ("TForm_Main", "TFormChromeWeb")


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    process: str
    rect: tuple[int, int, int, int]


def visible_windows() -> list[WindowInfo]:
    import psutil
    import win32gui
    import win32process

    result: list[WindowInfo] = []

    def collect(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid).name()
        except Exception:
            process = ""
        result.append(
            WindowInfo(hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), process,
                       tuple(win32gui.GetWindowRect(hwnd)))  # type: ignore[arg-type]
        )
        return True

    win32gui.EnumWindows(collect, None)
    return result


def environment() -> tuple[list[str], list[str]]:
    """(sichtbare Fenstertitel, laufende Prozessnamen) für den Prüfungsschutz."""
    import psutil

    from app.security.policy import EXAM_ENVIRONMENT_TITLES

    # Schlüsselwörter nur in te.comp-/Access-Fenstern (ein fremder Tab mit „Prüfung“ im Titel soll nichts sperren);
    # echte Prüfungsumgebungen (Next Exam, Safe Exam Browser) aber in JEDEM Fenster, auch im Browser
    titles = [w.title for w in visible_windows() if w.title and (
        w.process.lower() in (TECOMP_PROCESS, "msaccess.exe")
        or any(marker in w.title.lower() for marker in EXAM_ENVIRONMENT_TITLES))]
    return titles, [p.info["name"] or "" for p in psutil.process_iter(["name"])]


def find_task_windows(own_pid: int | None = None) -> list[WindowInfo]:
    """te.comp-Aufgabenleiste und -Aufgabenseite, falls te.comp läuft – sonst das Fenster im Vordergrund."""
    import win32gui
    import win32process

    windows = visible_windows()
    tecomp = [w for w in windows if w.process.lower() == TECOMP_PROCESS and w.cls in TECOMP_TASK_CLASSES]
    if tecomp:
        return tecomp
    foreground = win32gui.GetForegroundWindow()
    for window in windows:
        if window.hwnd == foreground:
            if own_pid is not None and win32process.GetWindowThreadProcessId(window.hwnd)[1] == own_pid:
                break
            return [window]
    raise CaptureError("Kein Aufgabenfenster gefunden. Bitte te.comp öffnen oder das Fenster mit der Aufgabe anklicken.")


def grab(window: WindowInfo) -> bytes:
    """Bild genau dieses Fensters als PNG – auch wenn andere Fenster davor liegen.

    Bewusst kein Bildschirmausschnitt: der würde verdeckende Fenster (z. B. den Browser) mitfotografieren.
    """
    import ctypes

    import win32gui
    import win32ui
    from PIL import Image

    if win32gui.IsIconic(window.hwnd):
        raise CaptureError("Das Aufgabenfenster ist minimiert.")
    left, top, right, bottom = win32gui.GetWindowRect(window.hwnd)
    width, height = right - left, bottom - top
    if width < 20 or height < 20:
        raise CaptureError("Das Aufgabenfenster ist zu klein.")
    window_dc = win32gui.GetWindowDC(window.hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        ok = ctypes.windll.user32.PrintWindow(window.hwnd, memory.GetSafeHdc(), 2)  # 2 = PW_RENDERFULLCONTENT
        data = bitmap.GetBitmapBits(True)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(window.hwnd, window_dc)
    image = Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1)
    if not ok or image.getextrema() == ((0, 0), (0, 0), (0, 0)):
        raise CaptureError("Der Inhalt des Aufgabenfensters konnte nicht gelesen werden.")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _ocr(png: bytes, language: str) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_language(Language(language))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrUnavailableError()
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


def ocr(png: bytes, language: str = "de") -> str:
    """Texterkennung mit der in Windows eingebauten OCR. Kleine Schrift wird vorher vergrößert."""
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    if image.height < 1500:  # Bildschirmschrift ist für die OCR oft zu klein
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        return asyncio.run(_ocr(buffer.getvalue(), language)).strip()
    except OcrUnavailableError:
        raise
    except ImportError as exc:
        raise OcrUnavailableError(detail=str(exc)) from exc
    except Exception as exc:
        raise CaptureError(f"Texterkennung fehlgeschlagen: {exc}") from exc

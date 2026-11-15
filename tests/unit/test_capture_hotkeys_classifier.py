"""Unit-Tests: OCR, Aufgabenleser, Tastenkürzel, Klassifikation, JSON-Extraktion, Geheimnisse."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.agent.task_reader import TaskReader
from app.ai.jsonutil import extract_json_object
from app.ai.manager import ProviderManager
from app.automation.hotkeys import HotkeyManager, parse
from app.core.errors import (
    CaptureError,
    CostLimitError,
    HotkeyError,
    ProviderRateLimitError,
    ResponseFormatError,
)
from app.core.logging_setup import redact, register_secret
from app.core.settings import AppSettings
from app.security.policy import SafetyPolicy
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage
from app.tasks.classifier import classify_task
from app.vision import capture
from tests.helpers import ScriptedProvider


def _text_png(text: str) -> bytes:
    image = Image.new("RGB", (1100, 160), "white")
    ImageDraw.Draw(image).text((20, 50), text, fill="black", font=ImageFont.truetype("arial.ttf", 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_windows_ocr_reads_german_task_text(tmp_path: Path) -> None:
    from app.core.errors import OcrUnavailableError

    png = _text_png("Erstellen Sie eine Abfrage qryKunden mit dem Feld Nachname.")
    try:
        text = capture.ocr(png)
    except OcrUnavailableError as exc:  # z. B. GitHub-Runner ohne deutsche Windows-Texterkennung
        pytest.skip(f"Windows-OCR nicht verfügbar: {exc.user_message}")
    assert "Abfrage" in text and "Nachname" in text
    file = tmp_path / "aufgabe.png"
    file.write_bytes(png)
    reader = TaskReader(AppSettings(), ProviderManager(AppSettings(), SecretStore(MemoryBackend())))
    task = reader.from_image(file)
    assert task.method == "image" and "Abfrage" in task.text


def test_empty_image_gives_clear_error(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buffer, format="PNG")
    file = tmp_path / "leer.png"
    file.write_bytes(buffer.getvalue())
    reader = TaskReader(AppSettings(), ProviderManager(AppSettings(), SecretStore(MemoryBackend())))
    with pytest.raises(CaptureError):
        reader.from_image(file)
    with pytest.raises(CaptureError):
        reader.from_text("   ")


def test_window_enumeration_and_environment() -> None:
    titles, processes = capture.environment()
    assert processes and all(isinstance(t, str) for t in titles)


@pytest.mark.parametrize(
    ("combo", "expected"),
    [("Alt+W", (1, ord("W"))), ("strg+umschalt+f5", (2 | 4, 0x74)), ("F9", (0, 0x78)), ("Ctrl+Alt+S", (3, ord("S"))),
     ("Ctrl+Alt+D", (3, ord("D"))), ("Ctrl+Alt+X", (3, ord("X"))), ("Meta+F2", (8, 0x71))],
)
def test_hotkey_parse(combo: str, expected: tuple[int, int]) -> None:
    assert parse(combo) == expected


def test_new_default_hotkeys_and_migration() -> None:
    from app.core.settings import HotkeySettings

    defaults = HotkeySettings()
    assert (defaults.solve, defaults.settings, defaults.dashboard, defaults.cancel) == \
        ("Alt+W", "Ctrl+Alt+S", "Ctrl+Alt+D", "Ctrl+Alt+X")
    assert HotkeySettings(cancel="Esc").cancel == "Ctrl+Alt+X", "alte Voreinstellung Esc wird ersetzt"
    parsed = [parse(getattr(defaults, n)) for n in ("solve", "analyze", "ask", "status", "settings", "dashboard", "cancel")]
    assert len(set(parsed)) == len(parsed), "Voreinstellungen ohne Doppelbelegung"


# Strg+Alt+Q/E/M/2/3/7/8/9/0 = AltGr-Zeichen der deutschen Tastatur (@, €, µ, ², ³, {, [, ], })
@pytest.mark.parametrize("combo", ["", "W", "Alt+F4", "Ctrl+C", "Foo+W", "Alt+ß§", "Ctrl+Alt+Q", "Ctrl+Alt+E", "Strg+Alt+7",
                                   "Esc", "Space", "Enter", "Tab", "Meta+D", "Alt+Escape", "Ctrl+Strg+S"])  # Codex-Agent
def test_hotkey_parse_rejects(combo: str) -> None:
    with pytest.raises(HotkeyError):
        parse(combo)


def test_hotkey_registration_and_conflicts() -> None:
    first = HotkeyManager(lambda name: None)
    second = HotkeyManager(lambda name: None)
    try:
        assert first.start({"a": "Ctrl+Alt+Shift+F11", "b": "Ctrl+Alt+Shift+F11", "c": "Alt+F4"}).keys() == {"b", "c"}
        failed = second.start({"x": "Ctrl+Alt+Shift+F11"})
        assert "anderen Programm" in failed["x"]
        first.stop()
        assert second.start({"x": "Ctrl+Alt+Shift+F11"}) == {}
    finally:
        first.stop()
        second.stop()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Erstellen Sie eine Abfrage, die alle Kunden aus Tirol zeigt, sortiert nach Nachname.", "select_query"),
        ("Erstellen Sie eine Abfrage mit der Summe der Umsätze je Bundesland (Gruppierung).", "aggregate_query"),
        ("Erstellen Sie eine Parameterabfrage, die beim Ausführen nach dem Ort fragt.", "parameter_query"),
        ("Erstellen Sie eine Beziehung zwischen Kunden und Bestellungen mit referenzieller Integrität.", "relationship"),
        ("Erstellen Sie einen Bericht, gruppiert nach Kategorie, mit der Summe des Lagerbestands.", "report"),
        ("Erstellen Sie ein Formular für die Tabelle Kunden mit einem Kombinationsfeld.", "form"),
        ("Legen Sie in der Tabelle Kunden das Feld KundenNr als Primärschlüssel fest.", "primary_key"),
        ("Ändern Sie den Datentyp des Feldes PLZ auf Kurzer Text.", "datatype"),
        ("Fügen Sie einen neuen Datensatz in die Tabelle Artikel ein.", "data_editing"),
        ("Was versteht man unter einem Primärschlüssel?", "theory"),
        ("Welche Aussage ist richtig?\nA) Ein Feld\nB) Ein Datensatz\nC) Eine Tabelle", "multiple_choice"),
        ("", "unknown"),
    ],
)
def test_classifier(text: str, expected: str) -> None:
    assert classify_task(text).task_type.value == expected


def test_json_extraction_variants() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('Hier:\n```json\n{"a": {"b": "}"}}\n```\nFertig') == {"a": {"b": "}"}}
    assert extract_json_object('Text {"a": [1, 2,], } Ende') == {"a": [1, 2]}
    for bad in ("", "kein json", "[1, 2]", "{unvollständig"):
        with pytest.raises(ResponseFormatError):
            extract_json_object(bad)


def test_secrets_are_never_logged_or_stored_in_sqlite(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = SecretStore(MemoryBackend())
    key = "sk-ant-api03-GEHEIM1234567890abcdef"
    store.set_api_key("anthropic", key)
    assert store.get_api_key("anthropic") == key and store.has_api_key("anthropic")
    assert SecretStore.mask(key).startswith("sk-a") and "GEHEIM" not in SecretStore.mask(key)
    assert key not in redact(f"Authorization: Bearer {key}") and "GEHEIM" not in redact(f"key={key}")
    register_secret("ganz-geheimes-token")
    assert redact("x ganz-geheimes-token y") == "x *** y"
    storage = Storage(tmp_path / "s.sqlite3")
    storage.save_settings(AppSettings())
    storage.close()
    assert b"GEHEIM" not in (tmp_path / "s.sqlite3").read_bytes()
    with caplog.at_level(logging.INFO):
        store.set_api_key("openai", "sk-proj-ABCDEFGHIJKLMNOPQRST")
    assert "ABCDEFGH" not in caplog.text


def test_provider_retry_backoff_and_cost_limit(tmp_path: Path) -> None:
    settings = AppSettings()
    storage = Storage(tmp_path / "u.sqlite3")
    sleeps: list[float] = []
    manager = ProviderManager(settings, SecretStore(MemoryBackend()), storage, sleep=sleeps.append)
    provider = ScriptedProvider([ProviderRateLimitError(retry_after=7), ProviderRateLimitError(), "ok"])
    manager.set_provider_for_testing("anthropic", provider)
    from app.ai.base import ChatMessage

    assert manager.complete(messages=[ChatMessage(role="user", content="hi")]).text == "ok"
    assert len(sleeps) == 2 and sleeps[0] >= 7 and sleeps[1] >= 2
    usage = storage.usage_summary()
    # 1000 Eingabe- und 500 Ausgabe-Tokens mit claude-opus-5-5: 0,004 $ + 0,010 $
    assert usage.requests == 1 and usage.cost_usd == pytest.approx(0.014)
    settings.ai.cost_limit_usd = 0.01
    with pytest.raises(CostLimitError):
        manager.complete(messages=[ChatMessage(role="user", content="hi")])
    assert len(provider.requests) == 3, "bei erreichtem Limit wird nichts gesendet"
    storage.close()


def test_exam_policy() -> None:
    policy = SafetyPolicy(AppSettings().security)
    assert not policy.check_environment(["te.comp-training", "Automatische Prüfung", "Mein Test.docx"], ["a.exe"]).blocked
    # „Schularbeit“ bezeichnet im Unterricht auch Übungsdatenbanken – kein Sperrgrund mehr; echte Prüfungen laufen in
    # Next Exam/Sperrbrowsern und werden dort erkannt (test_program_approval.py)
    assert not policy.check_environment(["Schularbeit Access"], []).blocked
    assert policy.check_environment(["Prüfungsmodus Access"], []).blocked
    assert policy.check_environment([], ["LockDownBrowser.exe"]).blocked
    assert not policy.task_text_warnings("Schularbeit: Erstellen Sie ...")
    assert policy.task_text_warnings("Abschlussprüfung: Erstellen Sie ...")
    assert policy.validate_target("C:/x/datei.txt")

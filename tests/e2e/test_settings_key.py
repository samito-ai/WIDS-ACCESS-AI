"""GUI: Schlüssel eingeben → „Verbindung testen“ (ohne vorher „Schlüssel speichern“) → Neustart → wiederverwenden.

Genau dieser Ablauf führte früher zu „kein API-Schlüssel hinterlegt“: Der Test-Knopf ignorierte die Eingabe.
Verwendet die echte Windows-Anmeldeinformationsverwaltung mit eigenem Testeintrag und einen Testschlüssel.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from PySide6.QtWidgets import QMessageBox

from app.ai import manager as manager_module
from app.ai.base import AIProvider, AIRequest, AIResponse
from app.core.context import AppContext
from app.core.errors import ProviderAuthError
from app.security.secrets import CredentialManagerBackend, SecretStore
from app.storage.database import Storage
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.gui
PREFIX = "WIDS-Access-AI-PYTEST-GUI"
KEY = "sk-ant-api03-GUITESTSCHLUESSEL-0123456789abcdefghij"


class KeyCheckingProvider(AIProvider):
    """Antwortet nur, wenn er mit dem erwarteten Schlüssel erzeugt wurde – wie die echte API."""

    name = "anthropic"
    keys: ClassVar[list[str]] = []

    def __init__(self, api_key: str, timeout_s: float) -> None:
        self.keys.append(api_key)
        self.api_key = api_key

    def complete(self, request: AIRequest) -> AIResponse:
        if self.api_key != KEY:
            raise ProviderAuthError()
        return AIResponse(text="OK", provider="anthropic", model=request.model)

    def list_models(self) -> list[str]:
        return ["claude-opus-5-5", "claude-haiku-5-5"]


@pytest.fixture
def make_window(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(manager_module.PROVIDER_FACTORIES, "anthropic", KeyCheckingProvider)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    KeyCheckingProvider.keys = []
    windows: list[MainWindow] = []

    def factory() -> MainWindow:
        ctx = AppContext(Storage(tmp_path / "s.sqlite3"), SecretStore(CredentialManagerBackend(prefix=PREFIX)))
        ctx.settings.hotkeys.enabled = False
        window = MainWindow(ctx)
        qtbot.addWidget(window)
        window.show()
        window.nav.setCurrentRow(5)
        windows.append(window)
        return window

    yield factory
    for window in windows:
        window.close()
        window.ctx.shutdown()
    CredentialManagerBackend(prefix=PREFIX).delete("api-key-anthropic")


def test_enter_key_test_connection_restart_and_reuse(make_window: Any, qtbot: Any) -> None:
    first = make_window()
    assert first.set_key_state.text() == "Kein Schlüssel gespeichert."
    first.set_key.setText(KEY + "\n")
    first.test_connection()  # ohne vorher „Schlüssel speichern“
    qtbot.waitUntil(lambda: "verbunden" in first.set_key_state.text(), timeout=10000)
    assert first.set_key_state.text() == "Anthropic Claude verbunden. Getestetes Modell: claude-opus-5-5"
    assert first.set_key.text() == "" and "gespeichert: sk-a" in first.set_key.placeholderText()
    assert CredentialManagerBackend(prefix=PREFIX).get("api-key-anthropic") == KEY, "dauerhaft gespeichert"
    assert "Schlüssel hinterlegt" in first.dash["ai"].text()
    first.close()

    second = make_window()  # Neustart: neuer Kontext, neue Schlüsselablage
    assert second.set_key_state.text().startswith("Gespeicherter Schlüssel: sk-a")
    assert KEY not in second.set_key_state.text() and second.set_key.text() == ""
    second.test_connection()
    qtbot.waitUntil(lambda: "verbunden" in second.set_key_state.text(), timeout=10000)
    assert KeyCheckingProvider.keys == [KEY, KEY], "beide Male wurde der gespeicherte Schlüssel verwendet"
    second.nav.setCurrentRow(0)
    second.nav.setCurrentRow(5)  # Seitenwechsel ändert nichts
    assert second.set_key_state.text().startswith("Anthropic Claude verbunden")


def test_invalid_key_is_reported_and_can_be_replaced(make_window: Any, qtbot: Any) -> None:
    window = make_window()
    window.set_key.setText("sk-ant-api03-FALSCH-000000000000000000")
    window.test_connection()
    qtbot.waitUntil(lambda: "fehlgeschlagen" in window.set_key_state.text(), timeout=10000)
    assert "abgelehnt" in window.set_key_state.text() and "verbunden" not in window.set_key_state.text()
    window.set_key.setText(KEY)  # sicher ersetzen
    window.save_settings()
    assert CredentialManagerBackend(prefix=PREFIX).get("api-key-anthropic") == KEY
    window.test_connection()
    qtbot.waitUntil(lambda: "verbunden" in window.set_key_state.text(), timeout=10000)
    window.delete_key()
    assert window.set_key_state.text() == "Kein Schlüssel gespeichert."
    assert CredentialManagerBackend(prefix=PREFIX).get("api-key-anthropic") is None

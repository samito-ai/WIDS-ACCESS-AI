"""API-Schlüssel: echte Windows-Anmeldeinformationsverwaltung, Neustart, Ersetzen, Löschen, Fehlerarten.

Alle Tests verwenden Testschlüssel und einen eigenen Eintrag (``WIDS-Access-AI-PYTEST/…``) – der echte
Eintrag des Programms wird nicht berührt.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.base import AIProvider, AIRequest, AIResponse
from app.ai.manager import ProviderManager
from app.core import errors
from app.core.settings import AppSettings
from app.security.secrets import CredentialManagerBackend, SecretStore

TEST_PREFIX = "WIDS-Access-AI-PYTEST"
KEY_1 = "sk-ant-api03-TESTSCHLUESSEL-eins-0123456789abcdef"
KEY_2 = "sk-ant-api03-TESTSCHLUESSEL-zwei-fedcba9876543210"


@pytest.fixture
def store() -> Iterator[SecretStore]:
    backend = CredentialManagerBackend(prefix=TEST_PREFIX)
    yield SecretStore(backend)
    for provider in ("anthropic", "openai"):
        backend.delete(f"api-key-{provider}")


def test_save_load_restart_replace_delete(store: SecretStore) -> None:
    assert store.get_api_key("anthropic") is None
    # 1. speichern (mit beim Einfügen mitkopierten Leerzeichen/Zeilenumbruch/Anführungszeichen)
    assert store.set_api_key("anthropic", f'  "{KEY_1}"\r\n') == KEY_1
    # 2./3./4. „Neustart“: völlig neue Objekte lesen denselben Eintrag aus dem Credential Manager
    restarted = SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX))
    assert restarted.backend_name == "Windows-Anmeldeinformationsverwaltung"
    assert restarted.get_api_key("anthropic") == KEY_1
    manager = ProviderManager(AppSettings(), restarted)
    assert manager.is_configured("anthropic") and manager.api_key("anthropic") == KEY_1
    # 5. ersetzen
    restarted.set_api_key("anthropic", KEY_2)
    assert SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX)).get_api_key("anthropic") == KEY_2
    # 6. löschen
    restarted.delete_api_key("anthropic")
    assert SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX)).get_api_key("anthropic") is None
    with pytest.raises(errors.WidsError):
        restarted.set_api_key("anthropic", "   ")


def test_openai_key_is_optional(store: SecretStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store.set_api_key("anthropic", KEY_1)
    manager = ProviderManager(AppSettings(), store)
    assert manager.is_configured("anthropic") and not manager.is_configured("openai")
    # 7. der Anthropic-Provider wird mit genau dem gespeicherten Schlüssel erzeugt
    provider = manager.provider()
    assert isinstance(provider, AnthropicProvider) and provider._client.api_key == KEY_1
    assert manager.model_for("frage") == "claude-opus-5-5"
    manager.settings.ai.auto_model_selection = True
    assert manager.model_for("frage") == "claude-haiku-5-5" and manager.model_for("analyse") == "claude-opus-5-5"


def test_missing_key_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manager = ProviderManager(AppSettings(), SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX)))
    with pytest.raises(errors.ProviderNotConfiguredError, match="kein API-Schlüssel"):
        manager.test_connection("anthropic")


class _FakeProvider(AIProvider):
    name = "anthropic"

    def __init__(self, models: list[str]) -> None:
        self.models = models
        self.requests: list[AIRequest] = []

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        return AIResponse(text="OK", provider="anthropic", model=request.model)

    def list_models(self) -> list[str]:
        return self.models


def test_connection_test_sends_real_request_and_reports_model() -> None:
    # 8. der Verbindungstest schickt eine echte Anfrage an das eingestellte Modell
    manager = ProviderManager(AppSettings(), SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX)))
    fake = _FakeProvider(["claude-opus-5-5", "claude-haiku-5-5"])
    manager.set_provider_for_testing("anthropic", fake)
    check = manager.test_connection("anthropic")
    assert [r.model for r in fake.requests] == ["claude-opus-5-5"]
    assert check.message() == "Anthropic Claude verbunden. Getestetes Modell: claude-opus-5-5"
    fake.models = ["claude-opus-5-5"]
    assert "nicht verfügbar: claude-haiku-5-5" in manager.test_connection("anthropic").message()


def _status_error(cls: Any, code: int, message: str) -> Any:
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (lambda sdk: _status_error(sdk.AuthenticationError, 401, "invalid x-api-key"), errors.ProviderAuthError),
        (lambda sdk: _status_error(sdk.AuthenticationError, 401, "API key has expired"), errors.ProviderKeyExpiredError),
        (lambda sdk: _status_error(sdk.PermissionDeniedError, 403, "not allowed in this workspace"), errors.ProviderPermissionError),
        (lambda sdk: _status_error(sdk.BadRequestError, 400, "Your credit balance is too low"), errors.ProviderBillingError),
        (lambda sdk: _status_error(sdk.NotFoundError, 404, "model: claude-xyz"), errors.ProviderModelError),
        (lambda sdk: _status_error(sdk.RateLimitError, 429, "rate limit"), errors.ProviderRateLimitError),
        (lambda sdk: _status_error(sdk.InternalServerError, 500, "boom"), errors.ProviderServerError),
        (lambda sdk: _status_error(sdk.APIStatusError, 529, "overloaded"), errors.ProviderServerError),
        (lambda sdk: sdk.APIConnectionError(request=__import__("httpx2").Request("GET", "https://x")), errors.ProviderNetworkError),
        (lambda sdk: sdk.APITimeoutError(request=__import__("httpx2").Request("GET", "https://x")), errors.ProviderTimeoutError),
    ],
)
def test_anthropic_error_kinds_are_distinguished(factory: Any, expected: type) -> None:
    import anthropic

    provider = AnthropicProvider("sk-ant-test")
    error = provider._translate(factory(anthropic))
    assert type(error) is expected
    assert error.user_message and "sk-ant" not in error.user_message


def test_failed_connection_is_never_reported_as_success() -> None:
    class Failing(_FakeProvider):
        def complete(self, request: AIRequest) -> AIResponse:
            raise errors.ProviderAuthError()

    manager = ProviderManager(AppSettings(), SecretStore(CredentialManagerBackend(prefix=TEST_PREFIX)))
    manager.set_provider_for_testing("anthropic", Failing(["claude-opus-5-5"]))
    with pytest.raises(errors.ProviderAuthError):
        manager.test_connection("anthropic")

"""AI Provider Manager: Anbieterwahl, Wiederholungen, Tokenverbrauch, Kostenlimits, JSON-Validierung."""
from __future__ import annotations

import os
import random
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.base import AIProvider, AIRequest, AIResponse, ChatMessage
from app.ai.jsonutil import extract_json_object, summarize_validation_errors
from app.core.errors import (
    CostLimitError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ResponseFormatError,
)
from app.core.logging_setup import get_logger
from app.core.settings import AppSettings
from app.security.secrets import SecretStore
from app.storage.database import Storage, UsageSummary

log = get_logger(__name__)
M = TypeVar("M", bound=BaseModel)

ProviderFactory = Callable[[str, float], AIProvider]
Purpose = Literal["analyse", "frage", "vision", "reparatur", "erklaerung", "test"]


def _anthropic_factory(api_key: str, timeout_s: float) -> AIProvider:
    from app.ai.anthropic_provider import AnthropicProvider

    return AnthropicProvider(api_key, timeout_s=timeout_s)


def _openai_factory(api_key: str, timeout_s: float) -> AIProvider:
    from app.ai.openai_provider import OpenAIProvider

    return OpenAIProvider(api_key, timeout_s=timeout_s)


#: Registrierung der verfügbaren Anbieter – hier wird ein weiterer Anbieter ergänzt.
PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "anthropic": _anthropic_factory,
    "openai": _openai_factory,
}
PROVIDER_LABELS: dict[str, str] = {"anthropic": "Anthropic Claude", "openai": "OpenAI"}


class ConnectionCheck(BaseModel):
    provider: str
    #: Modell, das auf die Testanfrage tatsächlich geantwortet hat
    model: str
    #: Eingestellte Modelle (Haupt-/Schnellmodell), die der Schlüssel laut API nicht nutzen kann
    unavailable: list[str] = []

    def message(self) -> str:
        text = f"{PROVIDER_LABELS.get(self.provider, self.provider)} verbunden. Getestetes Modell: {self.model}"
        if self.unavailable:
            text += f" – nicht verfügbar: {', '.join(self.unavailable)} (bitte anderes Modell eintragen)"
        return text
_ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class ProviderManager:
    def __init__(
        self,
        settings: AppSettings,
        secrets: SecretStore,
        storage: Storage | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._secrets = secrets
        self._storage = storage
        self._sleep = sleep
        self._providers: dict[str, AIProvider] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ Anbieter
    #: Aufgabe, der die nächsten Anfragen zugerechnet werden (Kosten je Aufgabe, Aufgabenbudget); None = keine
    task_id: int | None = None

    def set_provider_for_testing(self, name: str, provider: AIProvider) -> None:
        self._providers[name] = provider

    def api_key(self, provider: str) -> str | None:
        return self._secrets.get_api_key(provider) or os.environ.get(_ENV_KEYS.get(provider, ""), "") or None

    def is_configured(self, provider: str | None = None) -> bool:
        name = provider or self.settings.ai.provider
        return name in self._providers or bool(self.api_key(name))

    def reset(self, provider: str | None = None) -> None:
        """Verwirft zwischengespeicherte Clients (nach Schlüssel- oder Einstellungsänderung)."""
        with self._lock:
            if provider:
                self._providers.pop(provider, None)
            else:
                self._providers.clear()

    def provider(self, name: str | None = None) -> AIProvider:
        name = name or self.settings.ai.provider
        with self._lock:
            if name in self._providers:
                return self._providers[name]
            if name not in PROVIDER_FACTORIES:
                raise ProviderError(f"Unbekannter KI-Anbieter: {name}")
            key = self.api_key(name)
            if not key:
                raise ProviderNotConfiguredError(
                    f"Für {PROVIDER_LABELS.get(name, name)} ist kein API-Schlüssel hinterlegt. "
                    "Bitte in den Einstellungen eintragen."
                )
            provider = PROVIDER_FACTORIES[name](key, self.settings.ai.request_timeout_s)
            self._providers[name] = provider
            return provider

    def model_for(self, purpose: Purpose) -> str:
        """Automatische Modellwahl: einfache Schritte dürfen das schnelle Modell nutzen."""
        if self.settings.ai.auto_model_selection and purpose in ("frage", "vision", "erklaerung"):
            return self.settings.fast_model()
        return self.settings.active_model()

    def test_connection(self, provider: str | None = None) -> ConnectionCheck:
        """Echte Anfrage an das eingestellte Modell; prüft zusätzlich, ob das schnelle Modell verfügbar ist.

        Wirft bei jedem Fehler – ein Erfolg wird nur gemeldet, wenn das Modell tatsächlich geantwortet hat.
        """
        name = provider or self.settings.ai.provider
        client = self.provider(name)
        main = self.settings.ai.anthropic_model if name == "anthropic" else self.settings.ai.openai_model
        fast = self.settings.ai.anthropic_fast_model if name == "anthropic" else self.settings.ai.openai_fast_model
        answered = client.test_connection(main)
        available = set(client.list_models())
        return ConnectionCheck(provider=name, model=answered,
                               unavailable=[m for m in (main, fast) if m and m not in available])

    # ------------------------------------------------------------------ Kosten
    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int, *,
                      cache_read: int = 0, cache_write: int = 0) -> float | None:
        """Kosten in $; ``input_tokens`` enthält die Cache-Tokens (Lesen ≈ 10 %, Schreiben ≈ 125 % des Preises)."""
        price = self.settings.ai.prices.get(model)
        if price is None:
            for known, candidate in self.settings.ai.prices.items():
                if model.startswith(known):
                    price = candidate
                    break
        if price is None:
            return None
        plain = max(input_tokens - cache_read - cache_write, 0)
        billed_input = plain + cache_read * 0.1 + cache_write * 1.25
        return (billed_input * price.input + output_tokens * price.output) / 1_000_000

    def usage(self, period: str = "month") -> UsageSummary:
        return self._storage.usage_summary(period) if self._storage else UsageSummary()

    def _check_limit(self, model: str = "") -> None:
        if self._storage is None:
            return
        task_limit = self.settings.ai.task_cost_limit_usd
        limited = (task_limit > 0 and self.task_id is not None) or self.settings.ai.cost_limit_usd > 0
        if limited and model and self.estimate_cost(model, 1, 1) is None:
            raise CostLimitError(
                f"Für das Modell „{model}“ ist kein Preis hinterlegt – das Kostenlimit ließe sich nicht einhalten. "
                "Die Anfrage wurde nicht gesendet. Bitte ein Modell mit bekanntem Preis wählen oder die Kostenlimits "
                "im Dashboard auf 0 setzen.")
        if task_limit > 0 and self.task_id is not None:
            spent_task = self._storage.usage_summary(task_id=self.task_id).cost_usd
            if spent_task >= task_limit:
                raise CostLimitError(
                    f"Das Aufgabenbudget von {task_limit:.2f} $ ist erreicht (diese Aufgabe bisher {spent_task:.2f} $). "
                    "Weitere KI-Anfragen für diese Aufgabe wurden gestoppt; bisherige Ergebnisse bleiben erhalten.")
        limit = self.settings.ai.cost_limit_usd
        if limit <= 0:
            return
        spent = self._storage.usage_summary(self.settings.ai.cost_limit_period).cost_usd
        if spent >= limit:
            raise CostLimitError(
                f"Das Kostenlimit von {limit:.2f} $ ist erreicht (bisher {spent:.2f} $). "
                "Die Anfrage wurde nicht gesendet. Das Limit lässt sich im Dashboard unter „API-Kosten“ ändern."
            )

    def _record(self, response: AIResponse, purpose: str) -> None:
        cost = self.estimate_cost(response.model, response.input_tokens, response.output_tokens,
                                  cache_read=response.cache_read_tokens, cache_write=response.cache_write_tokens)
        if self._storage is not None:
            self._storage.record_usage(
                provider=response.provider,
                model=response.model,
                purpose=purpose,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=cost,
                task_id=self.task_id,
            )

    # ------------------------------------------------------------------ Anfragen
    def complete(
        self,
        *,
        messages: list[ChatMessage],
        system: str = "",
        purpose: Purpose = "frage",
        json_mode: bool = False,
        model: str | None = None,
        max_tokens: int | None = None,
        cancel: threading.Event | None = None,
    ) -> AIResponse:
        """Sendet eine Anfrage mit Wiederholungen (exponentielles Backoff) bei vorübergehenden Fehlern."""
        provider = self.provider()
        request = AIRequest(
            system=system,
            messages=messages,
            model=model or self.model_for(purpose),
            max_tokens=max_tokens or self.settings.ai.max_output_tokens,
            json_mode=json_mode,
            timeout_s=self.settings.ai.request_timeout_s,
        )
        self._check_limit(request.model)
        attempts = max(1, self.settings.ai.max_retries + 1)
        last_error: ProviderError | None = None
        for attempt in range(1, attempts + 1):
            if cancel is not None and cancel.is_set():
                from app.core.errors import TaskCancelledError

                raise TaskCancelledError()
            try:
                response = provider.complete(request)
            except ProviderError as exc:
                if not exc.retryable or attempt == attempts:
                    log.error("KI-Anfrage fehlgeschlagen (%s): %s", type(exc).__name__, exc.detail or exc)
                    raise
                last_error = exc
                delay = min(30.0, (2 ** (attempt - 1)) + random.uniform(0, 0.5))
                if isinstance(exc, ProviderRateLimitError) and exc.retry_after:
                    delay = min(60.0, max(delay, exc.retry_after))
                log.warning(
                    "KI-Anfrage fehlgeschlagen (%s), Versuch %d/%d, warte %.1fs",
                    type(exc).__name__, attempt, attempts, delay,
                )
                self._sleep(delay)
                continue
            self._record(response, purpose)
            return response
        assert last_error is not None  # pragma: no cover
        raise last_error

    def complete_json(
        self,
        schema: type[M],
        *,
        messages: list[ChatMessage],
        system: str = "",
        purpose: Purpose = "analyse",
        max_repairs: int = 2,
        model: str | None = None,
        cancel: threading.Event | None = None,
        post_validate: Callable[[M], None] | None = None,
    ) -> M:
        """Fordert eine strukturierte Antwort an und validiert sie gegen ``schema``.

        Fehlerhaftes JSON oder Schemaverstöße werden dem Modell mit einer präzisen Fehlerliste
        zurückgegeben (höchstens ``max_repairs`` Mal). ``post_validate`` kann zusätzliche fachliche
        Prüfungen durchführen und ``ValueError`` werfen.
        """
        conversation = list(messages)
        last_problem = ""
        for round_number in range(max_repairs + 1):
            response = self.complete(
                messages=conversation,
                system=system,
                purpose=purpose if round_number == 0 else "reparatur",
                json_mode=True,
                model=model,
                cancel=cancel,
            )
            problem: str
            try:
                if response.truncated:
                    raise ResponseFormatError(
                        "Die Antwort wurde wegen der Tokenbegrenzung abgeschnitten. Antworte kürzer "
                        "(weniger Erläuterungen), aber vollständig."
                    )
                data: dict[str, Any] = extract_json_object(response.text)
                result = schema.model_validate(data)
                if post_validate is not None:
                    post_validate(result)
                return result
            except ResponseFormatError as exc:
                problem = exc.user_message
            except ValidationError as exc:
                problem = "Das JSON verletzt das Schema:\n" + summarize_validation_errors(
                    [dict(e) for e in exc.errors(include_url=False, include_input=False)]
                )
            except ValueError as exc:
                problem = str(exc)
            last_problem = problem
            log.warning("Strukturierte KI-Antwort ungültig (Runde %d): %s", round_number + 1, problem[:500])
            conversation = [
                *conversation,
                ChatMessage(role="assistant", content=response.text or "(leer)"),
                ChatMessage(
                    role="user",
                    content=(
                        "Deine Antwort konnte nicht verarbeitet werden.\n"
                        f"{problem}\n\n"
                        "Sende die vollständige, korrigierte Antwort erneut – ausschließlich als ein "
                        "gültiges JSON-Objekt im geforderten Schema."
                    ),
                ),
            ]
        raise ResponseFormatError(
            "Die KI hat auch nach mehreren Korrekturversuchen keine gültige strukturierte Antwort geliefert. "
            f"Letztes Problem: {last_problem[:400]}"
        )

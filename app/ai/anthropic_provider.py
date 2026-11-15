"""Anthropic-Claude-Anbindung über das offizielle ``anthropic``-SDK."""
from __future__ import annotations

from typing import Any

from app.ai.base import AIProvider, AIRequest, AIResponse, ChatMessage
from app.core.errors import (
    ProviderAuthError,
    ProviderBillingError,
    ProviderError,
    ProviderKeyExpiredError,
    ProviderModelError,
    ProviderNetworkError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderServerError,
    ProviderTimeoutError,
)
from app.core.logging_setup import get_logger

log = get_logger(__name__)

#: Modelle, bei denen Sicherheitsklassifikatoren eine Anfrage ablehnen können und für die die
#: API einen serverseitigen Rückfall auf ein anderes Modell anbietet (Beta ``fallbacks``).
_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _content(message: ChatMessage) -> Any:
    if not message.images:
        return message.content
    blocks: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.b64()}}
        for img in message.images
    ]
    blocks.append({"type": "text", "text": message.content})
    return blocks


class AnthropicProvider(AIProvider):
    name = "anthropic"
    display_name = "Anthropic Claude"

    def __init__(self, api_key: str, *, timeout_s: float = 120.0, client: Any = None) -> None:
        import anthropic

        self._sdk = anthropic
        # Wiederholungen übernimmt der ProviderManager (einheitlich für alle Anbieter)
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
        self._fallback_supported = True

    # ------------------------------------------------------------------ Anfrage
    def complete(self, request: AIRequest) -> AIResponse:
        system = request.system
        if request.json_mode:
            system = (
                f"{system}\n\nAntworte ausschließlich mit einem einzigen gültigen JSON-Objekt – "
                "ohne Markdown-Codeblock und ohne Text davor oder danach."
            ).strip()
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": _content(m)} for m in request.messages],
        }
        if system:
            # Die System-Prompts sind fest (Aktionsschema, Regeln) – zwischengespeichert kosten Wiederholungen ~10 %.
            # Zu kurze Prompts werden vom Server einfach nicht gecacht (kein Fehler, kein Aufpreis).
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        client = self._client.with_options(timeout=request.timeout_s)
        try:
            response = self._send(client, params)
        except self._sdk.APIError as exc:
            raise self._translate(exc) from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        stop_reason = str(response.stop_reason or "")
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            explanation = getattr(details, "explanation", None) if details else None
            raise ProviderRefusalError(
                "Das KI-Modell hat die Anfrage abgelehnt." + (f" ({explanation})" if explanation else "")
            )
        usage = response.usage
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return AIResponse(
            text=text,
            provider=self.name,
            model=str(getattr(response, "model", request.model)),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0) + cache_read + cache_write,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            stop_reason=stop_reason,
            truncated=stop_reason == "max_tokens",
        )

    def _send(self, client: Any, params: dict[str, Any]) -> Any:
        """Sendet die Anfrage; nutzt – wo verfügbar – den serverseitigen Rückfall bei Ablehnungen."""
        if self._fallback_supported and params["model"] in _FALLBACK_MODELS:
            try:
                return client.beta.messages.create(**params, betas=[_FALLBACK_BETA], fallbacks="default")
            except self._sdk.BadRequestError as exc:
                message = str(getattr(exc, "message", exc)).lower()
                if "fallback" not in message and "beta" not in message:
                    raise
                # Konto/Plattform unterstützt die Beta nicht -> künftig ohne Rückfall senden
                log.info("Serverseitiger Rückfall nicht verfügbar – sende ohne 'fallbacks'")
                self._fallback_supported = False
        return client.messages.create(**params)

    def _translate(self, exc: Exception) -> ProviderError:
        sdk = self._sdk
        message = str(getattr(exc, "message", exc))
        lowered = message.lower()
        detail = f"{type(exc).__name__}: {message}"
        if isinstance(exc, sdk.APIStatusError) and ("credit balance" in lowered or "billing" in lowered):
            return ProviderBillingError(detail=detail)
        if isinstance(exc, sdk.AuthenticationError):
            return ProviderKeyExpiredError(detail=detail) if "expired" in lowered else ProviderAuthError(detail=detail)
        if isinstance(exc, sdk.PermissionDeniedError):
            return ProviderPermissionError(detail=detail)
        if isinstance(exc, sdk.NotFoundError):
            return ProviderModelError(
                "Das eingestellte Claude-Modell ist für diesen Schlüssel nicht verfügbar. Bitte in den Einstellungen "
                "ein verfügbares Modell eintragen (z. B. claude-opus-5-5).", detail=detail)
        if isinstance(exc, sdk.RateLimitError):
            retry_after: float | None = None
            try:
                retry_after = float(exc.response.headers.get("retry-after", ""))
            except (ValueError, TypeError, AttributeError):
                retry_after = None
            return ProviderRateLimitError(detail=detail, retry_after=retry_after)
        if isinstance(exc, sdk.APITimeoutError):
            return ProviderTimeoutError(detail=detail)
        if isinstance(exc, sdk.APIConnectionError):
            return ProviderNetworkError(detail=detail)
        if isinstance(exc, sdk.BadRequestError):
            return ProviderError(f"Die KI-Anfrage wurde abgelehnt: {message}", detail=detail)
        if isinstance(exc, sdk.APIStatusError):
            status = int(getattr(exc, "status_code", 0) or 0)
            if status >= 500 or status == 529:
                return ProviderServerError(detail=detail)
            return ProviderError(f"KI-Dienst meldet Fehler {status}.", detail=detail)
        return ProviderError(detail=detail)

    # ------------------------------------------------------------------ Modelle
    def list_models(self) -> list[str]:
        try:
            page = self._client.models.list(limit=100)
            return sorted({str(model.id) for model in page})
        except self._sdk.APIError as exc:
            raise self._translate(exc) from exc

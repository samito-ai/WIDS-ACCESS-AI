"""OpenAI-Anbindung über das offizielle ``openai``-SDK (Chat Completions)."""
from __future__ import annotations

from typing import Any

from app.ai.base import AIProvider, AIRequest, AIResponse, ChatMessage
from app.core.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderModelError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderServerError,
    ProviderTimeoutError,
)
from app.core.logging_setup import get_logger

log = get_logger(__name__)


def _content(message: ChatMessage) -> Any:
    if not message.images:
        return message.content
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
    for image in message.images:
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{image.b64()}"}}
        )
    return parts


class OpenAIProvider(AIProvider):
    name = "openai"
    display_name = "OpenAI"

    def __init__(self, api_key: str, *, timeout_s: float = 120.0, client: Any = None) -> None:
        import openai

        self._sdk = openai
        self._client = client or openai.OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)
        #: Manche Modelle lehnen ``response_format`` ab – dann wird ohne erneut gesendet
        self._json_mode_unsupported: set[str] = set()

    def complete(self, request: AIRequest) -> AIResponse:
        messages: list[dict[str, Any]] = []
        system = request.system
        if request.json_mode:
            # Die API verlangt bei JSON-Modus, dass das Wort „JSON“ im Prompt vorkommt
            system = (
                f"{system}\n\nAntworte ausschließlich mit einem einzigen gültigen JSON-Objekt."
            ).strip()
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend({"role": m.role, "content": _content(m)} for m in request.messages)
        params: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_tokens,
            "timeout": request.timeout_s,
        }
        use_json = request.json_mode and request.model not in self._json_mode_unsupported
        if use_json:
            params["response_format"] = {"type": "json_object"}
        try:
            try:
                response = self._client.chat.completions.create(**params)
            except self._sdk.BadRequestError as exc:
                if use_json and "response_format" in str(getattr(exc, "message", exc)):
                    self._json_mode_unsupported.add(request.model)
                    params.pop("response_format", None)
                    response = self._client.chat.completions.create(**params)
                else:
                    raise
        except self._sdk.APIError as exc:
            raise self._translate(exc) from exc
        choice = response.choices[0]
        refusal = getattr(choice.message, "refusal", None)
        if refusal:
            raise ProviderRefusalError(f"Das KI-Modell hat die Anfrage abgelehnt. ({refusal})")
        usage = response.usage
        finish = str(choice.finish_reason or "")
        return AIResponse(
            text=choice.message.content or "",
            provider=self.name,
            model=str(getattr(response, "model", request.model)),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
            stop_reason=finish,
            truncated=finish == "length",
        )

    def _translate(self, exc: Exception) -> ProviderError:
        sdk = self._sdk
        detail = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
        if isinstance(exc, sdk.AuthenticationError | sdk.PermissionDeniedError):
            return ProviderAuthError(detail=detail)
        if isinstance(exc, sdk.NotFoundError):
            return ProviderModelError(detail=detail)
        if isinstance(exc, sdk.RateLimitError):
            message = str(getattr(exc, "message", exc)).lower()
            if "quota" in message or "billing" in message:
                return ProviderError(
                    "Das Kontingent des OpenAI-Kontos ist aufgebraucht. Bitte Abrechnung/Guthaben prüfen.",
                    detail=detail,
                )
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
            return ProviderError(
                f"Die KI-Anfrage wurde abgelehnt: {getattr(exc, 'message', exc)}", detail=detail
            )
        if isinstance(exc, sdk.APIStatusError):
            status = int(getattr(exc, "status_code", 0) or 0)
            if status >= 500:
                return ProviderServerError(detail=detail)
            return ProviderError(f"KI-Dienst meldet Fehler {status}.", detail=detail)
        return ProviderError(detail=detail)

    def list_models(self) -> list[str]:
        try:
            models = self._client.models.list()
            ids = sorted({str(model.id) for model in models})
        except self._sdk.APIError as exc:
            raise self._translate(exc) from exc
        chat = [m for m in ids if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]
        return chat or ids

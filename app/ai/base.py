"""Provider-neutrale Schnittstelle für KI-Anfragen.

Die Access-Engine kennt diese Schicht nicht; Agenten sprechen ausschließlich mit
:class:`AIProvider`. Ein neuer Anbieter wird durch eine weitere Unterklasse ergänzt und in
``manager.PROVIDER_FACTORIES`` registriert.
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["user", "assistant"]


class ImageInput(BaseModel):
    data: bytes
    media_type: str = "image/png"

    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")


class ChatMessage(BaseModel):
    role: Role
    content: str
    images: list[ImageInput] = Field(default_factory=list)


class AIRequest(BaseModel):
    system: str = ""
    messages: list[ChatMessage]
    model: str
    max_tokens: int = 16000
    #: Die Antwort soll ein einzelnes JSON-Objekt sein
    json_mode: bool = False
    timeout_s: float = 120.0


class AIResponse(BaseModel):
    text: str
    provider: str
    model: str
    #: alle Eingabe-Tokens, einschließlich der aus dem bzw. in den Prompt-Cache gelesenen/geschriebenen
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""
    truncated: bool = False


class AIProvider(ABC):
    """Ein KI-Anbieter. Implementierungen übersetzen SDK-Fehler in ``ProviderError``-Klassen
    und führen selbst **keine** Wiederholungen durch – das übernimmt der ProviderManager."""

    name: str = "abstract"
    display_name: str = "Abstrakt"
    supports_vision: bool = True

    @abstractmethod
    def complete(self, request: AIRequest) -> AIResponse: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    def test_connection(self, model: str) -> str:
        """Echte, authentifizierte Mini-Anfrage an genau dieses Modell (wenige Tokens).

        Prüft damit Schlüssel, Berechtigung, Guthaben und Modell zugleich – eine reine Modellliste würde
        z. B. fehlendes Guthaben nicht erkennen. Liefert das Modell, das geantwortet hat.
        """
        response = self.complete(AIRequest(model=model, max_tokens=64, timeout_s=60,
                                           messages=[ChatMessage(role="user", content="Antworte nur mit: OK")]))
        return response.model

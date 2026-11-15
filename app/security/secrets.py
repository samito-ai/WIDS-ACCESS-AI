"""Sichere Ablage von API-Schlüsseln.

Primär: Windows-Anmeldeinformationsverwaltung (Credential Manager).
Fallback: DPAPI-verschlüsselte Datei im Datenverzeichnis (nur für den angemeldeten Benutzer lesbar).
Schlüssel werden niemals im Klartext auf die Festplatte, in die SQLite-Datei oder ins Log geschrieben.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app import APP_ID
from app.core import paths
from app.core.errors import WidsError
from app.core.logging_setup import get_logger, register_secret

log = get_logger(__name__)


class SecretBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class CredentialManagerBackend(SecretBackend):
    name = "Windows-Anmeldeinformationsverwaltung"

    def __init__(self, prefix: str = APP_ID) -> None:
        import win32cred

        self._cred = win32cred
        self._prefix = prefix

    def _target(self, key: str) -> str:
        return f"{self._prefix}/{key}"

    def get(self, key: str) -> str | None:
        import pywintypes

        try:
            cred = self._cred.CredRead(self._target(key), self._cred.CRED_TYPE_GENERIC)
        except pywintypes.error:
            return None
        blob = cred.get("CredentialBlob")
        if not blob:
            return None
        text = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
        return text.rstrip("\x00") or None

    def set(self, key: str, value: str) -> None:
        self._cred.CredWrite(
            {
                "Type": self._cred.CRED_TYPE_GENERIC,
                "TargetName": self._target(key),
                "UserName": APP_ID,
                "CredentialBlob": value,
                "Comment": "API-Schlüssel für WIDS ACCESS AI",
                "Persist": self._cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def delete(self, key: str) -> None:
        import pywintypes

        try:
            self._cred.CredDelete(self._target(key), self._cred.CRED_TYPE_GENERIC)
        except pywintypes.error:
            pass


class DpapiFileBackend(SecretBackend):
    name = "DPAPI-verschlüsselte Datei"

    def __init__(self, directory: Path | None = None) -> None:
        import win32crypt

        self._crypt = win32crypt
        self._dir = directory or (paths.data_dir() / "secrets")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self._dir / f"{safe}.bin"

    def get(self, key: str) -> str | None:
        file = self._file(key)
        if not file.exists():
            return None
        try:
            _, data = self._crypt.CryptUnprotectData(file.read_bytes(), None, None, None, 0)
        except Exception as exc:
            log.warning("DPAPI-Entschlüsselung fehlgeschlagen: %s", type(exc).__name__)
            return None
        return bytes(data).decode("utf-8")

    def set(self, key: str, value: str) -> None:
        blob = self._crypt.CryptProtectData(value.encode("utf-8"), APP_ID, None, None, None, 0)
        self._file(key).write_bytes(blob)

    def delete(self, key: str) -> None:
        self._file(key).unlink(missing_ok=True)


class MemoryBackend(SecretBackend):
    """Nur für Tests – hält Werte ausschließlich im Arbeitsspeicher."""

    name = "Arbeitsspeicher"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


class SecretStore:
    """Fassade: wählt automatisch das beste verfügbare Backend."""

    def __init__(self, backend: SecretBackend | None = None) -> None:
        self._backend = backend or self._pick_backend()

    @staticmethod
    def _pick_backend() -> SecretBackend:
        try:
            backend: SecretBackend = CredentialManagerBackend()
            # Funktionsprobe, damit ein defekter Credential Manager früh auffällt
            backend.get("__probe__")
            return backend
        except Exception as exc:
            log.warning("Credential Manager nicht nutzbar (%s), verwende DPAPI-Datei", type(exc).__name__)
            return DpapiFileBackend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @staticmethod
    def _key(provider: str) -> str:
        return f"api-key-{provider}"

    def get_api_key(self, provider: str) -> str | None:
        value = self._backend.get(self._key(provider))
        if value:
            register_secret(value)
        return value or None

    @staticmethod
    def normalize(value: str) -> str:
        """Entfernt beim Einfügen mitkopierte Leerzeichen, Zeilenumbrüche und Anführungszeichen."""
        return "".join(value.split()).strip("\"'„“”")

    def set_api_key(self, provider: str, value: str) -> str:
        """Speichert den Schlüssel dauerhaft und liest ihn zur Kontrolle zurück. Liefert den gespeicherten Wert."""
        value = self.normalize(value)
        if not value:
            raise WidsError("Der eingegebene API-Schlüssel ist leer.")
        register_secret(value)
        try:
            self._backend.set(self._key(provider), value)
        except Exception as exc:
            raise WidsError(f"Der Schlüssel konnte nicht gespeichert werden ({self._backend.name}).",
                            detail=type(exc).__name__) from exc
        if self._backend.get(self._key(provider)) != value:
            raise WidsError(f"Der Schlüssel wurde nicht korrekt gespeichert ({self._backend.name}).")
        log.info("API-Schlüssel für %s gespeichert und geprüft (%s)", provider, self._backend.name)
        return value

    def delete_api_key(self, provider: str) -> None:
        self._backend.delete(self._key(provider))
        log.info("API-Schlüssel für %s entfernt", provider)

    def has_api_key(self, provider: str) -> bool:
        return bool(self._backend.get(self._key(provider)))

    @staticmethod
    def mask(value: str | None) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "•" * len(value)
        return f"{value[:4]}{'•' * 8}{value[-4:]}"

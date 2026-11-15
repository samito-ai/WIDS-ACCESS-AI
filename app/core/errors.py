"""Fehlerhierarchie der Anwendung.

Jede Ausnahme trägt eine verständliche deutsche Meldung (``user_message``) für die
Oberfläche und optional technische Details (``detail``) für das Log.
"""
from __future__ import annotations


class WidsError(Exception):
    """Basisklasse aller erwarteten Anwendungsfehler."""

    default_message = "Ein unerwarteter Fehler ist aufgetreten."
    #: Darf die auslösende Operation gefahrlos automatisch wiederholt werden?
    retryable = False

    def __init__(self, user_message: str | None = None, *, detail: str | None = None) -> None:
        self.user_message = user_message or self.default_message
        self.detail = detail
        super().__init__(self.user_message)

    def __str__(self) -> str:
        return self.user_message


# --------------------------------------------------------------------------- Access
class AccessError(WidsError):
    default_message = "Fehler beim Zugriff auf Microsoft Access."

    def __init__(
        self, user_message: str | None = None, *, detail: str | None = None, dao_code: int | None = None
    ) -> None:
        super().__init__(user_message, detail=detail)
        self.dao_code = dao_code


class AccessNotInstalledError(AccessError):
    default_message = (
        "Microsoft Access bzw. die Access-Datenbank-Engine (DAO/ACE) wurde nicht gefunden. "
        "Bitte Microsoft Access installieren."
    )


class AccessNotRunningError(AccessError):
    default_message = "Es wurde keine geöffnete Access-Datenbank gefunden. Bitte Access mit der Übungsdatei öffnen."


class AccessBusyError(AccessError):
    default_message = (
        "Access reagiert gerade nicht (vermutlich ist ein Dialog geöffnet oder ein Feld wird bearbeitet). "
        "Bitte den Dialog schließen und erneut versuchen."
    )
    retryable = True


class AccessTimeoutError(AccessError):
    default_message = "Die Access-Operation hat zu lange gedauert und wurde abgebrochen."


class DatabaseNotFoundError(AccessError):
    default_message = "Die Datenbankdatei wurde nicht gefunden."


class DatabaseLockedError(AccessError):
    default_message = (
        "Die Datenbank oder das Objekt ist gesperrt (z. B. exklusiv oder in der Entwurfsansicht geöffnet). "
        "Bitte das Objekt in Access schließen und erneut versuchen."
    )
    retryable = True


class DatabaseReadOnlyError(AccessError):
    default_message = "Die Datenbank ist schreibgeschützt und kann nicht verändert werden."


class ObjectExistsError(AccessError):
    default_message = "Ein Objekt mit diesem Namen existiert bereits."

    def __init__(self, object_type: str, name: str, *, detail: str | None = None) -> None:
        super().__init__(f"{object_type} „{name}“ existiert bereits.", detail=detail)
        self.object_type = object_type
        self.name = name


class ObjectNotFoundError(AccessError):
    default_message = "Das Objekt wurde nicht gefunden."

    def __init__(self, object_type: str, name: str, *, detail: str | None = None) -> None:
        super().__init__(f"{object_type} „{name}“ wurde nicht gefunden.", detail=detail)
        self.object_type = object_type
        self.name = name


class InvalidNameError(AccessError):
    default_message = "Der Name ist in Access nicht zulässig."


class SqlSyntaxError(AccessError):
    default_message = "Die Abfrage enthält einen SQL-Fehler."


class DataIntegrityError(AccessError):
    default_message = "Die Änderung verletzt eine Regel der Datenbank (z. B. Schlüssel, Pflichtfeld oder Beziehung)."


class UnsupportedOperationError(AccessError):
    default_message = "Diese Operation wird von der installierten Access-Version nicht unterstützt."


# --------------------------------------------------------------------------- KI
class ProviderError(WidsError):
    default_message = "Fehler bei der KI-Anfrage."


class ProviderNotConfiguredError(ProviderError):
    default_message = "Es ist kein API-Schlüssel hinterlegt. Bitte in den Einstellungen einen Schlüssel eintragen."


class ProviderAuthError(ProviderError):
    default_message = (
        "Der API-Schlüssel wurde abgelehnt (ungültig, bei Anthropic widerrufen oder abgelaufen). "
        "Bitte in den Einstellungen einen neuen Schlüssel eintragen."
    )


class ProviderKeyExpiredError(ProviderAuthError):
    default_message = (
        "Der API-Schlüssel ist bei Anthropic abgelaufen. Lokal gespeicherte Schlüssel laufen nicht ab – "
        "bitte in der Anthropic-Konsole einen neuen Schlüssel erstellen und in den Einstellungen ersetzen."
    )


class ProviderPermissionError(ProviderError):
    default_message = (
        "Der API-Schlüssel ist gültig, hat aber keine Berechtigung für diese Anfrage "
        "(z. B. falscher Workspace oder gesperrtes Modell)."
    )


class ProviderBillingError(ProviderError):
    default_message = "Das API-Konto hat kein Guthaben oder die Abrechnung ist nicht eingerichtet (Anthropic-Konsole → Billing)."


class ProviderRateLimitError(ProviderError):
    default_message = "Das API-Limit ist erreicht. Bitte später erneut versuchen."
    retryable = True

    def __init__(
        self, user_message: str | None = None, *, detail: str | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(user_message, detail=detail)
        self.retry_after = retry_after


class ProviderNetworkError(ProviderError):
    default_message = "Keine Verbindung zum KI-Dienst. Bitte die Internetverbindung prüfen."
    retryable = True


class ProviderTimeoutError(ProviderError):
    default_message = "Der KI-Dienst hat nicht rechtzeitig geantwortet."
    # bewusst nicht wiederholbar: jeder weitere Versuch kostet erneut und dauert genauso lange


class ProviderServerError(ProviderError):
    default_message = "Der KI-Dienst meldet einen Serverfehler. Bitte später erneut versuchen."
    retryable = True


class ProviderModelError(ProviderError):
    default_message = "Das gewählte KI-Modell ist nicht verfügbar. Bitte in den Einstellungen ein anderes Modell wählen."


class ProviderRefusalError(ProviderError):
    default_message = "Das KI-Modell hat die Anfrage abgelehnt."


class ResponseFormatError(ProviderError):
    default_message = "Die KI hat keine gültige strukturierte Antwort geliefert."


class CostLimitError(ProviderError):
    default_message = "Das eingestellte Kostenlimit ist erreicht. Die Anfrage wurde nicht gesendet."


# --------------------------------------------------------------------------- Sicherheit / Ablauf
class PolicyViolationError(WidsError):
    default_message = "Die Aktion wurde aus Sicherheitsgründen blockiert."


class ApprovalRequiredError(WidsError):
    default_message = "Für diese Aktion ist eine ausdrückliche Freigabe erforderlich."


class TaskCancelledError(WidsError):
    default_message = "Die Automatisierung wurde abgebrochen."


class PlanValidationError(WidsError):
    default_message = "Der Lösungsplan ist ungültig."


class BackupError(WidsError):
    default_message = "Die Sicherungskopie konnte nicht erstellt oder wiederhergestellt werden."


# --------------------------------------------------------------------------- Erkennung / UI-Automation
class CaptureError(WidsError):
    default_message = "Die Aufgabenstellung konnte nicht erfasst werden."


class OcrUnavailableError(CaptureError):
    default_message = "Auf diesem System steht keine Texterkennung (OCR) zur Verfügung."


class UiAutomationError(WidsError):
    default_message = "Die Bedienung der Access-Oberfläche ist fehlgeschlagen."


class HotkeyError(WidsError):
    default_message = "Das Tastenkürzel konnte nicht registriert werden."

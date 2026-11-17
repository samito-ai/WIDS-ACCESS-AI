"""„System & KI testen“: prüft, ob WIDS wirklich einsatzbereit ist.

Teil 1 (kostenlos, lokal): Access installiert, temporäre Testdatenbank, Verbindung über COM/DAO, Erkennung der
geöffneten Übung, sicherer Testplan auf einer TEMPORÄREN Kopie, unabhängige Ergebnisprüfung, Sicherung und Bereinigung.
Echte Übungs- oder Schuldatenbanken werden nie angefasst (eigene Access-Verbindung, eigener Ordner).

Teil 2 (kostet wenige Zehntel Cent, nur nach bewusstem Start): API-Schlüssel, Anmeldung und Modell (kostenlose
Modellliste), eine echte kleine KI-Anfrage – gezählt und vorab gegen ``PAID_COST_CAP_USD`` geprüft – und deren
Verarbeitung.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core.logging_setup import get_logger

log = get_logger(__name__)

Status = Literal["ok", "warn", "fail", "skipped"]
#: Obergrenze des kostenpflichtigen Teils: EINE Anfrage (höchstens 64 Ausgabetokens), auch über Wiederholungen
PAID_COST_CAP_USD = 0.01
_JSON_MAX_TOKENS = 64
_PROMPT = 'Antworte nur mit JSON: {"summe": <2+3>, "ok": true}'


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    paid: bool = False


def summary(results: list[CheckResult]) -> tuple[str, str]:
    """(Gesamturteil, Text): bereit / eingeschränkt / fehlgeschlagen mit konkreter Ursache."""
    failed = [r for r in results if r.status == "fail"]
    warned = [r for r in results if r.status in ("warn", "skipped")]
    if not failed and not warned:
        return "bereit", "Bestanden – System bereit."
    if failed and any(r.name.startswith(("Microsoft Access", "API-Schlüssel", "Anmeldung", "KI-Testanfrage"))
                      for r in failed):
        return "fehlgeschlagen", "Fehlgeschlagen: " + "; ".join(f"{r.name} – {r.detail}" for r in failed)
    issues = failed + warned
    return "eingeschränkt", "Teilweise bestanden – eingeschränkte Funktion: " + "; ".join(
        f"{r.name} – {r.detail}" for r in issues)


def _step(results: list[CheckResult], name: str, func: Callable[[], str], *, paid: bool = False) -> bool:
    try:
        detail = func()
    except Exception as exc:  # jeder Prüfpunkt meldet seine konkrete Ursache, der Test läuft weiter
        message = getattr(exc, "user_message", "") or f"{type(exc).__name__}: {exc}"
        results.append(CheckResult(name, "fail", message, paid))
        log.info("Selbsttest „%s“ fehlgeschlagen: %s", name, message)
        return False
    results.append(CheckResult(name, "ok", detail, paid))
    return True


def run_local(progress: Callable[[str], None] | None = None) -> list[CheckResult]:
    """Kostenloser Teil – ändert nichts außer einer temporären Testdatenbank, die am Ende gelöscht wird."""
    from app.access.service import AccessService
    from app.access.session import is_access_installed
    from app.agent.executor import AccessExecutor
    from app.agent.verifier import ResultVerifier
    from app.security.backup import BackupManager
    from app.storage.database import Storage
    from app.tasks import models as m
    from app.utils.sampledb import populate
    from app.vision import tecomp

    note = progress or (lambda _: None)
    results: list[CheckResult] = []
    note("Microsoft Access …")
    if not _step(results, "Microsoft Access installiert", lambda: "gefunden" if is_access_installed() else _raise(
            "Microsoft Access bzw. die Access-Datenbank-Engine wurde nicht gefunden.")):
        return results + [CheckResult(n, "skipped", "ohne Access nicht prüfbar") for n in (
            "Testdatenbank", "Verbindung COM/DAO", "Erkennung der Übungsdatenbank", "Sicherer Testplan",
            "Unabhängige Ergebnisprüfung", "Sicherung und Bereinigung")]
    folder = Path(tempfile.mkdtemp(prefix="wids-systemtest-"))
    service = AccessService(operation_timeout_s=60)
    step = m.PlanStep.model_validate({"id": 1, "title": "Selbsttest-Abfrage", "action": {
        "action": "create_query", "name": "qryWidsSelbsttest", "sql": "SELECT Nachname, Ort FROM Kunden"}})
    state: dict[str, Any] = {}
    try:
        note("Testdatenbank …")
        db = folder / "WIDS-Selbsttest.accdb"
        # im COM-Thread des eigenen AccessService (der aufrufende Pool-Thread hat kein COM initialisiert)
        _step(results, "Testdatenbank", lambda: (service.create_database(str(db), populate), "angelegt")[1])
        note("Verbindung …")

        def connect() -> str:
            if not service.is_connected:
                raise RuntimeError("Testdatenbank nicht verbunden")
            service.disconnect()
            service.connect(str(db), prefer_running=False)  # erneut öffnen: eigene Instanz, nie die geöffnete Übung
            return f"{len(service.schema(refresh=True).tables)} Tabellen gelesen"
        _step(results, "Verbindung COM/DAO", connect)
        note("Erkennung …")
        _step(results, "Erkennung der Übungsdatenbank",
              lambda: f"geöffnet: {Path(found).name}" if (found := tecomp.open_access_database())
              else "funktioniert – zurzeit ist keine Übung in Access geöffnet")
        note("Sicherer Testplan …")

        def execute() -> str:
            state["result"] = service.run(lambda e: AccessExecutor().execute(e, step))
            if state["result"].status != "ok":
                raise RuntimeError(state["result"].message)
            return str(state["result"].message)
        _step(results, "Sicherer Testplan", execute)
        note("Ergebnisprüfung …")

        def verify() -> str:
            verification = service.run(lambda e: ResultVerifier().verify(e, step, state["result"]))
            if not verification.passed:
                raise RuntimeError("Prüfung nicht bestanden: " + ", ".join(c.name for c in verification.failed_checks))
            return f"{len(verification.checks)} Prüfpunkte bestanden"
        _step(results, "Unabhängige Ergebnisprüfung", verify)
        note("Sicherung und Bereinigung …")

        def backup_and_cleanup() -> str:
            storage = Storage(folder / "selbsttest.sqlite3")
            try:
                service.disconnect()
                info = BackupManager(storage, folder / "sicherung", folder / "kopien").create_backup(db, "Selbsttest")
                if not Path(info.backup_path).exists() or Path(info.backup_path).stat().st_size == 0:
                    raise RuntimeError("Sicherungsdatei fehlt.")
            finally:
                storage.close()
            return "Sicherung erstellt und geprüft"
        _step(results, "Sicherung und Bereinigung", backup_and_cleanup)
    finally:
        service.shutdown()
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists() and results and results[-1].name == "Sicherung und Bereinigung":
            results[-1] = CheckResult("Sicherung und Bereinigung", "warn", "temporärer Ordner ließ sich nicht löschen")
    return results


def run_paid(ai: Any) -> list[CheckResult]:
    """Kostenpflichtiger Teil (höchstens ``PAID_COST_CAP_USD``) – nur nach bewusstem Start im Dashboard."""
    from app.ai.base import ChatMessage
    from app.ai.jsonutil import extract_json_object

    results: list[CheckResult] = []
    if not _step(results, "API-Schlüssel vorhanden", lambda: "hinterlegt" if ai.is_configured() else _raise(
            "Kein API-Schlüssel hinterlegt – bitte in den Einstellungen eintragen."), paid=True):
        return results + [CheckResult(n, "skipped", "ohne Schlüssel nicht prüfbar", True)
                          for n in ("Anmeldung und Modell", "KI-Testanfrage", "Verarbeitung der KI-Antwort")]
    check: dict[str, Any] = {}
    model = ai.model_for("test")

    def login() -> str:
        available = ai.provider().list_models()  # kostenlos; ein ungültiger Schlüssel wird hier abgewiesen
        if model not in available:
            raise RuntimeError(f"Modell „{model}“ ist für diesen Schlüssel nicht verfügbar.")
        return f"angemeldet, Modell {model} verfügbar"
    if not _step(results, "Anmeldung und Modell", login, paid=True):
        return results + [CheckResult(n, "skipped", "nach fehlgeschlagener Anmeldung nicht gesendet", True)
                          for n in ("KI-Testanfrage", "Verarbeitung der KI-Antwort")]

    def request() -> str:
        worst = ai.estimate_cost(model, 400, _JSON_MAX_TOKENS)  # großzügig geschätzte Eingabe inkl. Systemanteil
        if worst is None or worst > PAID_COST_CAP_USD:
            raise RuntimeError(f"Höchstkosten für „{model}“ nicht innerhalb von {PAID_COST_CAP_USD:.2f} $ – "
                               "Anfrage nicht gesendet.")
        response = ai.complete(messages=[ChatMessage(role="user", content=_PROMPT)], purpose="test", json_mode=True,
                               max_tokens=_JSON_MAX_TOKENS)
        check["response"] = response
        cost = ai.estimate_cost(response.model, response.input_tokens, response.output_tokens) or 0.0
        return f"{response.input_tokens}+{response.output_tokens} Tokens, ≈ {cost:.4f} $"
    _step(results, "KI-Testanfrage", request, paid=True)

    def parse() -> str:
        if "response" not in check:
            raise RuntimeError("keine Antwort erhalten")
        data = extract_json_object(check["response"].text)
        if data.get("summe") != 5:
            raise RuntimeError(f"unerwartete Antwort: {check['response'].text[:80]}")
        return "JSON-Antwort korrekt verarbeitet"
    _step(results, "Verarbeitung der KI-Antwort", parse, paid=True)
    return results


def _raise(message: str) -> str:
    raise RuntimeError(message)

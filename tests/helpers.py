"""Testhilfen: skriptgesteuerter KI-Provider und Aufbau einer vollständigen Pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.access.service import AccessService
from app.agent.analyzer import TaskAnalyzer
from app.agent.orchestrator import TaskPipeline
from app.ai import prompts
from app.ai.base import AIProvider, AIRequest, AIResponse
from app.ai.manager import ProviderManager
from app.core.settings import AppSettings
from app.security.backup import BackupManager
from app.security.policy import SafetyPolicy
from app.security.secrets import MemoryBackend, SecretStore
from app.storage.database import Storage


class ScriptedProvider(AIProvider):
    """Liefert vorab festgelegte Antworten (oder wirft vorab festgelegte Fehler) – ohne Netzwerk.

    Damit wird die komplette Verarbeitungskette getestet (Prompt-Aufbau, JSON-Extraktion,
    Schemavalidierung, Korrekturschleifen, Ausführung, Verifikation). Die Qualität echter
    Modellantworten wird dadurch NICHT getestet.
    """

    name = "anthropic"
    display_name = "Skript"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requirement_responses: list[Any] = []
        #: Antworten auf Korrekturanfragen für nicht erfüllte Anforderungen (leer = „keine Korrektur möglich“)
        self.fix_responses: list[Any] = []
        self.requests: list[AIRequest] = []

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if request.system.startswith(prompts.REQUIREMENT_FIX_SYSTEM[:40]):
            fix = self.fix_responses.pop(0) if self.fix_responses else {"steps": [], "notes": ["keine Korrektur"]}
            if isinstance(fix, Exception):
                raise fix
            text = fix if isinstance(fix, str) else json.dumps(fix, ensure_ascii=False)
            return AIResponse(text=text, provider=self.name, model=request.model, input_tokens=600, output_tokens=150)
        if request.system.startswith(prompts.REQUIREMENTS_SYSTEM[:40]):
            # Anforderungsprüfung: eigene Warteschlange, sonst „alles erfüllt“
            answer = self.requirement_responses.pop(0) if self.requirement_responses else None
            if answer is None or (isinstance(answer, dict) and "unmet" in answer):
                answer = requirement_answers(request.messages[-1].content, (answer or {}).get("unmet"))
            if isinstance(answer, Exception):
                raise answer
            text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            return AIResponse(text=text, provider=self.name, model=request.model, input_tokens=800, output_tokens=200)
        if not self.responses:
            raise AssertionError("ScriptedProvider: keine weitere Antwort hinterlegt")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, AIResponse):  # z. B. abgeschnittene Antwort (truncated) nachstellen
            return item
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return AIResponse(text=text, provider=self.name, model=request.model, input_tokens=1000, output_tokens=500)

    def list_models(self) -> list[str]:
        return ["skript-modell"]


def solution(steps: list[dict[str, Any]], **analysis: Any) -> dict[str, Any]:
    """Baut eine KI-Antwort im AgentSolution-Schema."""
    base = {
        "task_type": "select_query",
        "title": "Testaufgabe",
        "summary": "Zusammenfassung",
        "requirements": ["Anforderung"],
        "confidence": 0.9,
        "explanation": "Erklärung des Lösungswegs.",
    }
    base.update(analysis)
    wrapped = [{"action": s} if "action" in s and isinstance(s["action"], str) else dict(s) for s in steps]
    for step in wrapped:  # standardmäßig deckt jeder Schritt alle Anforderungen ab
        step.setdefault("covers", list(range(1, len(base["requirements"]) + 1)))
    return {"analysis": base, "plan": {"steps": wrapped, "notes": []}}


def requirement_answers(text: str, unmet: dict[int, str] | None = None) -> dict[str, Any]:
    """Antwort der Anforderungsprüfung: alle aus dem Prompt gelesenen Anforderungen erfüllt, außer ``unmet``."""
    block = text.split("ANFORDERUNGEN:\n", 1)[1].split("\n\n", 1)[0]
    results = []
    for line in block.splitlines():
        number, _, requirement = line.partition(". ")
        status = "nicht erfüllt" if unmet and int(number) in unmet else "erfüllt"
        results.append({"number": int(number), "requirement": requirement, "status": status,
                        "evidence": (unmet or {}).get(int(number), "im Endzustand vorhanden")})
    return {"results": results}


@dataclass
class Harness:
    settings: AppSettings
    storage: Storage
    access: AccessService
    ai: ProviderManager
    provider: ScriptedProvider
    backups: BackupManager
    pipeline: TaskPipeline
    environment: dict[str, list[str]]
    sleeps: list[float]

    def close(self) -> None:
        self.access.shutdown()
        self.storage.close()


def build_harness(db_path: Path | None, responses: list[Any], tmp_path: Path, **settings_overrides: Any) -> Harness:
    settings = AppSettings()
    for dotted, value in settings_overrides.items():
        section, _, attr = dotted.partition("__")
        setattr(getattr(settings, section), attr, value)
    storage = Storage(tmp_path / "wids-test.sqlite3")
    sleeps: list[float] = []
    ai = ProviderManager(settings, SecretStore(MemoryBackend()), storage, sleep=sleeps.append)
    provider = ScriptedProvider(responses)
    ai.set_provider_for_testing("anthropic", provider)
    access = AccessService(operation_timeout_s=90)
    if db_path is not None:
        access.connect(str(db_path), prefer_running=False)
    backups = BackupManager(storage, tmp_path / "backups", tmp_path / "workcopies")
    environment: dict[str, list[str]] = {"titles": [], "processes": []}
    pipeline = TaskPipeline(
        settings=settings,
        storage=storage,
        access=access,
        analyzer=TaskAnalyzer(ai),
        backups=backups,
        policy=SafetyPolicy(settings.security),
        environment_probe=lambda: (environment["titles"], environment["processes"]),
        sleep=sleeps.append,
    )
    return Harness(settings, storage, access, ai, provider, backups, pipeline, environment, sleeps)

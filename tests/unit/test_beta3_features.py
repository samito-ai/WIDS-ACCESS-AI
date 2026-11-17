"""Beta 3: Ursachen-Einordnung der Selbstkorrektur, Fortschritt, Kosten, Updates, Diagnose."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from app.agent.executor import StepResult
from app.agent.orchestrator import FAILURE_HINTS, classify_failure
from app.tasks import models as m


def _req(status: str) -> m.RequirementResult:
    return m.RequirementResult(number=1, requirement="A", status=status, evidence="x")


def test_failure_cause_is_classified_into_the_four_categories() -> None:
    task = m.CapturedTask(text="Erstellen Sie die Abfrage qryA mit allen Kunden aus Tirol, sortiert nach Nachname.",
                          method="tecomp")
    ok, failed = StepResult(step_id=1), StepResult(step_id=2, status="failed", message="Access-Fehler")
    assert classify_failure([ok, failed], [], [_req("nicht erfüllt")], task) == "Access-Ausführung"
    assert classify_failure([ok], ["Schritt 1: Breite"], [], task) == "Ergebnisprüfung"
    assert classify_failure([ok], [], [_req("nicht erfüllt")], task) == "KI-Plan"
    short = m.CapturedTask(text="Zeile farbig", method="ocr")
    assert classify_failure([ok], [], [_req("unklar")], short) == "Aufgabenerkennung"
    assert set(FAILURE_HINTS) == {"Access-Ausführung", "Ergebnisprüfung", "KI-Plan", "Aufgabenerkennung"}



# --------------------------------------------------------------------------- Fortschritt
def test_progress_is_built_only_from_real_events() -> None:
    from app.agent.progress import ProgressTracker

    tracker = ProgressTracker()
    tracker.start()
    tracker.mark("Aufgabenstellung erkannt")
    tracker.mark("KI analysiert Anforderungen", "active")
    tracker.mark("Datenbank verbunden")
    tracker.mark("KI analysiert Anforderungen")
    tracker.plan(2)
    tracker.on_event("step_started", "Abfrage anlegen", 1)
    assert tracker.current == "Schritt 1 von 2: Abfrage anlegen"
    tracker.on_event("verification", "Prüfung bestanden", 1, {"passed": True})
    tracker.on_event("step_done", "ok", 1)
    tracker.on_event("step_started", "Bericht", 2)
    tracker.on_event("verification", "Prüfung nicht bestanden", 2, {"passed": False})
    tracker.on_event("step_done", "ok", 2)
    assert (tracker.done_steps, tracker.total_steps, tracker.confirmed) == (2, 2, ["Abfrage anlegen"])
    tracker.on_event("verification", "Neuprüfung nicht bestanden", 1, {"passed": False})
    assert tracker.confirmed == [], "Endprüfung nimmt die Bestätigung zurück"
    tracker.on_event("log", "Prüfe …", 0, {"phase": "check"})
    tracker.on_event("log", "Korrektur", 0, {"correction": 1, "cause": "Ergebnisprüfung"})
    tracker.on_event("step_started", "Korrektur: Bericht", 3)
    assert tracker.total_steps == 3, "Korrekturschritte erhöhen die Gesamtzahl"
    tracker.finish("Nicht abgeschlossen", "review")
    assert tracker.phases["Ergebnisprüfung"] == "failed" and not tracker.running
    assert "Zyklus 1 von 3 – Ursache: Ergebnisprüfung" in tracker.correction
    assert any(line.startswith("✗ Ergebnisprüfung") for line in tracker.lines())


# --------------------------------------------------------------------------- Kosten
def test_costs_per_task_session_today_and_task_budget(tmp_path: Path) -> None:
    from app.ai.manager import ProviderManager
    from app.core.errors import CostLimitError
    from app.core.settings import AppSettings
    from app.security.secrets import MemoryBackend, SecretStore
    from app.storage.database import Storage, now_iso
    from tests.helpers import ScriptedProvider

    storage = Storage(tmp_path / "k.sqlite3")
    settings = AppSettings()
    settings.ai.task_cost_limit_usd = 0.01  # eine Testanfrage kostet ≈ 0,02 $
    ai = ProviderManager(settings, SecretStore(MemoryBackend()), storage, sleep=lambda _: None)
    ai.set_provider_for_testing("anthropic", ScriptedProvider(['{"x": 1}'] * 5))
    session = now_iso()
    ai.task_id = 7
    from app.ai.base import ChatMessage

    ai.complete(messages=[ChatMessage(role="user", content="a")])  # 1000 in / 500 out (ScriptedProvider)
    per_task = storage.usage_summary(task_id=7)
    assert per_task.requests == 1 and per_task.cost_usd > 0 and per_task.input_tokens == 1000
    assert storage.last_task_with_usage() == 7
    assert storage.usage_summary(since=session).requests == 1 and storage.usage_summary("day").requests == 1
    with pytest.raises(CostLimitError, match="Aufgabenbudget"):  # Budget dieser Aufgabe schon überschritten
        ai.complete(messages=[ChatMessage(role="user", content="b")])
    ai.task_id = 8  # neue Aufgabe: eigenes Budget
    ai.complete(messages=[ChatMessage(role="user", content="c")])
    assert storage.usage_summary("total").requests == 2


def test_old_usage_table_is_migrated(tmp_path: Path) -> None:
    import sqlite3

    from app.storage.database import Storage

    db = tmp_path / "alt.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, provider TEXT NOT NULL, "
                "model TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0, "
                "output_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL)")
    con.execute("INSERT INTO usage(ts, provider, model) VALUES('2026-11-01T00:00:00+00:00', 'anthropic', 'm')")
    con.commit()
    con.close()
    storage = Storage(db)
    storage.record_usage(provider="anthropic", model="m", purpose="x", input_tokens=1, output_tokens=1, cost_usd=0.1,
                         task_id=3)
    assert storage.usage_summary("total").requests == 2 and storage.usage_summary(task_id=3).requests == 1


# --------------------------------------------------------------------------- Diagnose
def test_severe_report_only_for_severe_errors_and_anonymized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import diagnostics, paths
    from app.core.errors import (
        AccessError,
        BackupError,
        CostLimitError,
        ProviderBillingError,
        ResponseFormatError,
        TaskCancelledError,
        WidsError,
    )

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)
    for normal in (TaskCancelledError(), CostLimitError("x"), ProviderBillingError("x"), WidsError("x")):
        assert diagnostics.classify(normal) is None, type(normal)
    assert diagnostics.classify(BackupError("x"))[1] == "Sicherung"
    assert diagnostics.classify(ResponseFormatError("x"))[1] == "KI-Anbindung"
    assert diagnostics.classify(AccessError("x"))[1] == "Access-Automatisierung"
    assert diagnostics.classify(KeyError("x"))[0].startswith("Programmfehler")
    home = str(Path.home())
    (tmp_path / "wids.log.jsonl").write_text(f'{{"msg": "Datei {home}\\\\a.accdb, Schlüssel sk-ant-api03-GEHEIM123456789abcdef"}}\n',
                                              encoding="utf-8")
    report = diagnostics.write_report(
        "Programmfehler", "Programm", f"Fehler in {home}\\x.accdb sk-ant-api03-ABCDEFG123456789 "
        "\\\\schulserver\\klasse2b\\Mueller_Anna\\note.accdb WHERE Nachname = 'Huber' „Geheimtext“",
        attempts=["Zyklus 1: erneut verbunden"], outcome="nicht behoben", task_id=5)
    text = report.read_text(encoding="utf-8")
    assert "GEHEIM" not in text and "ABCDEFG" not in text and home not in text and "<Pfad>.accdb" in text
    assert "Mueller_Anna" not in text and "schulserver" not in text and "Huber" not in text and "Geheimtext" not in text
    assert "Zyklus 1: erneut verbunden" in text and diagnostics.latest_report() == report


# --------------------------------------------------------------------------- Updates
def _release(tag: str, *, digest: str = "", body: str = "", url: str = "") -> dict[str, Any]:
    url = url or f"https://github.com/samito-ai/WIDS-ACCESS-AI/releases/download/{tag}/WIDS-Access-AI.exe"
    return {"tag_name": tag, "draft": False, "body": body, "html_url": "https://github.com/x",
            "assets": [{"name": "WIDS-Access-AI.exe", "browser_download_url": url, "digest": digest}]}


def test_version_comparison_and_release_choice() -> None:
    from app.core import updater

    assert updater.parse_version("1.0.0b2") < updater.parse_version("v1.0.0-beta.3") < updater.parse_version("1.0.0")
    assert updater.parse_version("v1.0.0-rc.1") < updater.parse_version("v1.0.0")
    good = "a" * 64
    releases = [_release("v1.0.0-beta.2", digest="sha256:" + "b" * 64),
                _release("v1.0.0-beta.3", digest="sha256:" + good),
                _release("v1.0.0-beta.9", url="https://evil.example.com/WIDS-Access-AI.exe"),
                {**_release("v2.0.0"), "draft": True}]
    latest = updater.pick_latest(releases, current="1.0.0b2")
    assert latest is not None and latest.version == "1.0.0-beta.3" and latest.sha256 == good, "fremde Quelle/Entwurf nie"
    assert updater.pick_latest(releases, current="1.0.0b3") is None, "nichts Neueres"
    body = f"| `WIDS-Access-AI.exe` | 1 Byte | `{good.upper()}` |"
    assert updater.pick_latest([_release("v1.0.0-beta.4", body=body)], current="1.0.0b3").sha256 == good
    # Stable-Kanal: Beta → 1.0.0 wird angeboten; 1.0.0 bekommt nur 1.0.1 (Stable), nie die Vorabversion 1.1.0-beta.1
    channel = [_release("v1.0.0", digest="sha256:" + good), _release("v1.0.1", digest="sha256:" + good),
               {**_release("v1.1.0-beta.1", digest="sha256:" + good), "prerelease": True}]
    assert updater.pick_latest(channel[:1], current="1.0.0b7").version == "1.0.0"
    assert updater.pick_latest(channel, current="1.0.0").version == "1.0.1", "Stable bekommt keine Beta"
    assert updater.pick_latest(channel, current="1.0.0b7").version == "1.1.0-beta.1", "Beta-Nutzer bleiben im Beta-Kanal"


def test_check_without_internet_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import updater

    def offline(*a: Any, **k: Any) -> Any:
        raise OSError("keine Verbindung")

    monkeypatch.setattr(updater.urllib.request, "urlopen", offline)
    assert updater.check() is None


def test_download_is_verified_against_published_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import paths, updater

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    payload = b"MZ echte EXE"

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *a: Any) -> None:
            self.close()

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: Response(payload))
    ok = updater.Release("1.0.0-beta.3", "v1.0.0-beta.3", "", "https://github.com/x", hashlib.sha256(payload).hexdigest(), "")
    assert updater.download(ok).read_bytes() == payload
    bad = updater.Release("1.0.0-beta.4", "v1.0.0-beta.4", "", "https://github.com/x", "0" * 64, "")
    with pytest.raises(updater.UpdateError, match="Prüfsumme"):
        updater.download(bad)
    assert not list((tmp_path / "updates").glob("*beta.4*")), "falsche Datei wird gelöscht"
    with pytest.raises(updater.UpdateError, match="keine Prüfsumme"):
        updater.download(updater.Release("1.0.0-beta.5", "v", "", "https://github.com/x", "", ""))


def test_install_swaps_and_restores_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import updater

    current, new = tmp_path / "WIDS-Access-AI.exe", tmp_path / "neu.exe"
    current.write_bytes(b"alt")
    new.write_bytes(b"neu")
    monkeypatch.setattr(updater, "_selftest", lambda exe: False)
    with pytest.raises(updater.UpdateError, match="Selbsttest"):
        updater.install(new, current)
    assert current.read_bytes() == b"alt", "nicht bestandener Selbsttest: nichts getauscht"
    monkeypatch.setattr(updater, "_selftest", lambda exe: True)

    def broken_copy(*a: Any, **k: Any) -> None:
        raise OSError("Datenträger voll")

    monkeypatch.setattr(updater.shutil, "copy2", broken_copy)
    with pytest.raises(updater.UpdateError, match="bisherige Version bleibt installiert"):
        updater.install(new, current)
    assert current.read_bytes() == b"alt", "Fehler beim Austausch: bisherige Version zurück"
    monkeypatch.undo()
    monkeypatch.setattr(updater, "_selftest", lambda exe: True)
    previous = updater.install(new, current)
    assert current.read_bytes() == b"neu" and previous.read_bytes() == b"alt"
    assert not current.with_name("WIDS-Access-AI.neu.exe").exists()


def test_restart_rolls_back_when_new_version_fails_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from app.core import updater

    current = tmp_path / "WIDS-Access-AI.exe"
    current.write_bytes(b"neu")
    current.with_name("WIDS-Access-AI.vorher.exe").write_bytes(b"alt")
    started: list[dict[str, Any]] = []
    fail = [False, False, True]  # erster Start scheitert, alle weiteren laufen

    class Child:
        def __init__(self, args: list[str], **kwargs: Any) -> None:
            started.append(kwargs)
            self.code = 3 if fail.pop() else None

        def wait(self, timeout: float) -> int:
            if self.code is None:
                raise subprocess.TimeoutExpired("x", timeout)
            return self.code

    monkeypatch.setattr(updater.subprocess, "Popen", Child)
    assert updater.restart(current, watch_s=0.1) is False
    assert current.read_bytes() == b"alt" and len(started) == 2, "bisherige Version zurück und gestartet"
    assert all(k["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1" for k in started)
    started.clear()
    assert updater.restart(current, watch_s=0.1) is True, "läuft die neue Version weiter, bleibt sie"



# --------------------------------------------------------------------------- System & KI testen
def test_system_check_summary_names_concrete_cause() -> None:
    from app.core.system_check import CheckResult, summary

    assert summary([CheckResult("Testdatenbank", "ok")])[0] == "bereit"
    verdict, text = summary([CheckResult("Testdatenbank", "ok"), CheckResult("Sicherer Testplan", "fail", "Timeout")])
    assert verdict == "eingeschränkt" and "Sicherer Testplan – Timeout" in text
    verdict, text = summary([CheckResult("Anmeldung und Modell", "fail", "Schlüssel ungültig", True)])
    assert verdict == "fehlgeschlagen" and "Schlüssel ungültig" in text


def test_paid_system_check_uses_one_small_request(tmp_path: Path) -> None:
    from app.ai.manager import ProviderManager
    from app.core import system_check
    from app.core.settings import AppSettings, ModelPrice
    from app.security.secrets import MemoryBackend, SecretStore
    from app.storage.database import Storage
    from tests.helpers import ScriptedProvider

    settings = AppSettings()
    settings.ai.anthropic_model = settings.ai.anthropic_fast_model = "skript-modell"
    storage = Storage(tmp_path / "s.sqlite3")
    ai = ProviderManager(settings, SecretStore(MemoryBackend()), storage, sleep=lambda _: None)
    assert [r.status for r in system_check.run_paid(ai)] == ["fail", "skipped", "skipped", "skipped"], "ohne Schlüssel"
    provider = ScriptedProvider(['{"summe": 5, "ok": true}'])
    ai.set_provider_for_testing("anthropic", provider)
    results = system_check.run_paid(ai)
    assert [r.status for r in results] == ["ok", "ok", "fail", "fail"], "ohne bekannten Preis: nichts gesendet"
    assert provider.requests == []
    settings.ai.prices["skript-modell"] = ModelPrice(input=3.0, output=15.0)
    results = system_check.run_paid(ai)
    assert [r.status for r in results] == ["ok"] * 4, results
    assert all(r.paid for r in results)
    assert len(provider.requests) == 1 and provider.requests[0].max_tokens == 64, "eine Anfrage, Ausgabe gedeckelt"
    assert storage.usage_summary("total").requests == 1, "die Anfrage ist in den Kosten gezählt"


def test_budget_does_not_fail_open_for_unpriced_model(tmp_path: Path) -> None:
    """Codex-Fund: ohne hinterlegten Preis zählte jede Anfrage 0 $ – das Limit wirkte nie. Jetzt: nicht gesendet."""
    from app.ai.base import ChatMessage
    from app.ai.manager import ProviderManager
    from app.core.errors import CostLimitError
    from app.core.settings import AppSettings
    from app.security.secrets import MemoryBackend, SecretStore
    from app.storage.database import Storage
    from tests.helpers import ScriptedProvider

    settings = AppSettings()
    settings.ai.anthropic_model = "unbekanntes-modell"
    settings.ai.cost_limit_usd = 5.0
    ai = ProviderManager(settings, SecretStore(MemoryBackend()), Storage(tmp_path / "p.sqlite3"), sleep=lambda _: None)
    provider = ScriptedProvider(['{"x": 1}'])
    ai.set_provider_for_testing("anthropic", provider)
    with pytest.raises(CostLimitError, match="kein Preis"):
        ai.complete(messages=[ChatMessage(role="user", content="a")], purpose="analyse")
    assert provider.requests == []
    settings.ai.cost_limit_usd = settings.ai.task_cost_limit_usd = 0.0  # Limits bewusst aus: Anfrage erlaubt
    ai.complete(messages=[ChatMessage(role="user", content="a")], purpose="analyse")
    assert len(provider.requests) == 1


def test_open_database_found_even_when_access_truncates_the_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXE-Abnahme-Fund: bei langen Pfaden kürzt Access den Fenstertitel – die Datenbank muss trotzdem erkannt werden
    (Pfad über die Running Object Table der Instanz mit genau diesem Fenster)."""
    from app.vision import tecomp
    from app.vision.capture import WindowInfo

    title = r"Access - Uebung : Datenbank- C:\Users\x\OneDrive - Schule\Klasse\sehr\langer\Ordner\Uebu"
    monkeypatch.setattr(tecomp, "visible_windows", lambda: [WindowInfo(77, title, "OMain", "MSACCESS.EXE", (0, 0, 1, 1))])
    asked: list[int] = []
    full = r"C:\Users\x\OneDrive - Schule\Klasse\sehr\langer\Ordner\Uebung.accdb"
    monkeypatch.setattr(tecomp, "_path_of_access_window", lambda hwnd: asked.append(hwnd) or full)
    assert tecomp.open_access_database() == full and asked == [77]
    monkeypatch.setattr(tecomp, "_path_of_access_window", lambda hwnd: "")
    assert tecomp.open_access_database() == "", "ohne Antwort der Instanz: nichts raten"

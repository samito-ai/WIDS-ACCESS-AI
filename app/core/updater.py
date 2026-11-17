"""Updates über GitHub Releases – nur aus dem fest hinterlegten offiziellen Repository.

Ablauf: Versionsprüfung (ohne Internet: still weiterarbeiten) → Hinweis im Dashboard → Download der Release-EXE erst
nach Zustimmung → SHA-256-Prüfung gegen die veröffentlichte Prüfsumme → Selbsttest der neuen EXE → Austausch der
laufenden EXE (die bisherige bleibt als ``…vorher.exe`` erhalten und wird bei einem Fehler zurückgelegt) → Neustart.
Einstellungen, API-Schlüssel (Windows-Anmeldeinformationsverwaltung) und Verlauf liegen außerhalb der EXE und
bleiben unverändert. Es werden keine Skripte ausgeführt und keine Windows-Schutzmechanismen umgangen.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app import __version__
from app.core import paths
from app.core.logging_setup import get_logger

log = get_logger(__name__)

REPO = "samito-ai/WIDS-ACCESS-AI"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
ASSET = "WIDS-Access-AI.exe"
_DOWNLOAD_PREFIX = f"/{REPO}/releases/download/"
_STAGES = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2, "": 3}


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    url: str
    sha256: str
    page: str


def parse_version(text: str) -> tuple[int, int, int, int, int]:
    """„1.0.0b2“ bzw. „v1.0.0-beta.3“ → vergleichbares Tupel (endgültige Version > rc > beta > alpha)."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)[-.]?(alpha|beta|rc|a|b)?\.?(\d*)", text.strip().lower())
    if match is None:
        raise ValueError(f"Unbekanntes Versionsformat: {text}")
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch), _STAGES[stage or ""], int(number or 0)


def _sha_for(release: dict) -> str:
    """Veröffentlichte SHA-256 der EXE: GitHub-Feld ``digest`` der Datei, sonst die Prüfsumme in den Release-Notes."""
    asset: dict = next((a for a in release.get("assets", []) if a.get("name") == ASSET), {})
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:") and re.fullmatch(r"[0-9a-fA-F]{64}", digest[7:]):
        return digest[7:].lower()
    for line in str(release.get("body") or "").splitlines():
        if ASSET in line and (found := re.search(r"\b[0-9a-fA-F]{64}\b", line)):
            return found.group(0).lower()
    return ""


def pick_latest(releases: list[dict], current: str = __version__) -> Release | None:
    """Neueste Version aus dem offiziellen Repository, die neuer als ``current`` ist und eine prüfbare EXE hat."""
    candidates = []
    stable = parse_version(current)[3] == _STAGES[""]
    for item in releases:
        if item.get("draft") or (stable and item.get("prerelease")):
            continue  # eine Stable-Version bekommt nur Stable-Updates angeboten, nie Vorabversionen
        try:
            version = parse_version(str(item.get("tag_name", "")))
        except ValueError:
            continue
        if stable and version[3] != _STAGES[""]:
            continue  # auch ohne Vorab-Markierung auf GitHub: eine Beta-/RC-Nummer ist kein Stable-Update
        asset = next((a for a in item.get("assets", []) if a.get("name") == ASSET), None)
        if asset is None or version <= parse_version(current):
            continue
        url = str(asset.get("browser_download_url", ""))
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(_DOWNLOAD_PREFIX):
            continue  # nur Downloads aus genau diesem Repository
        candidates.append((version, Release(version=str(item["tag_name"]).lstrip("v"), tag=str(item["tag_name"]),
                                            notes=str(item.get("body") or ""), url=url, sha256=_sha_for(item),
                                            page=str(item.get("html_url") or ""))))
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def check(timeout: float = 6.0) -> Release | None:
    """Neuere Version? Ohne Internet oder bei GitHub-Problemen: ``None`` (die Arbeit geht normal weiter)."""
    request = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                                       "User-Agent": "WIDS-Access-AI-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return pick_latest(json.loads(response.read().decode("utf-8")))
    except Exception as exc:  # offline, Proxy, Rate-Limit … – kein Fehler für die Bedienung
        log.info("Update-Prüfung nicht möglich: %s", exc)
        return None


def download(release: Release, timeout: float = 120.0) -> Path:
    """Lädt die EXE (nach Zustimmung) und prüft sie gegen die veröffentlichte SHA-256 – sonst wird sie verworfen."""
    if not release.sha256:
        raise UpdateError("Für dieses Release ist keine Prüfsumme veröffentlicht – ohne Prüfung wird nicht installiert.")
    folder = paths.data_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"WIDS-Access-AI-{release.version}.exe"
    part = target.with_suffix(".part")
    request = urllib.request.Request(release.url, headers={"User-Agent": "WIDS-Access-AI-Updater"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=timeout) as response, part.open("wb") as out:
        while chunk := response.read(1 << 16):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest().lower() != release.sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("Die heruntergeladene Datei stimmt nicht mit der veröffentlichten Prüfsumme überein – "
                          "sie wurde gelöscht, nichts wurde installiert.")
    os.replace(part, target)
    return target


def _clean_env(**extra: str) -> dict[str, str]:
    """Umgebung für eine ANDERE (bzw. neu gestartete) EXE: ohne die Entpack-Variablen dieses PyInstaller-Prozesses –
    sonst würde sie dessen (beim Beenden gelöschten) Entpackordner bzw. dessen Bibliotheken verwenden."""
    return {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1", **extra}


def _selftest(exe: Path) -> bool:
    env = _clean_env(WIDS_DATA_DIR=tempfile.mkdtemp(prefix="wids-update-selftest-"))
    try:
        return subprocess.run([str(exe), "--selftest"], env=env, timeout=180, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install(new_exe: Path, current: Path | None = None) -> Path:
    """Tauscht die laufende EXE gegen ``new_exe`` (nur gebaute EXE). Liefert den Pfad der bisherigen Version."""
    if current is None:
        if not getattr(sys, "frozen", False):
            raise UpdateError("Updates lassen sich nur in der gebauten EXE installieren (nicht im Quellcode).")
        current = Path(sys.executable)
    if not _selftest(new_exe):
        raise UpdateError("Die neue Version hat ihren Selbsttest nicht bestanden – die bisherige bleibt installiert.")
    previous = current.with_name(current.stem + ".vorher.exe")
    staged = current.with_name(current.stem + ".neu.exe")
    try:  # erst vollständig neben die EXE kopieren – ein Abbruch hier lässt die laufende Version unberührt
        shutil.copy2(new_exe, staged)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise UpdateError(f"Austausch fehlgeschlagen – die bisherige Version bleibt installiert ({exc}).") from exc
    previous.unlink(missing_ok=True)
    os.replace(current, previous)  # die laufende EXE darf umbenannt (nicht überschrieben) werden
    try:
        os.replace(staged, current)  # Umbenennen im selben Ordner: nie eine halb geschriebene EXE
    except OSError as exc:
        os.replace(previous, current)  # bisherige Version zurück
        raise UpdateError(f"Austausch fehlgeschlagen – die bisherige Version wurde wiederhergestellt ({exc}).") from exc
    return previous


def restart(current: Path | None = None, *, watch_s: float = 20.0) -> bool:
    """Startet die neue EXE (nach dem Freigeben der Einzelinstanz). Bricht sie in den ersten ``watch_s`` Sekunden mit
    einem Fehler ab, wird die bisherige Version zurückgelegt und gestartet. Liefert ``True``, wenn die neue läuft."""
    exe = current or Path(sys.executable)
    previous = exe.with_name(exe.stem + ".vorher.exe")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        child = subprocess.Popen([str(exe)], close_fds=True, creationflags=flags, env=_clean_env())
        code = child.wait(timeout=watch_s)
    except subprocess.TimeoutExpired:
        return True  # läuft
    except OSError:
        code = -1
    if code == 0 or not previous.exists():
        return code == 0
    log.error("Neue Version startete nicht (Code %s) – bisherige Version wird wiederhergestellt", code)
    failed = exe.with_name(exe.stem + ".fehlerhaft.exe")
    failed.unlink(missing_ok=True)
    os.replace(exe, failed)
    os.replace(previous, exe)
    subprocess.Popen([str(exe)], close_fds=True, creationflags=flags, env=_clean_env())
    return False


def cleanup_previous() -> None:
    """Nach einem erfolgreichen Start der neuen Version: alte Kopie und Downloads aufräumen (falls möglich)."""
    leftovers = list((paths.data_dir() / "updates").glob("WIDS-Access-AI-*"))  # Downloads (.exe/.part)
    if getattr(sys, "frozen", False):
        leftovers.append(Path(sys.executable).with_name(Path(sys.executable).stem + ".vorher.exe"))
    for leftover in leftovers:
        try:
            leftover.unlink(missing_ok=True)
        except OSError:  # z. B. noch gesperrt – beim nächsten Start erneut
            log.info("Update-Rest noch nicht löschbar: %s", leftover.name)

"""Prüft die gebaute EXE im Hintergrundmodus – mit echten Prozessen, Fenstern und Tastenkürzeln.

    python scripts\\check_exe_background.py dist\\WIDS-Access-AI.exe [--alt-w]

1. Start mit ``--background`` (genau der Befehl des Autostart-Eintrags): kein sichtbares Fenster
   (also kein Taskleisteneintrag), Symbol im Infobereich angelegt, Tastenkürzel registriert.
2. Zweiter Start: endet sofort, die laufende Instanz zeigt ihr Statusfenster.
3. Nur mit ``--alt-w`` (te.comp mit geöffneter Access-Übung, KEINE Freigabe): Tastenkürzel drücken → kein Fenster,
   kein Dialog, kein Fokuswechsel, keine KI-Anfrage → Verlauf „blockiert“ mit Ursache, Statuspunkt rot, die
   Übungsdatenbank bleibt unverändert. ``--alt-w-ausfuehren``: freigegebene Übung wird vollständig bearbeitet.
4. ``--quit``: die Instanz endet sauber, die Tastenkürzel sind wieder frei.
5. Erneuter Start (wie nach einem Windows-Neustart über den Autostart) und erneutes Beenden.

Einstellungen und Verlauf liegen in einem temporären Ordner. Die Prüfung nutzt eigene Kürzel
(Strg+Alt+Umschalt+…), damit eine schon laufende WIDS-Instanz mit Alt+W nicht stört.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOTKEYS = {"solve": "Ctrl+Alt+Shift+W", "analyze": "Ctrl+Alt+Shift+T", "ask": "Ctrl+Alt+Shift+F",
           "status": "Ctrl+Alt+Shift+S", "settings": "Ctrl+Alt+Shift+E", "dashboard": "Ctrl+Alt+Shift+D",
           "cancel": "Ctrl+Alt+Shift+X"}
SOLVE = (0x1 | 0x2 | 0x4, ord("W"))
SETTINGS = (0x1 | 0x2 | 0x4, ord("E"))
user32 = ctypes.windll.user32
results: list[tuple[str, bool]] = []
already_running: set[int] = set()  # z. B. eine umbenannte, noch laufende alte EXE meldet denselben Pfad


def check(what: str, ok: bool) -> bool:
    results.append((what, ok))
    print(("OK    " if ok else "FEHLER"), what)
    return ok


def wait_for(predicate, timeout: float):  # type: ignore[no-untyped-def]
    end = time.time() + timeout
    while time.time() < end:
        if result := predicate():
            return result
        time.sleep(0.25)
    return None


def family(exe: str) -> set[int]:
    """Alle Prozesse dieser EXE (PyInstaller startet einen Starter- und einen Programmprozess)."""
    target = os.path.normcase(exe)
    pids = set()
    for proc in psutil.process_iter(["exe"]):
        if proc.info["exe"] and os.path.normcase(proc.info["exe"]) == target:
            pids.add(proc.pid)
    return pids - already_running


def is_overlay(hwnd: int) -> bool:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    return bool(style & win32con.WS_EX_TRANSPARENT) and bool(style & win32con.WS_EX_TOOLWINDOW)


def overlays(pids: set[int]) -> list[int]:
    found: list[int] = []
    win32gui.EnumWindows(lambda h, _: (found.append(h) if win32process.GetWindowThreadProcessId(h)[1] in pids
                                        and win32gui.IsWindowVisible(h) and is_overlay(h) else None) or True, None)
    return found


def windows(pids: set[int], visible: bool | None = True) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []

    def collect(hwnd: int, _: object) -> bool:
        if win32process.GetWindowThreadProcessId(hwnd)[1] in pids and (visible is None or bool(win32gui.IsWindowVisible(hwnd)) == visible):
            if visible and is_overlay(hwnd):
                return True  # Statusanzeige: klickdurchlässig, ohne Taskleisteneintrag – kein „Fenster“ im Sinne der Prüfung
            found.append((hwnd, win32gui.GetClassName(hwnd), win32gui.GetWindowText(hwnd)))
        return True

    win32gui.EnumWindows(collect, None)
    return found


def hotkey_taken() -> bool:
    """Ist das Prüf-Kürzel von einem anderen Programm (der EXE) belegt?"""
    if user32.RegisterHotKey(None, 0xBEEF, SOLVE[0], SOLVE[1]):
        user32.UnregisterHotKey(None, 0xBEEF)
        return False
    return True


def press_solve(key: int = SOLVE[1]) -> None:
    keys = [win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_SHIFT, key]
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)


def start_background(exe: str, env: dict[str, str]) -> set[int]:
    subprocess.Popen([exe, "--background"], env=env)
    wait_for(hotkey_taken, 90)
    time.sleep(1.5)  # Infobereich-Symbol und Fensteraufbau abschließen lassen
    return family(exe)


def quit_instance(exe: str, env: dict[str, str], pids: set[int]) -> None:
    code = subprocess.run([exe, "--quit"], env=env, timeout=60).returncode
    gone = wait_for(lambda: not any(psutil.pid_exists(p) for p in pids), 60)
    check("--quit beendet die laufende Instanz vollständig", code == 0 and bool(gone))
    check("Tastenkürzel nach dem Beenden wieder frei", not hotkey_taken())


def latest_task(data: str) -> str:
    os.environ["WIDS_DATA_DIR"] = data
    from app.storage.database import Storage

    tasks = Storage().list_tasks(limit=1)
    return tasks[0].status if tasks else ""


def alt_w_flow(exe: str, data: str, pids: set[int]) -> None:
    """Ohne Freigabe: angehalten – kein Fenster, kein Dialog, kein Fokuswechsel, keine KI-Anfrage, roter Punkt."""
    before = win32gui.GetForegroundWindow()
    print(f"      Vordergrundfenster vor dem Kürzel: „{win32gui.GetWindowText(before)[:60]}“")
    press_solve()
    focus_changes: list[str] = []
    opened: list[str] = []

    def finished() -> str:
        if win32gui.GetForegroundWindow() != before:
            focus_changes.append(win32gui.GetWindowText(win32gui.GetForegroundWindow()))
        opened.extend(t for _, _, t in windows(pids))
        status = latest_task(data)
        return status if status not in ("", "erkannt", "geplant", "läuft") else ""

    status = wait_for(finished, 120)
    check("Alt+W ohne Freigabe: kein Fenster, kein Dialog, kein Fokuswechsel", not focus_changes and not opened)
    check(f"Alt+W ohne Freigabe: angehalten und im Verlauf gespeichert („{status}“)", status == "blockiert")
    check("Alt+W ohne Freigabe: Statuspunkt bleibt rot", bool(overlays(pids)))
    os.environ["WIDS_DATA_DIR"] = data
    from app.storage.database import Storage

    check("Alt+W ohne Freigabe: keine KI-Anfrage gesendet", Storage().usage_summary("total").requests == 0)


def alt_w_execute(data: str, pids: set[int]) -> None:
    """Freigegebene (gemerkte) te.comp-Übung: Alt+W liest, plant, sichert, führt aus und prüft – ohne Dialog."""
    before = win32gui.GetForegroundWindow()
    print(f"      Vordergrundfenster vor dem Kürzel: „{win32gui.GetWindowText(before)[:60]}“")
    press_solve()
    focus_changes: list[str] = []
    dots: list[int] = []

    def finished() -> str:
        fg = win32gui.GetForegroundWindow()
        if fg != before:
            focus_changes.append(win32gui.GetWindowText(fg))
        dots.extend(overlays(pids))
        status = latest_task(data)
        return status if status not in ("", "erkannt", "geplant", "läuft") else ""

    status = wait_for(finished, 600)
    if focus_changes or windows(pids):  # Diagnose: welches Fenster den Fokus nahm (Benutzer, te.comp, Access, WIDS?)
        def owner(title: str) -> str:
            hwnd = win32gui.FindWindow(None, title) if title else 0
            pid = win32process.GetWindowThreadProcessId(hwnd)[1] if hwnd else 0
            return psutil.Process(pid).name() if pid and psutil.pid_exists(pid) else "?"
        print("      Fokus wechselte zu:", "; ".join(f"„{t[:50]}“ ({owner(t)})" for t in dict.fromkeys(focus_changes)) or "-",
              "| WIDS-Fenster:", [t for _, _, t in windows(pids)])
    check("Alt+W (freigegeben): kein Fokuswechsel, kein Fenster", not focus_changes and windows(pids) == [])
    check("Alt+W (freigegeben): Statuspunkt erschienen", bool(dots))
    os.environ["WIDS_DATA_DIR"] = data
    from app.storage.database import Storage

    record = Storage().list_tasks(limit=1)[0]
    print(f"      Ergebnis: {status} – {record.title}; Sicherung: {record.backup_path}")
    usage = Storage().usage_summary("total")
    print(f"      KI-Verbrauch: {usage}")
    check("Alt+W (freigegeben): Aufgabe ausgeführt, Sicherung erstellt",
          status in ("erfolgreich", "unklar", "ausgeführt", "teilweise") and bool(record.backup_path)
          and Path(record.backup_path).exists())


def main() -> int:
    exe = os.path.abspath(sys.argv[1])
    already_running.update(family(exe))
    data = tempfile.mkdtemp(prefix="wids-exe-bg-")
    env = {**os.environ, "WIDS_DATA_DIR": data}
    os.environ["WIDS_DATA_DIR"] = data
    from app.core.settings import AppSettings
    from app.storage.database import Storage

    settings = AppSettings()
    settings.ui.first_run_done = True
    for name, combo in HOTKEYS.items():
        setattr(settings.hotkeys, name, combo)
    if "--alt-w-ausfuehren" in sys.argv:
        from app.agent.orchestrator import trust_key
        from app.vision import tecomp

        # jede in Access geöffnete Datenbank – nummerierte Übung, Workshop oder beliebiger Name
        exercise = tecomp.open_exercise() or ((tecomp.open_access_database(), "") if tecomp.open_access_database() else None)
        if exercise is None or not exercise[0]:
            print("FEHLER keine Datenbank in Access geöffnet")
            return 1
        from app.security import programs

        choice = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--zuordnen=")), "")
        if choice:  # wie im Dashboard: „Geöffnete Datenbank diesem Lernprogramm zuordnen …“ (genau dieser Pfad)
            target = next((p for p in programs.load_programs() if p.id == choice), None)
            if target is None:
                print(f"FEHLER unbekanntes Lernprogramm/Workshop „{choice}“")
                return 1
            programs.assign(settings.security.program_assignments, exercise[0], target)
            print(f"      Im Dashboard zugeordnet: {exercise[0]} → {target.caption}")
        program = programs.program_for(exercise[0], assignments=settings.security.program_assignments)
        if program is not None:  # neues Bedienkonzept: 24-h-Freigabe des Lernprogramms (wie im Dashboard bestätigt)
            programs.approve(settings.security.program_approvals, program)
            print(f"      Lernprogramm freigegeben (24 h): {program.caption} ({program.id})")
        else:
            settings.security.trusted_databases = [trust_key(exercise[0])]  # = vom Benutzer freigegebene Übung
            print("      Kein eindeutiges Lernprogramm – Übungsdatei einzeln freigegeben")
        budget = float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--budget=")), "0.60"))
        settings.ai.cost_limit_usd, settings.ai.cost_limit_period = budget, "total"  # harte Kostenbremse
        print(f"      Kostenbremse: {budget:.2f} $")
        print(f"      Freigegebene Übung: {exercise[0]}\n      Datenordner (Verlauf, Bericht): {data}")
    Storage().save_settings(settings)
    if not check("Prüf-Kürzel ist vor dem Start frei", not hotkey_taken()):
        return 1

    pids = start_background(exe, env)
    check("--background: Prozess läuft und hat die Tastenkürzel registriert", bool(pids) and hotkey_taken())
    check("--background: kein sichtbares Fenster (kein Taskleisteneintrag)", windows(pids) == [])
    check("--background: Symbol im Infobereich angelegt",
          any("TrayIcon" in cls for _, cls, _ in windows(pids, visible=None)))

    second = subprocess.run([exe], env=env, timeout=60).returncode
    check("Zweiter Start endet sofort (keine zweite Instanz)", second == 0 and family(exe) == pids)
    compact = wait_for(lambda: next((h for h, _, t in windows(pids) if t.endswith("kompakt")), None), 15)
    check("Zweiter Start zeigt das Statusfenster der laufenden Instanz", compact is not None)
    if compact:
        win32gui.PostMessage(compact, win32con.WM_CLOSE, 0, 0)
        check("Statusfenster schließen: wieder nur Infobereich", bool(wait_for(lambda: windows(pids) == [], 10)))

    press_solve(SETTINGS[1])  # Kürzel „Einstellungen öffnen“
    main = wait_for(lambda: next((h for h, _, t in windows(pids) if t.startswith("WIDS ACCESS AI ")), None), 15)
    check("Kürzel öffnet die Einstellungen im Hauptfenster", main is not None)
    if main:
        win32gui.PostMessage(main, win32con.WM_CLOSE, 0, 0)
        check("Hauptfenster schließen: Programm läuft im Infobereich weiter",
              bool(wait_for(lambda: windows(pids) == [], 10)) and hotkey_taken())

    if "--alt-w-ausfuehren" in sys.argv:
        alt_w_execute(data, pids)
    elif "--alt-w" in sys.argv:
        alt_w_flow(exe, data, pids)
    elif not any((p.info["name"] or "").lower() == "tecomptraining.exe" for p in psutil.process_iter(["name"])):
        # ohne te.comp: Alt+W liest höchstens lokal ein fremdes Fenster, sendet nichts und meldet das sichtbar
        before = win32gui.GetForegroundWindow()
        press_solve()
        shown = wait_for(lambda: overlays(pids), 30)
        time.sleep(1.0)
        check("Alt+W ohne te.comp: Statusanzeige erscheint, kein Fokuswechsel, kein Dialog",
              bool(shown) and win32gui.GetForegroundWindow() == before and windows(pids) == [])
    quit_instance(exe, env, pids)

    pids = start_background(exe, env)  # wie nach einem Windows-Neustart über den Autostart-Eintrag
    check("Neustart: läuft wieder im Hintergrund, Kürzel registriert", hotkey_taken() and windows(pids) == [])
    quit_instance(exe, env, pids)
    failed = [what for what, ok in results if not ok]
    print("ERGEBNIS:", "OK" if not failed else f"{len(failed)} FEHLER")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

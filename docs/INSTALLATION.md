# Installation und Einrichtung (Windows)

## Voraussetzungen
- **Windows 10 oder 11** (64 Bit).
- **Microsoft Access** (Office/Microsoft 365, Access 2016 oder neuer). WIDS steuert Access selbst – ohne installiertes
  Access funktioniert nur der KI-Assistent für Fragen. Access muss **einmal von Hand gestartet** worden sein (Lizenz-
  und Erststartdialoge bestätigt).
- Optional **te.comp-training** für Lernprogramme und Workshops; WIDS funktioniert auch mit beliebigen eigenen
  Access-Übungsdatenbanken.
- Ein **Anthropic-API-Schlüssel** mit Guthaben (siehe unten). Ohne Schlüssel startet WIDS, kann aber nichts planen.

## Variante A: fertige EXE (empfohlen)
1. `WIDS-Access-AI.exe` aus dem GitHub-Release herunterladen (keine Installation nötig) und z. B. nach
   `%LOCALAPPDATA%\Programs\WIDS-Access-AI\` kopieren.
2. Starten. Windows SmartScreen kann bei einer neuen, unsignierten EXE warnen („Weitere Informationen → Trotzdem
   ausführen“) – nur bei einer EXE aus dem offiziellen Release tun und die SHA-256-Prüfsumme vergleichen.
3. Optional in den Einstellungen „Mit Windows starten (im Hintergrund)“ aktivieren.
4. Optional im Dashboard **„System & KI testen“** klicken: zuerst ein kostenloser Test auf einer temporären
   Testdatenbank, danach – nur nach Bestätigung – ein KI-Test mit höchstens 0,01 $.

### Updates
Ab Beta 3 prüft WIDS beim Start (still, ohne Internet einfach nicht) die Releases dieses Repositorys. Eine neue Version
erscheint im Dashboard unter „System, Updates & Diagnose“; **„Update installieren“** lädt die EXE erst nach Zustimmung,
prüft sie gegen die veröffentlichte SHA-256-Prüfsumme, startet sie testweise und ersetzt dann die laufende EXE. Die
bisherige Version bleibt als `WIDS-Access-AI.vorher.exe` liegen, bis die neue eine Minute stabil läuft; startet die
neue nicht, wird die bisherige automatisch zurückgelegt. Einstellungen, Schlüssel und Verlauf bleiben unverändert.
Von **Beta 1/Beta 2** auf Beta 3 einmal von Hand aktualisieren: alte Instanz über das Infobereich-Symbol → „Beenden“,
EXE durch die neue ersetzen, starten.

## Variante B: aus dem Quellcode
```powershell
git clone https://github.com/samito-ai/WIDS-ACCESS-AI.git
cd WIDS-ACCESS-AI
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1   # Python 3.11 nötig
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```
Eigene EXE bauen: `python build.py` (PyInstaller, inklusive Selbsttest).

## Anthropic-API-Schlüssel sicher einrichten
1. Auf <https://console.anthropic.com> ein Konto anlegen, Guthaben aufladen und unter „API Keys“ einen **eigenen**
   Schlüssel erzeugen. Empfehlung: einen eigenen Workspace mit **Ausgabenlimit** (Spend Limit) für WIDS verwenden.
2. WIDS öffnen → **Einstellungen** → Schlüssel einfügen → **„Verbindung testen“** (eine sehr kleine, bezahlte Anfrage,
   ca. 0,001 $). Der Schlüssel wird dabei gespeichert.
3. Gespeichert wird in der **Windows-Anmeldeinformationsverwaltung** (Eintrag `WIDS-Access-AI/api-key-anthropic`,
   von Windows für dein Benutzerkonto verschlüsselt). Nur wenn diese nicht verfügbar ist, nutzt WIDS ersatzweise eine
   mit Windows-DPAPI für dein Benutzerkonto **verschlüsselte** Datei unter `%LOCALAPPDATA%\WIDS-Access-AI` (die
   Einstellungen zeigen, welche Ablage aktiv ist). Nie im Klartext, nie im Verlauf oder Protokoll. Entfernen:
   Systemsteuerung → Anmeldeinformationsverwaltung → Windows-Anmeldeinformationen (bzw. in WIDS „Schlüssel löschen“).
4. Die **Kostenlimits** stehen im Dashboard unter „API-Kosten“: Budget je Aufgabe (Standard 1 $) und ein Limit je
   Tag/Monat/gesamt. Eine typische Übung kostet etwa 0,05–0,35 $. Die Limits sind eine lokale Vorsichtsmaßnahme
   (berechnet aus Tokens × Listenpreis), keine Abrechnungsgarantie – das Ausgabenlimit in der Anthropic-Konsole
   bleibt der verlässliche Schutz.
5. Den Schlüssel **nie** weitergeben, in Screenshots zeigen oder in Issues/Repositories einfügen. Bei Verdacht sofort in
   der Anthropic-Konsole widerrufen.

## Erste Schritte
1. Übung in Access öffnen (te.comp-Übung, Workshop oder eigene Datenbank – der Name ist egal) und **Alt+W** drücken.
   Es gibt keine Freigaben mehr: Alt+W ist die bewusste Aktion. Löschende Schritte werden nur bei geöffnetem
   Dashboard einzeln bestätigt, im Hintergrund hält WIDS dann an. Statuspunkt oben links: gelb = arbeitet, grün = vollständig geprüft,
   rot = nicht abgeschlossen (Grund im Verlauf).
2. Vor jeder Änderung wird automatisch eine Sicherung erstellt (Verlauf → „Sicherung dieser Aufgabe wiederherstellen“).

## Nur für Übungen
WIDS sperrt sich, wenn eine Prüfungsumgebung läuft (z. B. Next Exam, Safe Exam Browser), und klickt nie „Prüfen“,
„Abschließen“ oder „Upload“. Die Verantwortung für den Einsatz liegt beim Benutzer.

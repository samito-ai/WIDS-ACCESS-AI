# WIDS ACCESS AI v1.0.0 Beta 3 (Build 17.11.2026 09:39)

Smart AI, Auto-Repair, Live-Testing & Auto-Updates. Kostenlos und Open Source (MIT).
**Nur für Übungen – nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 254 200 Bytes | `D7603D54A4CD389F07C8C4DB16693D42EE5EA53FADC77CBAAF5187F4427A03D3` |

Prüfen in PowerShell: `Get-FileHash .\WIDS-Access-AI.exe -Algorithm SHA256`. Die EXE ist nicht signiert (SmartScreen
kann warnen).

**Von Beta 1/Beta 2 einmal von Hand aktualisieren:** alte Instanz über das Infobereich-Symbol → „Beenden“, EXE durch
die neue ersetzen, starten. Einstellungen, API-Schlüssel und Verlauf bleiben erhalten. Ab Beta 3 meldet das Dashboard
neue Versionen selbst.

## Neu

- **System & KI testen** (Dashboard): kostenloser Teil auf einer temporären Testdatenbank – Access, COM/DAO, Erkennung
  der geöffneten Übung, sicherer Testplan, unabhängige Prüfung, Sicherung und Bereinigung; echte Übungs- oder
  Schuldatenbanken werden nie angefasst. Der KI-Teil (Schlüssel, Anmeldung, Modell, eine echte kleine Anfrage,
  Verarbeitung) kostet höchstens 0,01 $ und startet erst nach Bestätigung. Ergebnis: Bestanden (bereit), Teilweise
  (eingeschränkt) oder Fehlgeschlagen mit konkreter Ursache.
- **Live-Fortschritt** aus den echten Arbeitsschritten: Aufgabenstellung erkannt → Datenbank verbunden → KI analysiert →
  Lösungsplan → Schritt k von n → Ergebnisprüfung, mit erledigt/offen, bestätigten Teilergebnissen, Laufzeit und
  Selbstkorrektur-Status. Keine Ladeanimation, keine zusätzlichen Pop-ups; Alt+W bleibt im Hintergrund.
- **Selbstkorrektur mit Ursache:** WIDS ordnet jeden Fehlschlag ein (Aufgabenerkennung, KI-Plan, Access-Ausführung,
  Ergebnisprüfung), bessert gezielt nur das Nötige nach, behält bestätigte Schritte und bleibt bei höchstens drei
  Zyklen. Einen grünen Status gibt es weiterhin nur nach vollständig bestandener Prüfung.
- **API-Kosten nur im Dashboard:** letzte Aufgabe, Sitzung, heute, gesamt (lokaler Verlauf), Modell und Tokens.
  Tokens stammen aus den API-Antworten, Kosten sind daraus mit Listenpreisen berechnet (Schätzung). Budget je Aufgabe
  (Standard 1 $) und Limit je Tag/Monat/gesamt werden vor jeder Anfrage geprüft; die Ausgabe ist gedeckelt. Das
  Guthaben beim Anbieter kann WIDS nicht abrufen; das Limit ist eine lokale Vorsichtsmaßnahme.
- **Automatische Updates** aus diesem Repository: Hinweis mit Version und Notizen, Download erst nach Zustimmung,
  SHA-256-Prüfung gegen die veröffentlichte Prüfsumme, Selbsttest, Austausch beim Neustart, automatischer Rückfall
  auf die bisherige Version, wenn die neue nicht startet. Ohne Internet läuft alles normal.
- **Diagnose nur bei schweren Fehlern** (Absturz, nicht behebbarer Access-Fehler, Sicherung/Wiederherstellung,
  KI-Verarbeitung, nach allen Korrekturen nicht lösbare Aufgabe) – anonymisiert, ohne Schlüssel, Namen, Pfade oder
  Datenbankinhalte. „Diagnose exportieren“ im Dashboard.

## Getestet

- 600 automatische Tests bestanden, 0 fehlgeschlagen (inkl. 82 Pipeline-, Access-Integrations-, GUI- und E2E-Tests,
  Selbstkorrektur-, Verifier-, Kosten-/Budget- und Update-/Prüfsummen-Tests); 23 übersprungen: 21 kostenpflichtige
  Live-KI-Tests (zwei davon gezielt echt ausgeführt, siehe unten) und 2 Tests, die ein laufendes te.comp brauchen.
  Ruff und Mypy ohne Befund.
- Unabhängiges Codex-Review: 10 Funde, 9 behoben mit Regressionstests (u. a. Diagnose ohne Netzpfade/Inhalte,
  Neustart nach Update ohne alte Entpack-Umgebung, Systemtest im eigenen COM-Thread, Budget bei Modellen ohne Preis,
  Aufgabenzuordnung der Kosten, vollständige Zwischenkopie beim Austausch, Rückfall bei Startfehler, Fortschritt nimmt
  widerrufene Bestätigungen zurück); einer bewusst nicht (Austausch der geladenen Datei durch einen Prozess desselben
  Benutzers – dieser könnte ohnehin die EXE selbst ersetzen).
- **Echte KI-Tests (Anthropic, claude-opus-5-5), zusammen 0,32 $:** „System & KI testen“ bestanden (je Lauf 95 + 34
  Tokens ≈ 0,001 $); te.comp 0402/U4 an einer Übungskopie (zwei Abfragen + Tabellensortierung): erfolgreich, jede
  Anforderung unabhängig in Access bestätigt, 0,20 $; te.comp 0501/U5 (gruppierter Bericht): Bericht, Datenquelle und
  Gruppierung bestätigt, die Formatierung „gemäß Lösungsabbildung“ ohne Abbildung ehrlich als nicht nachweisbar
  gemeldet (kein grüner Status), 0,11 $. Kostenanzeige stimmt mit den Tokens der API überein; Aufgabenbudget und
  Gesamtlimit sperren vor dem Senden.
- EXE: Selbsttest (auch ohne Python in der Umgebung), 15/15 Hintergrundprüfungen (Infobereich, keine zweite Instanz,
  Kürzel, Alt+W ohne Fokuswechsel, sauberes Beenden), Update-Ablauf mit echten Dateien (Download von GitHub,
  SHA-256, Selbsttest der neuen EXE, Austausch, gesicherte Vorversion).

## Bekannte Grenzen

- Die EXE ist nicht signiert (SmartScreen-Hinweis beim ersten Start).
- Von Beta 1/2 ist einmal ein manuelles Update nötig; der automatische Weg (Beta 3 → spätere Version) ist mit echten
  Dateien und Unit-Tests geprüft, ein echtes Release-zu-Release-Update kann erst mit der nächsten Version stattfinden.
- Der EXE-Lauf „Alt+W mit automatischer Access-Verbindung“ in einer echten te.comp-Übung wurde für Beta 3 nicht
  wiederholt (te.comp war nicht geöffnet); der Ablauf ist durch GUI-/Pipeline-Tests abgedeckt und seit Beta 2
  unverändert – neu ist nur die Erkennung bei langen Pfaden.
- Kosten sind aus den Tokens mit Listenpreisen berechnet; für Modelle ohne hinterlegten Preis sendet WIDS bei aktivem
  Limit nichts. Das Guthaben beim Anbieter wird nicht abgefragt.
- Formatierungen „gemäß Abbildung“ lassen sich ohne die Abbildung nicht nachweisen und bleiben dann „nicht
  abgeschlossen“.

# WIDS ACCESS AI v1.0.0 Beta 4 (Build 17.11.2026)

Belegte Antworten, keine Blockade durch einzelne Löschschritte, nachgemessene Berichtslayouts, freigegebene
Lernordner. Kostenlos und Open Source (MIT). **Nur für Übungen – nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 272 513 Bytes | `ED21CBE40F2E50D55F278B4C5C76C2CDDD14ED6397DC288B77850FC8E1354D5E` |

Von Beta 3: im Dashboard „Update installieren“ (SHA-256-Prüfung, Selbsttest, Rückfall). Von Beta 1/2: einmal von
Hand ersetzen. Die EXE ist nicht signiert (SmartScreen kann warnen).

## Neu

- **Rechenfragen:** WIDS rechnet selbst exakt (kaufmännisch gerundet) und kontrolliert mit einem zweiten Rechenweg;
  jede Zahl muss wörtlich in der Aufgabe stehen – erfundene Werte werden abgewiesen.
- **Antworten nur mit Beleg:** Multiple Choice mit gemeldeter Unsicherheit, ohne Begründung je Option oder ohne
  richtige Option gilt als „nicht gesichert“ (roter Punkt), nie als beantwortet. Datenfragen weiterhin per nur
  lesender Abfrage aus der Datenbank, erneut nachgeprüft.
- **Freigegebene Lernordner** (Einstellungen): nur lesend, nur in der Aufgabe genannte Dateien (Excel, Text, CSV,
  Word); Laufwerke, System-, Programm- und Anmeldedaten-Ordner ausgeschlossen.
- **Berichtslayout nachgemessen:** Beschriftung links neben dem richtigen Feld, Reihenfolge, keine Überlappung;
  mehrspaltiger Druck getrennt geprüft.

## Behoben

- **Ein Löschschritt blockierte den ganzen Plan** (Golfclubs): Unabhängige Schritte laufen jetzt mit Sicherung und
  Prüfung; die Löschung wartet auf Bestätigung im Dashboard; Ergebnis „teilweise“ mit Grund. Schritte am selben
  Objekt nach einer nicht ausgeführten Löschung laufen ebenfalls nicht (keine doppelten Daten).

## Getestet

- 635 automatische Tests bestanden, 0 fehlgeschlagen, 21 übersprungen (kostenpflichtige Live-KI-Tests); darin u. a.
  Negativtests mit absichtlich falschen Layouts, vertauschten Beschriftungen, falschen Druckspalten, fehlendem JOIN,
  erfundenen Zahlen und unsicheren Antworten; die te.comp-Integrationstests liefen mit einer echten geöffneten Übung.
  Ruff und Mypy ohne Befund.
- EXE: Selbsttest (auch ohne Python), 14/14 ausgeführte Hintergrundprüfungen (Infobereich, keine zweite Instanz,
  Kürzel, sauberes Beenden, Neustart); die Prüfung „Alt+W ohne te.comp“ entfiel, weil te.comp geöffnet war.
- Keine neuen kostenpflichtigen KI-Tests (ohne neue Budgetfreigabe): **0 $**.

## Bekannte Grenzen

- Das unabhängige Codex-Review war wegen eines Nutzungslimits nicht verfügbar; stattdessen eigene Prüfung nach
  derselben Checkliste (3 Funde behoben: Größenbegrenzung der Rechnungen, Schritte nach nicht ausgeführter Löschung,
  zu strenge Antwortbewertung bei praktischen Aufgaben).
- PDF-Lernunterlagen werden nicht gelesen (kein zusätzliches Paket); Word, Excel, Text und CSV schon.
- Antworten werden angezeigt, nicht automatisch in te.comp eingetragen; „Prüfen“ klickt weiterhin der Benutzer.
- Formatierungen „gemäß Abbildung“ ohne lesbare Abbildung bleiben „nicht abgeschlossen“.

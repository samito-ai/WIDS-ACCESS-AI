# WIDS ACCESS AI 1.0.0 (17.11.2026)

**Erste Stable-Version.** Übung öffnen, Alt+W drücken: WIDS erledigt die Aufgabe in Access, sichert vorher und prüft
danach jede Anforderung unabhängig. Kostenlos, Open Source (MIT). **Nur für Übungen, nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 284 204 Bytes | `20866CFCFDE18F73E35457129AE3B582FD0429020E978BEEFFF57FE5200F7D8C` |

- Neu: EXE herunterladen und starten (keine Installation). Anleitung: `docs/SCHNELLSTART.md`.
- Von Beta 3 bis 7: im Dashboard „Update installieren“. Von Beta 1 oder 2: einmal von Hand ersetzen.
- Rückfallversion: [v1.0.0-beta.7](https://github.com/samito-ai/WIDS-ACCESS-AI/releases/tag/v1.0.0-beta.7) bleibt
  verfügbar.

## Was 1.0.0 gegenüber Beta 7 ändert

1.0.0 bringt keine neuen Funktionen, sondern stabilisiert:

- **Datensicherheit** (aus dem unabhängigen Codex-Review): Die Automatik-Freigabe für löschende Schritte wird direkt
  vor dem Schritt erneut geprüft. Gespeicherte Abfragen werden aufgelöst, damit verknüpfte Tabellen und Abhängigkeiten
  sichtbar sind. Änderungen an verknüpften Tabellen werden blockiert. Ein erneuter Start fügt nur fehlende Datensätze
  ein. Eine SQL-Reparatur, die eine Bedingung entfernt, gilt nicht als bestanden.
- **Ehrliche Meldungen:** Keine Hinweise mehr auf Bestätigungen, die es nicht gibt. Blockierte Schritte sagen, warum,
  und was zu tun ist.
- **Einstellungen:** Keine wirkungslosen Optionen mehr.
- **Updates:** Die Stable-Version bekommt nur Stable-Updates angeboten.

## Unterstützt

Tabellen, Beziehungen, Abfragen (auch Aktionsabfragen), Formulare, Berichte (Layout nachgemessen), Datensätze
einfügen/ändern/löschen, Import und Export (Excel, Text, Access), Datenfragen, Rechenfragen, Multiple Choice und
mehrteilige Aufgaben. Details: README, Abschnitt „Unterstützte Aufgaben“.

## Geprüft

- **653 automatische Tests bestanden, 0 fehlgeschlagen** (24 übersprungen: 21 kostenpflichtige Live-KI-Tests ohne
  Budgetfreigabe, 2 Tests, die ein laufendes te.comp brauchen, 1 Verknüpfungstest ohne Adminrechte). Darin enthalten:
  Access-Integration, Ende-zu-Ende-Abläufe, Oberfläche und die Regressionstests der Praxisfälle. Ruff und Mypy ohne
  Befund. Kein Test wurde entfernt oder deaktiviert.
- **Praxis-Nachspiel mit echtem Access:** echte KI-Pläne früherer Alt+W-Läufe, ausgeführt mit 1.0.0 an frischen
  Kopien der te.comp-Vorlagen. Golfclubs (Datensatz ergänzen, Club löschen, Formularfilter, Datenfrage), Students
  (Bericht im Spaltenlayout), Filterabfragen und Excel-Import (87 Datensätze): erfolgreich, ohne Rückfrage, nur die
  verlangten Daten geändert. Eine Übung mit schon vorhandener Abfrage: ehrlich „teilweise“, nichts überschrieben.
  Jede Sicherung war inhaltsgleich mit dem Ausgangsstand, jede Wiederherstellung stellte ihn exakt her.
- **EXE:** Selbsttest (auch ohne Python), 15/15 Hintergrundprüfungen (Infobereich, keine zweite Instanz, Kürzel,
  Alt+W ohne Fokuswechsel und ohne Dialog, sauberes Beenden, Neustart).
- **Unabhängiges Codex-Review** des Beta-7-Stands und dieser Version: 10 Befunde. Laut Nachprüfung sind 7 vollständig
  und 3 bis auf kleine Restgrenzen behoben (siehe unten); zwei Folgebefunde aus der Nachprüfung sind ebenfalls behoben.
  Alle Fixes sind mit Tests abgesichert.

## Bekannte Grenzen

- Die EXE ist nicht signiert; SmartScreen warnt beim ersten Start.
- Formatierungen „gemäß Abbildung“ ohne lesbare Werte bleiben „nicht abgeschlossen“.
- PDF-Unterlagen werden nicht gelesen. Antworten zeigt WIDS an, trägt sie aber nicht in te.comp ein.
- Restgrenzen aus dem Review: Beim erneuten Start werden Felder vom Typ Langer Text und Gleitkommazahl nicht
  verglichen; bei mehr als 50 000 Ergebniszeilen vergleicht die Abfrageprüfung nicht mehr; der Verbindungstest
  (ca. 0,001 $) erscheint nicht in der Kostenübersicht.
- Getestet auf einem Windows-11-PC mit Access (Microsoft 365); ein zweiter PC stand nicht zur Verfügung.
- Kein neuer kostenpflichtiger KI-Lauf für diese Version. Die Ausführung wurde mit den echten KI-Plänen früherer Läufe
  an frischen Übungskopien mit echtem Access nachgeprüft.

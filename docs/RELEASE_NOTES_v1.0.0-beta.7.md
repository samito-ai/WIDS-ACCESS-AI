# WIDS ACCESS AI v1.0.0 Beta 7 (17.11.2026)

**Alt+W drücken – Aufgabe wird erledigt – Ergebnis ist überprüfbar.** Keine Rückfragen mehr; der Schutz ist technisch.
Kostenlos, MIT. **Nur für Übungen – nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 281 451 Bytes | `759A58B409EC71FB6E2B599504839E95686013069C6194913AF8990B6CA3DE44` |

Von Beta 3–6: im Dashboard „Update installieren“. Von Beta 1/2: einmal von Hand ersetzen. Kurzanleitung:
`docs/SCHNELLSTART.md`, Vorführung mit eigenen Testdaten: `docs/DEMO.md`.

## Neu in Beta 7

- **Keine Freigabe- oder Bestätigungsfenster mehr** – auch nicht im Dashboard. Alt+W ist die Zustimmung für die
  geöffnete Übung. Vor jeder Änderung: Sicherung (als Datei geprüft); danach unabhängige Prüfung jeder Anforderung.
- **Automatisch:** Datensätze mit Bedingung löschen/ändern (höchstens 25), benannte Tabellen/Felder/Abfragen/
  Formulare/Berichte löschen, Aktionsabfragen (Mengengrenze prüft die Ausführung).
- **Nicht geraten, sondern nur dieser Schritt blockiert:** „alle Datensätze“, mehr Treffer als die Grenze,
  Löschweitergabe, verknüpfte Tabellen, verlustbehaftete Typänderung, unbekanntes Ziel. Unabhängige Schritte laufen.
- **Demo** (`docs/DEMO.md`): fünf Aufgaben auf der eingebauten Beispieldatenbank – Abfrage, Datenfrage, Rechenfrage,
  Ergänzen + Löschen, Bericht im Spaltenlayout.

## Seit Beta 3 behoben (aus echten Praxisläufen)

- Golfclubs: Sperre bei Lesezugriffen (exklusiv geöffnete Datei), unnötige Abhängigkeit Filter ↔ Löschung,
  Datensatzwerte als „fehlende Objekte“, „Wartet auf Bestätigung“; erneuter Start fügt nichts doppelt ein.
- Students: Berichtslayout (Beschriftung links vom richtigen Feld, Reihenfolge, Überlappung, Druckspalten).
- E-01 SI: alternative Hintergrundfarbe im richtigen Berichtsbereich (seit Beta 2, weiter getestet).
- Access-Neustart: tote Verbindung wird erkannt und neu aufgebaut; lange Pfade im Fenstertitel werden erkannt.
- Antworten: Datenfragen aus der Datenbank, Rechenfragen deterministisch, unsichere Antworten nie „beantwortet“.

## Geprüft

- 648 automatische Tests bestanden, 0 fehlgeschlagen (24 übersprungen: 21 kostenpflichtige Live-KI-Tests, 2
  te.comp-Live-Tests ohne laufendes te.comp, 1 Verknüpfungstest ohne Adminrechte); Ruff und Mypy ohne Befund.
- EXE: Selbsttest (auch ohne Python), 15/15 Hintergrundprüfungen (Start im Infobereich, keine zweite Instanz,
  Kürzel, Alt+W ohne Fokuswechsel und ohne Dialog, sauberes Beenden, Neustart).
- Unabhängiges Codex-Review der Beta-5/6-Änderungen (19 Funde behoben). Für Beta 7 war Codex nicht verfügbar
  (Nutzungslimit); eigene Prüfung nach derselben Checkliste: Löschweitergabe und verknüpfte Tabellen ausgeschlossen.
- Keine kostenpflichtigen KI-Tests in dieser Runde (0 $). Echte KI-Läufe zuletzt mit Beta 3 (0402/U4 erfolgreich,
  0501/U5 ehrlich „nicht nachweisbar“ für die Formatierung nach Abbildung).

## Bekannte Grenzen

- Die EXE ist nicht signiert (SmartScreen-Hinweis beim ersten Start).
- Formatierungen „gemäß Abbildung“ ohne lesbare Abbildung bleiben „nicht abgeschlossen“.
- PDF-Lernunterlagen werden nicht gelesen; Antworten werden angezeigt, nicht in te.comp eingetragen.
- Ein echter Alt+W-Lauf in der Golfclubs-Übung mit Beta 7 steht noch aus.

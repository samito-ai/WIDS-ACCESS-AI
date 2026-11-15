# WIDS ACCESS AI v1.0.0 Beta 1

Erste öffentliche Beta. Kostenlos und Open Source (MIT). **Nur für Übungen – nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 194 611 Bytes | `5641C13E1C9307E241687132C3BE4023CF1535139D4E564515E8C490C306F424` |

Prüfen in PowerShell: `Get-FileHash .\WIDS-Access-AI.exe -Algorithm SHA256`

Die EXE ist **nicht signiert**; Windows SmartScreen kann beim ersten Start warnen. Voraussetzungen: Windows 10/11,
Microsoft Access (Desktop), eigener Anthropic-API-Schlüssel ([Einrichtung](INSTALLATION.md)).

## Neu

- Universeller Access-Assistent: arbeitet mit jeder Access-Datei, egal wie sie heißt (auch Netzwerkpfade).
- 24-Stunden-Freigabe je Lernprogramm bzw. Workshop; nicht eindeutig erkannte Dateien werden einmal im Dashboard
  zugeordnet.
- Ruhemodus: im Hintergrund keine Dialoge und keine Benachrichtigungen; 2 × 2-Pixel-Statuspunkt.
- Begrenzte Selbstkorrektur mit Fortschrittskontrolle, Wechsel des Lösungswegs bei Stillstand, keine doppelten
  Datensätze.
- Berichte: mehrspaltiger Druck, Berichtsbreite, Datenquelle und Steuerelementinhalt, Etiketten, Seite einrichten;
  überlappende Felder werden nach Änderungen automatisch neu geordnet.
- Prüfungsschutz: Next Exam, Safe Exam Browser, Sperrbrowser und Prüfungsmodi sperren die Automatisierung.

## Behoben

- Etiketten druckten in der falschen Richtung (jetzt wie der Etikettenassistent: erst quer, dann nach unten).
- Der ständig laufende Windows-Dienst des Safe Exam Browser wurde fälschlich als laufende Prüfung erkannt.
- Das Wort „Schularbeit“ im Dateinamen sperrte gewöhnliche Übungsdatenbanken.
- Datenblatt-Änderungen fehlten in der Änderungsliste des Berichts.
- Überlappende Berichtsfelder nach einer Änderung.

## Getestet

- 556 automatische Tests bestanden (davon ein Teil mit echtem Microsoft Access), Ruff und Mypy ohne Befund.
- EXE-Selbsttest und 15 Hintergrundprüfungen der fertigen EXE bestanden.
- Stichproben mit echter KI an Übungsdatenbanken, einer frei benannten Datenbank und einem Workshop erfolgreich.

## Bekannte Einschränkungen

- Beta: Für unbekannte Aufgabentypen gibt es keine Erfolgsgarantie; nur vollständig geprüfte Ergebnisse gelten als
  „erfolgreich“, sonst meldet WIDS „Nicht abgeschlossen“ mit Ursache.
- Die Selbstkorrektur des Berichtslayouts wurde nach der letzten Änderung nur offline geprüft.
- Die Next-Exam-Erkennung wurde nicht gegen eine echte Next-Exam-Installation getestet (Programmnamen angenommen).
- ODBC gegen einen echten SQL Server und Probedruck auf Etikettenpapier sind ungetestet.
- Nur Windows; Microsoft Access muss installiert und einmal von Hand gestartet worden sein.

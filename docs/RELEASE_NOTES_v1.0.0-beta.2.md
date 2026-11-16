# WIDS ACCESS AI v1.0.0 Beta 2 (Final Release Candidate, Build 16.11.2026 21:54)

Stabilitäts-Beta: Fehler aus echten Übungsläufen behoben, kein Freigabesystem mehr, schneller.
Kostenlos und Open Source (MIT). **Nur für Übungen – nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 208 928 Bytes | `A55BAA707DC1E38D720A9188FD436F8F06A634D418E3A5EC23225864F0B9B674` |

Prüfen in PowerShell: `Get-FileHash .\WIDS-Access-AI.exe -Algorithm SHA256`. Die EXE ist nicht signiert (SmartScreen
kann warnen). Vor dem Start der neuen Version eine laufende ältere Instanz über das Infobereich-Symbol beenden.

## Neu

- **Keine Freigaben mehr nötig:** Alt+W ist die bewusste Aktion des Benutzers – kein Freigeben von
  Lernprogrammen, Workshops oder Dateien, keine Ablaufzeiten. Prüfungsschutz, Sicherung vor jeder Änderung und
  die Bestätigung löschender Schritte bleiben. Alte gespeicherte Freigaben werden ignoriert.
- **Excel-Import mit Datentypen und Feldbeschreibungen** (.xls/.xlsx); geprüft werden Feldnamen, Typen, Beschreibungen
  und ob alle Zeilen angekommen sind. Freitextspalten (Anmerkung, Bemerkung, Notiz …) werden „Langer Text“.

## Behoben (aus echten Übungsläufen)

- „Jede zweite Zeile farblich unterlegen“ (te.comp 0 von 1 Punkt): WIDS färbte das Tabellen-Datenblatt statt des
  Detailbereichs des Berichts. Neu: Hintergrund- und alternative Hintergrundfarbe für Berichts-/Formularbereiche;
  Bildschirminfo-Übungen lesen die te.comp-Info; die Prüfung vergleicht die gespeicherte Farbe exakt.
- Nach einem Access-Neustart durch te.comp scheiterte jedes weitere Alt+W – die tote Verbindung wird jetzt erkannt,
  geschlossen und neu aufgebaut.
- Import mit abweichenden Spaltenüberschriften ordnete nicht mehr stillschweigend nach Position zu.
- Strengere, unabhängige Ergebnisprüfung: Abfragen liefern dieselben Datensätze wie verlangt (eigene DAO-Verbindung),
  Aktualisierungen werden an genau den betroffenen Datensätzen geprüft, importierte Werte stehen in den richtigen Feldern,
  Summen im richtigen Berichtsbereich, Regeln der bedingten Formatierung exakt (Bedingung, Operator, Farben),
  Steuerelement-Größe/-Lage/-Sichtbarkeit/-Farben, Berichtsgruppen, Navigationsziele samt Typ.
- Tabellenkopien übernehmen Nachschlagefelder und die Gültigkeitsregel der Tabelle.
- Selbstkorrektur legt keine doppelten Steuerelemente („…_2“) mehr an; ein späteres Neuanlegen eines Objekts löst die
  Neuprüfung früherer Schritte aus.
- „Mit Windows starten“ funktioniert auch auf frischen Windows-Profilen ohne Autostart-Schlüssel.
- Eine verworfene Access-Verbindung wird ordentlich geschlossen (keine verwaisten Instanzen, keine hängenden Folgeläufe).

- Eine nicht ausführbare Abfrage der Übungsdatenbank (fehlende Eingabetabelle) wurde der KI als normale Datenquelle
  gemeldet → Formular scheiterte, die Reparatur verlor die zweite Layoutspalte. Solche Abfragen werden jetzt erkannt,
  gekennzeichnet und als Datenquelle abgewiesen.
- Neue Formulare werden je Layoutspalte und auf die verlangten Spaltenbreiten geprüft (mit Standardbreite als Beleg).
- Lernplattform-Hinweise („Informieren Sie sich …“) wurden als Anforderung gezählt und verhinderten „erfolgreich“.
- Verlustfreie Typänderungen (z. B. Kurzer → Langer Text) wurden im Hintergrund als riskant angehalten; verkleinernde
  Umwandlungen halten weiterhin an.
- Die Anforderungsprüfung sah die Spaltenüberschriften der Quelldatei nicht („Feldnamen aus Excel übernehmen“).

## Schneller

- Keine zweite KI-Analyse bzw. Bilderkennung für unveränderten Inhalt (spart 15–60 s und Kosten je Wiederholung).
- Ein nicht erreichbares Netzlaufwerk (W:) blockiert höchstens 1,5 s statt bis zu einer Minute.

## Getestet

- 580 automatische Tests bestanden (nach Entfernen der Freigabe-Tests) + 82 Pipeline-Tests im Nachlauf (inkl. Access-Integrations- und E2E-Tests, Gegentests mit absichtlich falschen
  Ergebnissen), Ruff und Mypy ohne Befund; EXE-Selbsttest (auch ohne Python in der Umgebung) und 15/15 Hintergrundprüfungen bestanden.
- Echte Läufe mit te.comp (15.11.2026): Workshop „CD-Sammlung“ Schritt 1 und 2 – von te.comp selbst als richtig
  bewertet; Formularübung mit Steuerelementlayout – 7 von 8 Anforderungen unabhängig bestätigt.

## Bekannte Einschränkungen

- Nicht live mit echter KI nachgeprüft (kein API-Guthaben): „Jede zweite Zeile farbig“ (E-01 SI), „Spalte
  verschmälern“ – beide offline mit gespeicherten KI-Antworten und unabhängigem Nachlesen bestätigt.
- War verlangtes SQL selbst ungültig und wurde repariert, ist der Ergebnisvergleich nur ein Hinweis.
- Next-Exam-Erkennung nicht gegen eine echte Next-Exam-Installation getestet.
- Nur Windows mit installiertem Microsoft Access.

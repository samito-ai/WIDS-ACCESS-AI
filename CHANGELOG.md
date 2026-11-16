# Changelog

Alle wichtigen Änderungen an WIDS ACCESS AI. Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/),
Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [1.0.0b2] – 2026-11-15 (vorbereitet, noch nicht veröffentlicht)

### Neu
- **Freigabesystem entfernt:** keine 24-Stunden-Freigaben, Lernprogramm-Zuordnungen oder gemerkten Datenbanken mehr –
  Alt+W startet die Bearbeitung direkt. Prüfungsschutz, Sicherung und die Bestätigung löschender Schritte bleiben;
  alte gespeicherte Freigaben werden ignoriert.
- **Excel-Import mit Datentypen und Feldbeschreibungen:** neue Tabelle aus .xls/.xlsx in einem Schritt; die Prüfung
  kontrolliert jetzt zusätzlich Feldnamen, Beschreibungen und ob alle Zeilen der Quelldatei importiert wurden.

### Schneller
- Dieselbe unveränderte Aufgabe (gleicher Text, gleiche Abbildungen, gleiche Datenbankstruktur) wird nicht erneut von
  der KI analysiert, z. B. bei erneutem Alt+W nach einem Halt (spart 15–60 s und Kosten); nach einem Fehlschlag wird
  neu geplant.
- Die KI-Bilderkennung desselben Fensterbildes wird wiederverwendet.
- Ein nicht erreichbares Netzlaufwerk (W:) blockiert höchstens 1,5 s statt bis zu einer Minute.

### Behoben
- **Alternative Zeilenfarben (te.comp E-01 SI, 0 von 1 Punkt):** WIDS färbte das Tabellen-Datenblatt statt des
  Detailbereichs des Berichts. Neu: Hintergrund- und alternative Hintergrundfarbe für Bereiche von Berichten und
  Formularen (`section_colors`); Bildschirminfo-Übungen lesen die te.comp-Info (Ansicht/Objektart); die Prüfung
  vergleicht die gespeicherte Farbe exakt (vorher genügte „irgendeine Farbe gesetzt“).
- Nach einem Access-Neustart durch te.comp scheiterte jedes weitere Alt+W an der toten Verbindung – sie wird jetzt
  erkannt, verworfen und neu aufgebaut.
- Import ordnete bei nicht passenden Spaltenüberschriften stillschweigend nach Position zu (Daten in falschen Feldern) –
  jetzt klare Fehlermeldung, Zielfelder müssen angegeben werden.
- Strengere Prüfungen: Breite, Höhe, Lage, Sichtbarkeit, Schrift und Farben geänderter Steuerelemente; Farben und genaue
  Anzahl bedingter Formatierungen beim Ersetzen; alle Einstellungen vorhandener Berichtsgruppen (auch „aus“).
- Unabhängige Prüfung erweitert: Abfrage-Ergebnis verlangt ↔ gespeichert, genau die geänderten Datensätze, erste
  importierte Zeile, Summen je Bereich, bedingte Formatierung exakt, Navigationsziel-Typ; Tabellenkopie mit
  Nachschlagefeldern; keine doppelten Steuerelemente; Neuprüfung nach Neuanlage; keine verwaisten Access-Instanzen.
- Nicht ausführbare Abfragen (fehlende Eingabetabelle) werden erkannt und nicht mehr als Datenquelle verwendet.
- Neue Formulare werden je Layoutspalte und auf die Spaltenbreiten geprüft.
- Lernplattform-Hinweise zählen nicht mehr als Anforderungen; verlustfreie Typänderungen halten im Hintergrund nicht an.
- Die Anforderungsprüfung sieht die Spaltenüberschriften der Quelldatei; Freitextspalten werden „Langer Text“.
- Ein während der Bearbeitung getrenntes Laufwerk bricht die Quelldateisuche nicht mehr ab.

## [1.0.0b1] – 2026-11-15 (erste öffentliche Beta)

### Neu
- **Universeller Access-Assistent:** arbeitet mit jeder Access-Datei, egal wie sie heißt (auch Netzwerkpfade); die
  Aufgabe bestimmt das Vorgehen, nicht der Dateiname.
- **24-Stunden-Freigabe je te.comp-Lernprogramm bzw. -Workshop**; nicht eindeutig erkannte Dateien werden einmal im
  Dashboard zugeordnet (dauerhaft gespeichert, nur für genau diesen Dateipfad).
- **Ruhemodus:** im Hintergrund keine Dialoge, keine Windows-Benachrichtigungen, kein unerwartetes Fenster;
  2 × 2-Pixel-Statuspunkt (gelb/grün/rot, Rot bleibt stehen).
- **Selbstkorrektur:** begrenzte Korrekturzyklen mit Fortschrittskontrolle; nach einem Zyklus ohne Fortschritt wird ein
  anderer Lösungsweg verlangt; nicht bestandene Schrittprüfungen werden mitkorrigiert; keine doppelten Datensätze.
- **Berichte:** mehrspaltiger Druck (Spaltenzahl, Abstand, Richtung), Berichtsbreite, Datenquelle und
  Steuerelementinhalt ändern, Etiketten, Seite einrichten.
- **Berichtslayout:** überdeckte Zeilen werden nach Änderungen automatisch neu geordnet (Reihenfolge bleibt,
  Seitenkopf bündig); neuer Prüfpunkt „Steuerelemente überlappen nicht“.
- **Prüfungsschutz:** echte Prüfungsumgebungen (Next Exam, Safe Exam Browser, Sperrbrowser, te.comp-Prüfungsmodus)
  sperren die Automatisierung – auch im Browser.

### Behoben
- Etiketten druckten erst nach unten statt wie der Etikettenassistent erst quer (gemessen über die Druckreihenfolge).
- Der dauerhaft laufende Windows-Dienst des Safe Exam Browser wurde fälschlich als laufende Prüfung erkannt.
- Das Wort „Schularbeit“ in Datei- oder Fenstertiteln sperrte gewöhnliche Übungsdatenbanken.
- Die Änderungsliste im Bericht enthielt keine Datenblatt-Änderungen (Formate, Spaltenbreiten).
- Überlappende Berichtsfelder nach einer Änderung.

### Bekannt / eingeschränkt
Siehe [Release-Notes](docs/RELEASE_NOTES_v1.0.0-beta.1.md) („Bekannte Einschränkungen“).

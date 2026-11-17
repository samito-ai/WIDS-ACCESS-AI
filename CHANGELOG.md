# Changelog

Alle wichtigen Änderungen an WIDS ACCESS AI. Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/),
Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [1.0.0b6] – 2026-11-17 (Patch)

### Neu: autonom in autorisierten Übungen
- **Eindeutige Löschungen/Änderungen ohne Rückfrage** – auch im Hintergrund –, wenn alles zutrifft: echte te.comp-Übung
  (Übungs-/Bildschirminfo-Name oder Workshop im te.comp-Arbeitsordner) oder WIDS-Arbeitskopie; Sicherung erstellt
  und als Datei geprüft; Bedingung vorhanden; höchstens so viele Treffer wie die Mengengrenze (Standard 25); lokale,
  nicht verknüpfte Tabelle. Damit läuft z. B. „Golfclub Neckartal löschen“ ohne „Wartet auf Bestätigung“ durch.
- Weiterhin mit Bestätigung: eigene bzw. unbekannte Dateien, „alle Datensätze“, Tabelle/Feld löschen,
  verlustbehaftete Typänderungen, Aktionsabfragen, verknüpfte Tabellen. Prüfungsschutz unverändert.

### Behoben
- **Erneuter Start fügt nichts doppelt ein:** Lief dieselbe Aufgabenstellung an derselben Datei schon teilweise, wird
  ein bereits ausgeführter Einfügeschritt übersprungen – aber nur, wenn ALLE verlangten Datensätze nachweislich
  vorhanden sind (sonst, z. B. nach Wiederherstellung einer Sicherung, wird normal eingefügt). Die Prüfung verlangt
  dann jeden Datensatz samt Mehrfachnennung.

## [1.0.0b5] – 2026-11-17 (Patch)

### Behoben (Live-Fund 17.11., Golfclubs-Übung)
- **Lesefragen scheiterten an einer Sperre, obwohl Schreiben funktionierte:** Datenfragen und Nachprüfungen öffneten
  eine zweite DAO-Verbindung; hat Access/te.comp die Datei exklusiv geöffnet, ist das unmöglich. Jetzt wird dann über
  die bestehende Verbindung gelesen (nur eine SELECT-Anweisung) und im Bericht ausgewiesen, dass die Nachprüfung keine
  unabhängige Zweitverbindung nutzen konnte. Keine Sperre wird aufgehoben, keine Lock-Datei gelöscht.
- **Keine sinnlosen Wiederholungen/KI-Kosten bei Zugriffsfehlern:** Sperr- und Verbindungsfehler lösen keine
  KI-Ersatzschritte mehr aus (vorher je Schritt KI-Anfrage + Ersatzschritt, insgesamt 80 s).
- **Abhängigkeiten nach Objektart:** Der Formularfilter „Golfclubs“ wartete auf eine Löschung in der gleichnamigen
  TABELLE. Jetzt zählen Objektart und Name, und angehalten werden nur Schritte, die auf der Löschung aufbauen
  (Datensätze hinzufügen, importieren, Objekt neu anlegen) – Filter, Formatierungen und andere Änderungen laufen.
- **Datensatz- und Filterwerte sind keine Objekte:** Hinweise „… existiert nicht in der Datenbank“ nur noch für
  Namen mit Objektwort (Tabelle, Abfrage, Formular, Bericht, Feld …), nicht für „Golfclub Bruchsal“ oder
  „Baden-Württemberg“.
- KI-Anweisung: „zuletzt erfasster Datensatz“ nur über einen fortlaufenden Autowert, sonst als offene Frage; Werte aus
  Abbildungen nie erfinden.

### Behoben (unabhängiges Codex-Review)
- Lesen über die bestehende Verbindung nur, wenn die Datei keine VBA-Module enthält (eine SELECT könnte sonst eigene
  VBA-Funktionen mit Schreibwirkung ausführen) – sonst ehrlicher Fehler statt Ersatzweg.
- Nicht ausgeführte Löschung hält ALLE späteren Datenschritte an derselben Tabelle an – auch bedingte Änderungen,
  SQL-Aktionsabfragen (Ziel aus dem SQL) und Tabellenkopien; Formulare, Berichte und Filter laufen weiter.
- Lernordner: Verknüpfungen/Junctions werden aufgelöst; das Ziel muss im freigegebenen Ordner und außerhalb gesperrter
  Orte liegen. Word-/Markdown-Unterlagen werden größenbegrenzt gelesen.
- Rechenfragen: kaufmännisches Runden exakt auf dem Bruch (auch negativ, keine Zwischenrundung); die Gegenrechnung
  rundet das exakte Argument; „0.125“ wird nicht als Tausenderzahl gelesen.
- Antworten: offene Fragen verhindern „beantwortet“ auch bei praktischen Aufgaben; Multiple Choice ohne ausgewertete
  Optionen gilt als nicht gesichert.
- Objekthinweis auch für „… unter dem Namen „X““.

## [1.0.0b4] – 2026-11-17

### Neu
- **Rechenfragen deterministisch** (`calculate_value`): WIDS rechnet den Ausdruck selbst exakt (Bruchrechnung,
  kaufmännisch gerundet) und prüft ihn mit einem zweiten Rechenweg; jede Zahl muss in einem wörtlichen Zitat der
  Aufgabe stehen, sonst wird nicht gerechnet. Nur Grundrechenarten, Potenzen mit kleinem Exponenten, `round` –
  kein anderer Code, Größenbegrenzung gegen Speicherüberlauf.
- **Antworten nur mit Beleg:** Theorie-/Multiple-Choice-Antworten mit gemeldeter Unsicherheit, ohne Begründung je
  Option, ohne richtige Option, ohne Antwort oder mit offenen Fragen heißen „nicht gesichert“ (Status „unklar“,
  roter Punkt) statt „beantwortet“.
- **Freigegebene Lernordner** in den Einstellungen: nur lesend, nur namentlich genannte Dateien; Word-Dokumente
  (.docx) als Lernunterlage; Laufwerke, System-, Programm- und Anmeldedaten-Ordner ausgeschlossen; nicht erreichbare
  Netzlaufwerke blockieren nicht.
- **Berichtslayout-Prüfung:** Spaltenlayout – Beschriftung links neben dem richtigen Feld, Felder in der verlangten
  Reihenfolge; tabellarisch – Überschrift über ihrem Feld, Spaltenreihenfolge; keine Überlappung im Detailbereich.
  Mehrspaltiger Druck wird weiterhin getrennt geprüft (Live-Fall „Students“).

### Behoben
- **Golfclubs-Fall:** Ein löschender Schritt sperrte im Hintergrund den ganzen Plan. Jetzt laufen unabhängige Schritte
  (mit Sicherung und Prüfung), die Löschung wartet auf Bestätigung im Dashboard, das Ergebnis heißt „teilweise“ mit
  konkretem Grund. Spätere Schritte am selben Objekt wie eine nicht ausgeführte Löschung werden ebenfalls nicht
  ausgeführt (z. B. „alles löschen, dann neu importieren“ – sonst doppelte Daten). Reine Löschpläne bleiben angehalten.
- Negativtest ergänzt: gespeicherte Abfrage ohne JOIN-Bedingung (Kreuzprodukt) wird abgelehnt.

## [1.0.0b3] – 2026-11-17

### Neu
- **System & KI testen** im Dashboard: kostenloser Teil (Access installiert, temporäre Testdatenbank, COM/DAO,
  Erkennung der geöffneten Übung, sicherer Testplan, unabhängige Prüfung, Sicherung und Bereinigung – echte
  Datenbanken werden nie angefasst) und – erst nach Bestätigung – KI-Teil mit höchstens 0,01 $ (Schlüssel, Anmeldung,
  Modell, eine gezählte Anfrage mit höchstens 64 Ausgabetokens, Verarbeitung). Ergebnis bereit/eingeschränkt/
  fehlgeschlagen mit konkreter Ursache.
- **Live-Fortschritt** aus echten Pipeline-Ereignissen (Phasen, Schritt k von n, erledigt/offen, bestätigte
  Teilergebnisse, Laufzeit, Selbstkorrektur-Zyklus mit Ursache); eine spätere fehlgeschlagene Neuprüfung nimmt eine
  Bestätigung zurück.
- **Selbstkorrektur ordnet die Ursache ein** (Aufgabenerkennung, KI-Plan, Access-Ausführung, Ergebnisprüfung) und gibt
  sie der KI mit; weiterhin höchstens 3 Zyklen, bestätigte Schritte bleiben.
- **API-Kosten im Dashboard:** letzte Aufgabe, Sitzung, heute, gesamt; Modell, Ein-/Ausgabetokens; Nutzung je Anfrage
  und Aufgabe gespeichert; Budget je Aufgabe (Standard 1 $) und Limit je Tag/Monat/gesamt einstellbar. Für Modelle ohne
  hinterlegten Preis wird bei aktivem Limit nichts gesendet (vorher: Limit wirkungslos). Preis für `gpt-5` ergänzt.
  Das Kostenlimit ist aus den Einstellungen ins Dashboard umgezogen.
- **Automatische Updates** über GitHub Releases dieses Repositorys: Hinweis mit Version und Notizen, Download nach
  Zustimmung, Prüfung gegen die veröffentlichte SHA-256, Selbsttest der neuen EXE, Austausch über eine vollständig
  kopierte Zwischendatei, Neustart erst nach Freigabe der Einzelinstanz, Rückfall auf die bisherige Version, wenn die
  neue nicht startet. Ohne Internet läuft alles normal weiter.
- **Diagnoseberichte nur für schwerwiegende Fehler** (Absturz, nicht behebbarer Access-Fehler, Sicherung/
  Wiederherstellung, KI-Verarbeitung, nach allen Korrekturen nicht lösbare Aufgabe) mit Kategorie, Komponente,
  Korrekturversuchen und Log-Auszug – ohne Schlüssel, Benutzernamen, Pfade (auch Netzlaufwerke) und Werte in
  Anführungszeichen. „Diagnose exportieren“ im Dashboard.

### Behoben
- KI-Anfragen nach einer Aufgabe (Chat, Systemtest) wurden noch der vorherigen Aufgabe zugerechnet.
- Lange Datenbankpfade (tiefe Ordner, OneDrive, Netzlaufwerke): Access kürzt sie im Fenstertitel, die geöffnete
  Datenbank wurde dann nicht erkannt – jetzt wird der Pfad bei genau dieser Access-Instanz erfragt.
- Die Vergleichsprüfung „Ergebnis wie verlangt“ entfiel, wenn Access die Datei exklusiv geöffnet hatte – jetzt läuft
  sie über die bestehende Verbindung (nur lesend).

## [1.0.0b2] – 2026-11-16

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

# Changelog

Alle wichtigen Änderungen an WIDS ACCESS AI. Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/),
Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

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

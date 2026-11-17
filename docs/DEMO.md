# WIDS ACCESS AI – Demo in 10 Minuten (eigene Testdaten)

Diese Demo braucht weder te.comp noch echte Übungsdateien: WIDS legt eine eigene Beispieldatenbank an
(Kunden, Artikel, Bestellungen). Jede Aufgabe kostet über den eigenen Anthropic-Schlüssel grob 0,05–0,30 $.

## Vorbereitung (einmal)

1. `WIDS-Access-AI.exe` starten → **Einstellungen** → API-Schlüssel einfügen → **Verbindung testen**.
2. **Dashboard** → **System & KI testen** (zeigt dem Publikum: Access, Testdatenbank, Prüfung, Sicherung – alles grün).
3. **Dashboard** → **Beispieldatenbank erstellen** (wird automatisch verbunden).

## Aufgaben (in „Aufgabe“ einfügen → „Analysieren und ausführen“)

Jede Aufgabe einzeln einfügen und starten. Danach im **Dashboard** den Live-Fortschritt und die Kosten zeigen und in
**Verlauf** den Bericht mit der unabhängigen Prüfung je Anforderung.

1. **Abfrage mit Kriterium und Sortierung**
   > Erstellen Sie eine Abfrage „qryKundenTirol“, die Nachname, Vorname und Ort aller Kunden aus Tirol anzeigt.
   > Sortieren Sie aufsteigend nach dem Nachnamen.

2. **Frage an die Datenbank** (Antwort kommt aus den echten Daten, nicht geraten)
   > Wie viele Bestellungen gibt es mit einer Menge von mindestens 3?

3. **Rechenfrage** (WIDS rechnet selbst und prüft doppelt)
   > Ein Artikel kostet 240 €. Es gibt 15 % Rabatt. Wie hoch ist der Rabatt in Euro?

4. **Datensatz ergänzen und eindeutig löschen** (läuft ohne Rückfrage, weil vorher gesichert wird)
   > Fügen Sie in der Tabelle „Kunden“ den Kunden Demo Schüler aus Imst hinzu. Löschen Sie danach alle Bestellungen
   > mit der Menge 1.

5. **Bericht im Spaltenlayout** (Layout wird nachgemessen)
   > Erstellen Sie den Bericht „rptKundenliste“ auf Basis der Tabelle „Kunden“ mit den Feldern Nachname, Vorname und
   > Ort im Spaltenlayout (einspaltig).

## Was man zeigen kann

- **Statuspunkt** oben links: gelb = arbeitet, grün = alles nachgewiesen, rot = nicht vollständig (Grund im Verlauf).
- **Sicherheit:** Vor jeder Änderung eine Sicherung (Verlauf → „Sicherung wiederherstellen“). Nicht eindeutige
  Löschungen (z. B. „alle Datensätze löschen“) werden nicht geraten, sondern als „blockiert“ markiert.
- **Ehrlichkeit:** Was WIDS nicht nachweisen kann, wird nie grün.
- **Prüfungsschutz:** In Prüfungsumgebungen (Next Exam, Safe Exam Browser) sperrt sich WIDS.

Zum Zurücksetzen einfach erneut **Beispieldatenbank erstellen** (neue Datei).

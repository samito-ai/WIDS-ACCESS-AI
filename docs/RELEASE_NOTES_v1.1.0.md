# WIDS ACCESS AI 1.1.0 (17.11.2026)

**Ein Alt+W für den ganzen te.comp-Workshop.** WIDS liest alle Teilaufgaben, plant sie gemeinsam in sinnvoller
Reihenfolge, sichert, führt aus, prüft jede Teilaufgabe einzeln und meldet „vollständig“ nur, wenn alle nachgewiesen
sind. Kostenlos, Open Source (MIT). **Nur für Übungen, nicht für benotete Prüfungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 306 323 Bytes | `3122E42B7B8EE2E3FB21E780694BC5FBC90369E83431327A65646CFD2A6E7C83` |

- Von 1.0.0 oder Beta 3–7: im Dashboard „Update installieren“. Neu: EXE herunterladen und starten.
- Rückfallversion: [v1.0.0](https://github.com/samito-ai/WIDS-ACCESS-AI/releases/tag/v1.0.0) bleibt verfügbar.

## Neu

- **Ganzer Workshop mit einem Alt+W:** WIDS liest alle Teilaufgaben aus der te.comp-Aufgabenleiste, auch die gerade
  nicht sichtbaren. Dazu blättert es mit te.comps eigenen Pfeilen (ohne Mausbewegung, ohne Fokuswechsel, nie
  „Prüfen“, „Nächste Aufgabe“ oder „Abschließen“) und stellt die Leiste danach wieder genau so ein. Funktioniert bei
  jeder Bildschirmskalierung. In einer Prüfungsumgebung wird nichts angeklickt.
- **Ein Plan, Status je Teilaufgabe:** Jede Anforderung trägt die Nummer ihrer Teilaufgabe. Das Ergebnis lautet
  „Workshop vollständig abgeschlossen – n von n“ oder „Workshop teilweise abgeschlossen – k von n …“ mit den offenen
  Nummern.
- **Nie falsch „erledigt“:** Eine Teilaufgabe gilt nur als nachgewiesen, wenn ihre Schritte gelaufen sind und ihre
  unabhängige Prüfung bestanden haben. Ein blockierter, gescheiterter oder nicht bestätigter Schritt zählt nie,
  egal was die KI-Anforderungsprüfung meint. Ein gescheiterter Schritt hält die Prüfung der übrigen Teilaufgaben nicht
  mehr auf.
- **Befüllen wird nachgeprüft:** Setzt eine Aktualisierung einen berechneten Wert (z. B. DLookUp), muss das Feld danach
  in den betroffenen Datensätzen Werte haben. „33 Datensätze geändert“ bei überall leerem Feld ist nicht bestanden.
- **Bereits Erledigtes:** Vorhandene Tabellen, Abfragen, Formulare und Berichte werden nicht neu angelegt, sondern mit
  derselben Prüfung wie neue kontrolliert; nur wenn sie der Vorgabe entsprechen, gilt der Schritt als erledigt. Nie
  überschrieben.
- **Neu importierte Daten:** Für Tabellen, die derselbe Lauf gerade angelegt oder importiert hat, gilt die Grenze von
  25 Datensätzen je Schritt nicht (sie liegen vollständig in der Sicherung davor). Vorhandene Daten bleiben geschützt;
  Umbenennen, Weitergabe an andere Tabellen, Abfragen über mehrere Tabellen und fremde Dateien sind ausgeschlossen.
- **Dashboard:** erkannte und nachgewiesene Teilaufgaben, aktueller Schritt, KI-Kosten der Aufgabe mit Budget und
  höchstens noch möglichem Betrag.

## Geprüft

- **694 automatische Tests bestanden, 0 fehlgeschlagen** (24 übersprungen: 21 kostenpflichtige Live-KI-Tests,
  2 Tests mit laufendem te.comp, 1 Test mit Adminrechten), darunter 20 Unit- und 15 Ende-zu-Ende-Tests mit echtem
  Access nur für Workshops: Workshop mit einer und mit mehreren Teilaufgaben, Abhängigkeiten Tabelle → Daten →
  Abfrage, blockierte und gescheiterte Teilaufgaben, Wiederaufnahme ohne doppelte Daten, unvollständige Erfassung,
  leerer Plan, falsche vorhandene Objekte, Befüllen ohne Ergebnis, Mengengrenze für neue und alte Tabellen.
  Ruff und Mypy ohne Befund; kein Test entfernt oder abgeschaltet.
- **Echte te.comp-Leiste:** alle 12 Teilaufgaben des Workshops in rund 10 Sekunden gelesen und die Leiste exakt
  zurückgestellt, bei logischer und physischer Bildschirmskalierung.
- **EXE:** Selbsttest (auch ohne Python), Hintergrundprüfungen OK (Infobereich, keine zweite Instanz, Kürzel,
  sauberes Beenden, Neustart); Alt+W in der EXE in drei echten Workshop-Läufen.
- **Unabhängiges Codex-Review** in mehreren Runden; alle bestätigten Befunde behoben und mit Tests abgesichert.

## Echter Test mit dem Workshop „CD-Sammlung“ (12 Teilaufgaben)

Drei kostenpflichtige Läufe der EXE mit echter KI (zusammen 1,30 $ von 1,50 $ freigegebenem Budget):

1. **Lauf 1:** Die Teilaufgaben-Leiste wurde bei 150 % Bildschirmskalierung nicht erkannt. WIDS bearbeitete nur die
   sichtbare Teilaufgabe und meldete ehrlich „Workshop nicht vollständig erfassbar“. Behoben: Maße relativ statt in
   Pixeln, geprüft in beiden Skalierungsmodi.
2. **Lauf 2:** Alle 12 Teilaufgaben erfasst, 10 von 11 Schritten ausgeführt. Ein Schritt scheiterte an der Grenze von
   25 Datensätzen für frisch importierte Daten; danach wurde gar nichts mehr geprüft („0 von 12“). Beides behoben.
3. **Lauf 3** (Workshop über „Neu laden“ zurückgesetzt, **ein** Alt+W): alle 12 Teilaufgaben erfasst, 19 Schritte
   samt zwei Selbstkorrektur-Runden, 0,88 $. Eigene Gegenprüfung in der Datenbank: Teilaufgaben 1, 2, 3, 4, 5, 7, 10, 11
   und 12 sind tatsächlich erledigt. Offen sind 6 (Liedtext aus der Textdatei), 8 (CD-Nummer zuordnen; die Zuordnung
   über den Titel fand keine Treffer) und 9 (Zeitformat ohne Stunden). WIDS hatte dabei Teilaufgabe 8 fälschlich als
   nachgewiesen gezählt und die erledigte Teilaufgabe 3 als „nicht nachweisbar“ gemeldet. Beides ist in 1.1.0 behoben
   (Prüfung „Feld enthält danach Werte“, Anlagen als Beleg) und mit Tests abgesichert. Ein weiterer bezahlter Lauf nach
   diesen Fixes wurde nicht gemacht.

## Bekannte Grenzen

- Nicht jede Teilaufgabe wird automatisch gelöst. Im Test offen: Text aus einer Textdatei in ein Feld übernehmen,
  Zeitformat eines Textfelds, Zuordnung über nicht exakt gleiche Titel. Solche Teilaufgaben bleiben offen und werden so
  gemeldet.
- Ein großer Workshop kostet mehr als eine Einzelübung (im Test 0,88 $). Reicht das Aufgabenbudget nicht (Standard
  1 $), bricht WIDS mit Grund ab; das Budget lässt sich im Dashboard erhöhen.
- Wird ein Workshop nach einem früheren Lauf erneut gestartet, gelten die damals importierten Tabellen als vorhandene
  Daten: Massenänderungen über 25 Datensätze daran bleiben blockiert.
- Die Teilaufgaben werden per Texterkennung gelesen. Ist eine Nummer nicht lesbar, gilt der Workshop als nicht
  vollständig erfasst und nie als abgeschlossen.
- Die EXE ist nicht signiert; PDF-Unterlagen werden nicht gelesen; Antworten werden nicht in te.comp eingetragen.

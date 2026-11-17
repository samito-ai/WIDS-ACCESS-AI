# WIDS ACCESS AI v1.0.0 Beta 5 (Patch, 17.11.2026)

Behebt den Live-Fund aus der Golfclubs-Übung vom 17.11.: Sperre bei Lesezugriffen, unnötige Wiederholungen,
übersprungener Formularfilter, Datensatzwerte als „fehlende Objekte“. Kostenlos, MIT. **Nur für Übungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 273 940 Bytes | `57E62D2927D759264CF5A26F4EFB051B60C5B0FC25AAB4ABA463966D62AF9DF9` |

Von Beta 3/4: im Dashboard „Update installieren“. Von Beta 1/2: einmal von Hand ersetzen.

## Behoben

- **Lesefragen scheiterten an „… in einen Status versetzt, in dem sie nicht geöffnet oder gesperrt werden kann“,
  während Schreiben funktionierte.** Ursache: Datenfragen und Nachprüfungen öffneten eine zweite DAO-Verbindung –
  hält Access/te.comp die Datei exklusiv, ist das unmöglich. Jetzt wird dann über die bestehende Verbindung gelesen
  (nur eine SELECT-Anweisung); der Bericht weist aus, dass keine unabhängige Zweitverbindung möglich war. Keine
  fremde Sperre wird aufgehoben, keine Lock-Datei gelöscht, kein Access-Prozess beendet.
- **Keine KI-Ersatzschritte bei Zugriffsfehlern** (Sperre, Verbindung, Zeitüberschreitung) – ein anderer Lösungsweg
  hilft dort nicht; vorher kostete das Zeit (80 s) und KI-Anfragen.
- **Abhängigkeiten nach Objektart:** Ein Formularfilter wartete auf eine Löschung in der gleichnamigen Tabelle. Jetzt
  warten nur Schritte, die tatsächlich auf der Löschung aufbauen (Datensätze hinzufügen, importieren, neu anlegen).
- **Datensatz- und Filterwerte sind keine Objekte:** Hinweise „existiert nicht in der Datenbank“ nur für Namen mit
  Objektwort (Tabelle, Abfrage, Formular, Bericht, Feld …) und deren Aufzählungen.
- KI-Anweisung: „zuletzt erfasster Datensatz“ nur über fortlaufenden Autowert; Werte aus Abbildungen nie erfinden.

## Geprüft

- 638 automatische Tests bestanden, 0 fehlgeschlagen (22 übersprungen: kostenpflichtige Live-KI-Tests). Neu u. a.:
  echte Reproduktion mit einer zweiten Access-Instanz, die die Datei exklusiv öffnet (zweite Verbindung scheitert,
  Lesen und Schreiben über die bestehende Verbindung funktionieren, Einschränkung wird ausgewiesen); Formularfilter
  trotz ausstehender Löschung; Objektname vs. Datensatzwert; keine KI-Reparatur bei Sperre.
- Datenkontrolle des Live-Laufs (Sicherungskopie): „ID“ ist ein fortlaufender Autowert, der höchste Wert gehört zu
  „Golf-Club Bad Bevensen e.V.“ – der richtige Datensatz wurde bearbeitet. Ob die eingetragenen Werte der Abbildung
  entsprechen, kann WIDS ohne Abbildung nicht unabhängig belegen.
- EXE: Selbsttest (auch ohne Python). {{BG}}
- Keine kostenpflichtigen KI-Tests: 0 $. {{CODEX}}

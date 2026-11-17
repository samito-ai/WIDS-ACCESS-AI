# WIDS ACCESS AI v1.0.0 Beta 6 (Patch, 17.11.2026)

Alt+W → bearbeiten → selbst korrigieren → unabhängig prüfen → fertig – ohne unnötige Rückfragen in echten Übungen.
Kostenlos, MIT. **Nur für Übungen.**

## Download

| Datei | Größe | SHA-256 |
|---|---|---|
| `WIDS-Access-AI.exe` | 65 281 266 Bytes | `E9F29ACFE3BC69AA2958E67143B3685187EBB97319E7DBF6FE8C70E512F5469F` |

Von Beta 3–5: im Dashboard „Update installieren“. Von Beta 1/2: einmal von Hand ersetzen.

## Neu

- **Autonom in autorisierten Übungen:** Eindeutige Lösch- und Änderungsschritte laufen ohne Rückfrage (auch im
  Hintergrund), wenn alles zutrifft: echte te.comp-Übung (Übungs-/Bildschirminfo-Name oder Workshop im
  te.comp-Arbeitsordner) oder WIDS-Arbeitskopie, Sicherung erstellt und geprüft, Bedingung vorhanden, höchstens 25
  Treffer, lokale (nicht verknüpfte) Tabelle. „Golfclub Neckartal löschen“ bleibt nicht mehr bei „Wartet auf
  Bestätigung“ stehen.
- Weiterhin mit Bestätigung: eigene/unbekannte Dateien, „alle Datensätze“, Tabelle/Feld löschen, verlustbehaftete
  Typänderungen, Aktionsabfragen, verknüpfte Tabellen. Prüfungsschutz unverändert.

## Behoben

- **Erneuter Start fügt nichts doppelt ein:** derselbe Einfügeschritt derselben Aufgabe an derselben Datei wird nicht
  wiederholt – nur wenn ALLE verlangten Datensätze nachweislich vorhanden sind; sonst wird normal eingefügt.

## Geprüft

- 647 automatische Tests bestanden, 0 fehlgeschlagen (24 übersprungen: 21 kostenpflichtige Live-KI-Tests, 2 te.comp-
  Live-Tests ohne laufendes te.comp, 1 Verknüpfungstest ohne Adminrechte). Neu: autonomer Golfclubs-artiger Ablauf,
  Neustart ohne Doppelung, Neustart nach Zurücksetzen, Grenzen (alle Datensätze, Strukturlöschung, Aktionsabfrage,
  verknüpfte Tabelle, eigene Datei im te.comp-Ordner).
- Unabhängiges Codex-Review des kritischen Ablaufs: 7 Funde, alle behoben und getestet.
- EXE: Selbsttest (auch ohne Python), 15/15 Hintergrundprüfungen. Keine kostenpflichtigen KI-Tests: 0 $.
- Noch nicht live geprüft: ein echter Alt+W-Lauf in der Golfclubs-Übung mit dieser Version.

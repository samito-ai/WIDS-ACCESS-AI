# Mitwirken an WIDS ACCESS AI

Danke für dein Interesse! WIDS ACCESS AI ist ein Hilfsmittel für **Microsoft-Access-Übungen**. Beiträge, die
Prüfungssperren umgehen, benotete Tests automatisieren oder Zugangsdaten sammeln, werden nicht angenommen.

## Entwicklungsumgebung
1. Windows 10/11, Python 3.11, Microsoft Access (Office 2016 oder neuer, 64 Bit empfohlen).
2. `scripts\setup_dev.ps1` ausführen (legt eine virtuelle Umgebung an und installiert `.[dev]`).
3. Start aus dem Quellcode: `scripts\run.ps1`.

## Prüfen vor einem Pull Request
```powershell
python -m ruff check app tests scripts build.py
python -m mypy app
python -m pytest -m "not live_ai and not live_tecomp"
```
- Tests mit echtem Access (`-m access`) laufen nur lokal; GitHub Actions führt die Offline-Tests ohne Access aus.
- **Keine kostenpflichtigen KI-Anfragen in Tests:** Der HTTP-Transport ist gesperrt, solange `WIDS_LIVE_AI` nicht
  gesetzt ist. Live-Tests (`-m live_ai`) nur bewusst und mit eigenem Schlüssel.
- Tests nie deaktivieren oder abschwächen, um ein grünes Ergebnis zu erzwingen; neue Funktionen brauchen einen Test,
  der ohne die Funktion fehlschlägt.

## Grundregeln im Code
- Jede Änderung an einer Datenbank: vorher Sicherung, danach unabhängige Prüfung direkt aus Access; „erfolgreich“ nur,
  wenn jede Anforderung bestätigt ist.
- Die KI liefert nur strukturierte Pläne – nie frei ausführbaren Code (kein VBA, keine Shell).
- API-Schlüssel nur über die Windows-Anmeldeinformationsverwaltung; nie in Dateien, Logs oder Tests.
- Keine persönlichen Daten, Schuldateien oder te.comp-Inhalte ins Repository (siehe `.gitignore`).

## Fehler melden
Bitte mit Windows-/Office-Version, kurzer Beschreibung und – falls möglich – dem Diagnosebericht aus dem Verlauf
(vorher persönliche Daten entfernen). Keine Datenbanken mit echten Personendaten anhängen.

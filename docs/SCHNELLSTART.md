# WIDS ACCESS AI – Schnellstart in 5 Minuten

WIDS ist **kostenlos** und Open Source (MIT-Lizenz). Es gibt **kein Bezahlsystem, keinen Lizenzschlüssel, kein
Benutzerkonto, keine Nutzungslimits und keine versteckten Gebühren**. Kosten entstehen nur bei Anthropic für deinen
**eigenen** KI-Schlüssel (meist wenige Cent pro Übung).

**Nur für Übungen** – in Prüfungsumgebungen (z. B. Next Exam, Safe Exam Browser) sperrt sich WIDS selbst.

## 1. Voraussetzungen
- Windows 10 oder 11
- Microsoft Access (Desktop), einmal von Hand gestartet
- ein eigener Anthropic-API-Schlüssel (Schritt 3)

## 2. Herunterladen und starten
1. Auf der GitHub-Seite des Projekts unter **Releases** die Datei `WIDS-Access-AI.exe` herunterladen.
2. In einen eigenen Ordner legen (z. B. `Dokumente\WIDS`) und doppelklicken – keine Installation nötig.
3. Warnt Windows („Der Computer wurde durch Windows geschützt“): **Weitere Informationen → Trotzdem ausführen**
   (die EXE ist nicht signiert). Wer sichergehen will, vergleicht die SHA-256-Prüfsumme aus den Release-Notes:
   `Get-FileHash .\WIDS-Access-AI.exe -Algorithm SHA256` in PowerShell.

## 3. Eigenen KI-Schlüssel einrichten
1. Auf <https://console.anthropic.com> ein Konto anlegen, etwas Guthaben aufladen (z. B. 5 $) und unter
   **API Keys** einen Schlüssel erzeugen. Tipp: dort ein **Ausgabenlimit** setzen.
2. In WIDS **Einstellungen** öffnen, den Schlüssel einfügen und **Verbindung testen** klicken. Wer möchte, klickt
   danach im Dashboard **System & KI testen** (der KI-Teil kostet höchstens 0,01 $ und startet erst nach Rückfrage).
3. Der Schlüssel wird verschlüsselt in Windows gespeichert und nie weitergegeben. **Schlüssel nie teilen** –
   jede Person braucht ihren eigenen.

## 4. Benutzen
1. Übung in te.comp bzw. Access öffnen (der Dateiname ist egal).
2. **Alt+W** drücken. WIDS arbeitet im Hintergrund, ohne Fenster und ohne Pop-ups.
3. Der kleine Punkt ganz oben links zeigt den Stand: **gelb** = arbeitet, **grün** = fertig und geprüft,
   **rot** = nicht abgeschlossen oder angehalten.
4. Details, Bericht und Sicherung: Rechtsklick auf das WIDS-Symbol unten rechts → **Dashboard** → **Verlauf**.
5. Danach in te.comp selbst **Prüfen** klicken.

Vor jeder Änderung legt WIDS eine Sicherung an (Verlauf → „Sicherung wiederherstellen“). Löschende Schritte führt
WIDS im Hintergrund nie aus – dafür das Dashboard öffnen und bestätigen.

## 5. Wenn etwas nicht klappt
- **Roter Punkt:** Dashboard → Verlauf öffnen; dort steht der Grund.
- **„kein Guthaben“:** in der Anthropic-Konsole Guthaben aufladen.
- **Alt+W reagiert nicht:** prüfen, ob das WIDS-Symbol unten rechts da ist; sonst WIDS neu starten.
- **Neue Version:** erscheint im Dashboard („Update installieren“) – wird geprüft und nach Zustimmung installiert.
- Fehler bitte mit dem Bericht aus dem Verlauf melden (ohne Schlüssel!). Bei schweren Fehlern gibt es im Dashboard
  **Diagnose exportieren** – der Bericht enthält keine Schlüssel, Namen, Pfade oder Datenbankinhalte.

## Unterstützen
WIDS bleibt kostenlos. Wer möchte, kann freiwillig etwas beitragen – es ist nie Voraussetzung für die Nutzung.

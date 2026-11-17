"""Dashboard-Bereiche: Live-Fortschritt, API-Kosten sowie System, Updates & Diagnose.

Alles Langsame (Access, Netzwerk, KI) läuft über ``MainWindow.run_async`` – die Oberfläche bleibt bedienbar.
Kosten erscheinen ausschließlich hier im Dashboard (nicht im Statuspunkt, nicht in Meldungen).
"""
from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app import APP_NAME
from app.agent.progress import ProgressTracker
from app.core import diagnostics, system_check, updater
from app.storage.database import UsageSummary, now_iso

_PERIODS = {"day": "Tag", "month": "Monat", "total": "gesamt"}
_ICONS = {"ok": "✓", "warn": "⚠", "fail": "✗", "skipped": "–"}


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName="card")
    layout = QVBoxLayout(frame)
    layout.addWidget(QLabel(title, objectName="muted"))
    return frame, layout


def _tokens(value: int) -> str:
    return f"{value:,}".replace(",", ".")


# --------------------------------------------------------------------------- Live-Fortschritt
class ProgressView:
    """Zeigt den ``ProgressTracker`` – aktualisiert durch echte Pipeline-Ereignisse, Laufzeit im Sekundentakt."""

    def __init__(self, parent: Any) -> None:
        self.tracker = ProgressTracker()
        self.frame, box = card("Live-Fortschritt")
        self.steps = QLabel("Noch keine Aufgabe in dieser Sitzung.", objectName="value", wordWrap=True)
        self.meta = QLabel("", objectName="muted", wordWrap=True)
        box.addWidget(self.steps)
        box.addWidget(self.meta)
        box.addStretch()
        self._timer = QTimer(parent, interval=1000)
        self._timer.timeout.connect(self.render)
        #: Kostenzeile der laufenden Aufgabe (vom Hauptfenster gesetzt; leer = keine Angabe)
        self.cost_line: Callable[[], str] = lambda: ""

    def finish_and_render(self, text: str, state: str) -> None:
        self.tracker.finish(text, state)
        self.render()

    def render(self) -> None:
        t = self.tracker
        if t.started is None:
            return
        self.steps.setText("\n".join(t.lines()))
        meta = [f"Laufzeit {t.elapsed():.0f} s"]
        if t.total_steps:
            meta.append(f"erledigt {t.done_steps} · offen {max(t.total_steps - t.done_steps, 0)}")
        if cost := self.cost_line():
            meta.append(cost)
        if t.confirmed:
            meta.append("bestätigt: " + ", ".join(t.confirmed[-4:]) + (" …" if len(t.confirmed) > 4 else ""))
        if t.result:
            meta.append(t.result.splitlines()[0][:160])
        self.meta.setText(" · ".join(meta))
        if t.running and not self._timer.isActive():  # Laufzeit im Sekundentakt nur während einer Aufgabe
            self._timer.start()
        elif not t.running:
            self._timer.stop()


# --------------------------------------------------------------------------- API-Kosten
class CostView:
    """Kosten der letzten Aufgabe, der Sitzung, von heute und gesamt (lokaler Verlauf) – plus einstellbare Limits."""

    def __init__(self, win: Any) -> None:
        self.win = win
        self.session_start = now_iso()
        ai = win.ctx.settings.ai
        self.frame, box = card("API-Kosten (lokal gezählt)")
        self.text = QLabel("–", objectName="value", wordWrap=True)
        box.addWidget(self.text)
        row = QHBoxLayout()
        self.task_limit = QDoubleSpinBox(maximum=100, decimals=2, singleStep=0.1, value=ai.task_cost_limit_usd,
                                         suffix=" $")
        self.task_limit.setToolTip("Höchstkosten je Aufgabe (0 = aus). Vor jeder KI-Anfrage geprüft.")
        self.limit = QDoubleSpinBox(maximum=10000, decimals=2, value=ai.cost_limit_usd, suffix=" $")
        self.limit.setToolTip("Höchstkosten je Zeitraum (0 = aus)")
        self.period = QComboBox()
        for key, label in _PERIODS.items():
            self.period.addItem(label, key)
        self.period.setCurrentIndex(list(_PERIODS).index(ai.cost_limit_period))
        save = QPushButton("Limits speichern")
        save.clicked.connect(self.save)
        for widget in (QLabel("je Aufgabe"), self.task_limit, QLabel("je"), self.period, self.limit, save):
            row.addWidget(widget)
        row.addStretch()
        box.addLayout(row)
        box.addWidget(QLabel("Tokens: tatsächliche Zahlen laut API-Antwort. Kosten: berechnet aus diesen Tokens und den "
                             "hinterlegten Modellpreisen (Schätzung – maßgeblich ist die Abrechnung des Anbieters). Das "
                             "Guthaben beim Anbieter kann WIDS nicht abrufen. Die Limits sind eine lokale "
                             "Vorsichtsmaßnahme, keine Abrechnungsgarantie.", objectName="muted", wordWrap=True))

    @staticmethod
    def _line(label: str, usage: UsageSummary) -> str:
        if not usage.requests:
            return f"{label}: –"
        unpriced = f" (+{usage.unpriced_requests} ohne Preis)" if usage.unpriced_requests else ""
        return (f"{label}: {usage.cost_usd:.4f} $ geschätzt{unpriced} · {usage.requests} Anfragen · "
                f"{_tokens(usage.input_tokens)} ein / {_tokens(usage.output_tokens)} aus")

    def refresh(self) -> None:
        storage = self.win.ctx.storage
        last = storage.last_task_with_usage()
        total = storage.usage_summary("total")
        lines = [
            self._line(f"Letzte Aufgabe (#{last})" if last else "Letzte Aufgabe",
                       storage.usage_summary(task_id=last) if last else UsageSummary()),
            self._line("Diese Sitzung", storage.usage_summary(since=self.session_start)),
            self._line("Heute", storage.usage_summary("day")),
            self._line("Gesamt (lokaler Verlauf)", total),
            f"Modell: {self.win.ctx.settings.active_model()}"
            + (f" · bisher verwendet: {total.models}" if total.models else ""),
        ]
        self.text.setText("\n".join(lines))

    def save(self) -> None:
        settings = self.win.ctx.settings
        settings.ai.task_cost_limit_usd = self.task_limit.value()
        settings.ai.cost_limit_usd = self.limit.value()
        settings.ai.cost_limit_period = self.period.currentData()
        self.win.ctx.storage.save_settings(settings)
        self.win.task_status.setText("Kostenlimits gespeichert.")


# --------------------------------------------------------------------------- System, Updates, Diagnose
class MaintenanceView:
    def __init__(self, win: Any) -> None:
        self.win = win
        self.release: updater.Release | None = None
        self.frame, box = card("System, Updates & Diagnose")
        self.system = QLabel("Systemtest: noch nicht ausgeführt.", objectName="value", wordWrap=True)
        self.update_info = QLabel("Updates: noch nicht geprüft.", objectName="muted", wordWrap=True)
        self.diagnose = QLabel("", objectName="muted", wordWrap=True)
        for label in (self.system, self.update_info, self.diagnose):
            box.addWidget(label)
        row = QHBoxLayout()
        self.btn_test = QPushButton("System && KI testen")  # && = sichtbares &, kein Tastenkürzel
        self.btn_test.clicked.connect(self.run_system_test)
        self.btn_check = QPushButton("Nach Updates suchen")
        self.btn_check.clicked.connect(lambda: self.check_updates(quiet=False))
        self.btn_install = QPushButton("Update installieren", objectName="primary")
        self.btn_install.clicked.connect(self.install_update)
        self.btn_install.setVisible(False)
        self.btn_export = QPushButton("Diagnose exportieren")
        self.btn_export.clicked.connect(self.export_diagnose)
        for btn in (self.btn_test, self.btn_check, self.btn_install, self.btn_export):
            row.addWidget(btn)
        row.addStretch()
        box.addLayout(row)
        self.refresh_diagnose()

    # ---------------------------------------------------------------- Systemtest
    def run_system_test(self) -> None:
        win = self.win
        if win._busy:
            return
        self.btn_test.setEnabled(False)
        win.set_busy(True, "Systemtest (kostenloser Teil) läuft – auf einer temporären Testdatenbank …")
        self.system.setText("Systemtest läuft (kostenloser lokaler Teil) …")
        win.run_async(system_check.run_local, self._local_done, self._test_failed)

    def _show(self, results: list[system_check.CheckResult], *, paid_offered: bool) -> None:
        verdict, text = system_check.summary(results)
        lines = [f"{_ICONS[r.status]} {r.name}{' (kostenpflichtig)' if r.paid else ''}"
                 + (f" – {r.detail}" if r.detail else "") for r in results]
        head = {"bereit": "Bestanden (bereit)", "eingeschränkt": "Teilweise bestanden (eingeschränkt)",
                "fehlgeschlagen": "Fehlgeschlagen"}[verdict]
        if not paid_offered:
            head += " – KI-Teil nicht ausgeführt"
        self.system.setText(f"Systemtest: {head}\n" + "\n".join(lines) + ("" if verdict == "bereit" else f"\n{text}"))

    def _local_done(self, local: list[system_check.CheckResult]) -> None:
        win = self.win
        self._show(local, paid_offered=False)
        win.set_busy(False, "Kostenloser Systemtest abgeschlossen.")
        answer = QMessageBox.question(
            win._dialog_parent(), APP_NAME,
            "Der kostenlose Teil ist fertig.\n\nJetzt den KI-Teil starten? Er prüft API-Schlüssel, Anmeldung, Modell und "
            f"eine echte kleine KI-Anfrage.\n\nKosten: höchstens {system_check.PAID_COST_CAP_USD:.2f} $ "
            "(eine Verbindungsprobe und eine Antwort mit höchstens 64 Tokens).")
        if answer != QMessageBox.StandardButton.Yes:
            self.btn_test.setEnabled(True)
            return
        win.set_busy(True, "Systemtest (KI-Teil) läuft …")
        win.run_async(lambda: local + system_check.run_paid(win.ctx.ai), self._paid_done, self._test_failed)

    def _paid_done(self, results: list[system_check.CheckResult]) -> None:
        self._show(results, paid_offered=True)
        verdict = system_check.summary(results)[0]
        self.win.set_busy(False, f"Systemtest: {verdict}")  # Ergebnis steht im Dashboard, nicht im Statuspunkt
        self.btn_test.setEnabled(True)
        self.win.refresh_dashboard()

    def _test_failed(self, exc: Exception) -> None:
        self.win.set_busy(False)
        self.btn_test.setEnabled(True)
        self.system.setText(f"Systemtest: Fehlgeschlagen – {type(exc).__name__}: {exc}")
        self.win.show_error(exc)

    # ---------------------------------------------------------------- Updates
    def check_updates(self, *, quiet: bool) -> None:
        self.btn_check.setEnabled(False)
        if not quiet:
            self.update_info.setText("Suche nach Updates …")
        self.win.run_async(updater.check, self._checked, lambda exc: self._checked(None))

    def _checked(self, release: updater.Release | None) -> None:
        self.btn_check.setEnabled(True)
        self.release = release
        self.btn_install.setVisible(release is not None)
        if release is None:
            self.update_info.setText("Updates: keine neuere Version gefunden (oder keine Internetverbindung).")
            return
        notes = "\n".join(line for line in release.notes.splitlines() if line.strip())[:600]
        self.update_info.setText(f"Neue Version verfügbar: {release.version}\n{notes}")

    def install_update(self) -> None:
        release, win = self.release, self.win
        if release is None or win._busy:
            return
        if not getattr(sys, "frozen", False):
            QMessageBox.information(win._dialog_parent(), APP_NAME,
                                    "Updates werden nur in der installierten EXE eingespielt. Im Quellcode bitte "
                                    "„git pull“ verwenden.")
            return
        answer = QMessageBox.question(
            win._dialog_parent(), APP_NAME,
            f"Version {release.version} jetzt herunterladen und installieren?\n\nQuelle: offizielles Release auf "
            f"github.com/{updater.REPO}. Die Datei wird per SHA-256 geprüft und testweise gestartet; erst dann wird "
            "die EXE ersetzt (die bisherige bleibt bis zum erfolgreichen Neustart erhalten). Einstellungen, "
            "API-Schlüssel und Verlauf bleiben unverändert.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.btn_install.setEnabled(False)
        win.set_busy(True, f"Update {release.version} wird geladen und geprüft …")

        def done(_: Any) -> None:
            win.set_busy(False, "Update installiert.")
            self.update_info.setText(f"Version {release.version} ist installiert und wird beim Neustart verwendet.")
            if QMessageBox.question(win._dialog_parent(), APP_NAME,
                                    "Update installiert. WIDS jetzt neu starten?") == QMessageBox.StandardButton.Yes:
                win.restart_after_quit = True
                win.quit_app()

        def failed(exc: Exception) -> None:
            win.set_busy(False)
            self.btn_install.setEnabled(True)
            self.update_info.setText(f"Update nicht installiert: {exc}")
            QMessageBox.warning(win._dialog_parent(), APP_NAME, f"Update nicht installiert:\n{exc}")

        win.run_async(lambda: updater.install(updater.download(release)), done, failed)

    # ---------------------------------------------------------------- Diagnose
    def refresh_diagnose(self) -> None:
        report = diagnostics.latest_report()
        self.btn_export.setEnabled(report is not None)
        self.diagnose.setText(f"Letzter schwerwiegender Fehler: {report.stem.removeprefix('diagnose_')}"
                              if report else "Keine schwerwiegenden Fehler aufgezeichnet.")

    def export_diagnose(self) -> None:
        report = diagnostics.latest_report()
        if report is None:
            return
        target, _ = QFileDialog.getSaveFileName(self.win._dialog_parent(), "Diagnose exportieren",
                                                str(Path.home() / "Desktop" / report.name), "Markdown (*.md)")
        if target:
            shutil.copy2(report, target)
            self.win.task_status.setText(f"Diagnose gespeichert: {target}")

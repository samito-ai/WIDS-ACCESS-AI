"""Dunkles und helles Design (Qt-Stylesheet) mit blau-violetten Akzenten."""
from __future__ import annotations

_PALETTES = {
    "dark": {"bg": "#12131c", "panel": "#1b1d2b", "card": "#22253a", "text": "#e6e8f2", "muted": "#8d93ad",
             "border": "#30344e", "accent": "#6d7dff", "accent2": "#9a6bff", "ok": "#3ecf8e", "bad": "#ff6b81"},
    "light": {"bg": "#f4f5fb", "panel": "#ffffff", "card": "#ffffff", "text": "#1c1e2d", "muted": "#66708a",
              "border": "#d9dcea", "accent": "#4f5fe8", "accent2": "#7d4fe0", "ok": "#1f9d68", "bad": "#d63a55"},
}

_QSS = """
QWidget {{ background: {bg}; color: {text}; font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }}
QListWidget#nav {{ background: {panel}; border: none; padding: 10px 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 11px 14px; border-radius: 8px; margin: 2px 0; }}
QListWidget#nav::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, stop:1 {accent2}); color: white; }}
QListWidget#nav::item:hover:!selected {{ background: {card}; }}
QFrame#card {{ background: {card}; border: 1px solid {border}; border-radius: 12px; }}
QFrame#card QLabel, QFrame#card QCheckBox, QCheckBox, QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 17pt; font-weight: 600; }}
QLabel#muted {{ color: {muted}; }}
QLabel#value {{ font-size: 12pt; font-weight: 600; }}
QPushButton {{ background: {card}; border: 1px solid {border}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; }}
QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, stop:1 {accent2}); color: white; border: none; font-weight: 600; }}
QPushButton#danger {{ border-color: {bad}; color: {bad}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QTreeWidget, QTableWidget, QListWidget {{
    background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 5px; selection-background-color: {accent}; }}
QHeaderView::section {{ background: {card}; border: none; padding: 5px; }}
QProgressBar {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def stylesheet(theme: str) -> str:
    return _QSS.format(**_PALETTES.get(theme, _PALETTES["dark"]))


def color(theme: str, name: str) -> str:
    return _PALETTES.get(theme, _PALETTES["dark"])[name]

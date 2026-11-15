"""Robuste Extraktion von JSON aus Modellantworten."""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.errors import ResponseFormatError

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _balanced_object(text: str, start: int) -> str | None:
    """Liefert das bei ``start`` beginnende, vollständig geklammerte JSON-Objekt."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _candidates(text: str) -> list[str]:
    result: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        result.append(stripped)
    result.extend(m.group(1).strip() for m in _FENCE.finditer(text))
    pos = text.find("{")
    while pos != -1 and len(result) < 12:
        obj = _balanced_object(text, pos)
        if obj:
            result.append(obj)
            pos = text.find("{", pos + len(obj))
        else:
            pos = text.find("{", pos + 1)
    return result


def _repair(candidate: str) -> str:
    # häufige Modellfehler: abschließende Kommas, typografische Anführungszeichen als Begrenzer
    fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
    return fixed


def extract_json_object(text: str) -> dict[str, Any]:
    """Findet das erste gültige JSON-Objekt in ``text``.

    Akzeptiert reines JSON, JSON in Markdown-Codeblöcken und JSON mit umgebendem Fließtext.
    Wirft ``ResponseFormatError``, wenn nichts Verwertbares enthalten ist.
    """
    if not text or not text.strip():
        raise ResponseFormatError("Die KI hat eine leere Antwort geliefert.")
    last_error = ""
    for candidate in _candidates(text):
        for variant in (candidate, _repair(candidate)):
            try:
                value = json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = f"{exc.msg} (Zeile {exc.lineno}, Spalte {exc.colno})"
                continue
            if isinstance(value, dict):
                return value
            last_error = "Die Antwort ist kein JSON-Objekt."
    raise ResponseFormatError(
        "Die KI hat kein gültiges JSON geliefert." + (f" Letzter Fehler: {last_error}" if last_error else ""),
        detail=text[:2000],
    )


def summarize_validation_errors(errors: list[dict[str, Any]], limit: int = 12) -> str:
    """Kompakte, für das Modell verständliche Zusammenfassung von Pydantic-Fehlern."""
    lines: list[str] = []
    for error in errors[:limit]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        lines.append(f"- {location or '(Wurzel)'}: {error.get('msg', '')}")
    if len(errors) > limit:
        lines.append(f"- … und {len(errors) - limit} weitere Fehler")
    return "\n".join(lines)

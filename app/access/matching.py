"""Eindeutige Zuordnung von Datensätzen über Textwerte (z. B. „Al Jarreau - Look to the rainbow“ → CD-Nummer 1).

Live-Fund CD-Sammlung: In der Tracks-Tabelle steht „Interpret - Titel“ in EINEM Feld, mit anderer Groß-/Kleinschreibung
und einmal mit Tippfehler („White Rabatt“), in der CD-Tabelle stehen Interpret und Titel getrennt. Ein Vergleich über
gleiche Titel fand nichts. Regeln, damit nie eine falsche Beziehung entsteht:

1. Vergleich normalisiert (Groß-/Kleinschreibung, Leer- und Satzzeichen egal) gegen die zusammengesetzten Felder des
   Kandidaten – oder gegen ein einzelnes seiner Felder. Genau EIN Treffer → zugeordnet.
2. Sonst nur, wenn ein Kandidat klar ähnlicher ist als alle anderen (Mindestähnlichkeit UND Abstand zum Zweitbesten).
3. Mehrdeutig oder zu unähnlich → nicht zugeordnet (wird gemeldet, nichts geschrieben).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

MIN_RATIO = 0.88
MIN_MARGIN = 0.06
#: Trennstellen zusammengesetzter Werte („Interpret - Titel“, „A / B“, „A; B“)
_PARTS = re.compile(r"\s+[-–/|]\s+|;")


def _fits(parts: list[str], fields: list[str]) -> bool:
    # „Archie Shepp“ ist Anfang von „Archie Shepp / Jasper van't Hof“; „Ann“ steckt zwar in „Joanne“, zählt aber nicht
    return any(p in fields for p in parts) and all(
        any(p == f or (len(p) >= 4 and f.startswith(p)) for f in fields) for p in parts)


def match_where(fields: Sequence[str], values: Sequence[Any]) -> str:
    """WHERE-Bedingung für genau diese Werte der Quellfelder (leer = Is Null)."""
    from app.access.naming import quote_ident, sql_literal

    return " AND ".join(f"{quote_ident(f)} Is Null" if v is None else f"{quote_ident(f)} = {sql_literal(v)}"
                        for f, v in zip(fields, values, strict=True))


def normalize(value: Any) -> str:
    """„Al Jarreau - Look to the Rainbow“ → „aljarreaulooktotherainbow“ (Umlaute bleiben Buchstaben)."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value)).casefold()
    return "".join(ch for ch in text if ch.isalnum())


@dataclass
class KeyMapping:
    #: Quellwerte (als Tupel der Quellfelder) → Schlüssel
    exact: dict[tuple[Any, ...], Any] = field(default_factory=dict)
    #: über die Ähnlichkeitsregel zugeordnet (Quelle → (Schlüssel, Ähnlichkeit))
    similar: dict[tuple[Any, ...], tuple[Any, float]] = field(default_factory=dict)
    #: nicht zugeordnet (Quelle → Grund)
    unresolved: dict[tuple[Any, ...], str] = field(default_factory=dict)

    @property
    def keys(self) -> dict[tuple[Any, ...], Any]:
        return {**self.exact, **{s: k for s, (k, _) in self.similar.items()}}


def plan_key_mapping(sources: Sequence[tuple[Any, ...]], candidates: Sequence[tuple[Any, tuple[Any, ...]]],
                     *, min_ratio: float = MIN_RATIO, min_margin: float = MIN_MARGIN) -> KeyMapping:
    """``sources``: verschiedene Werte der Quellfelder; ``candidates``: (Schlüssel, Werte der Vergleichsfelder)."""
    whole = [(key, normalize("".join("" if v is None else str(v) for v in values))) for key, values in candidates]
    fields = {key: {normalize(v) for v in values if normalize(v)} for key, values in candidates}
    single = [(key, normalize(v)) for key, values in candidates for v in values if normalize(v)]
    result = KeyMapping()
    for source in sources:
        wanted = normalize("".join("" if v is None else str(v) for v in source))
        if not wanted:
            result.unresolved[source] = "leer"
            continue
        hits = {key for key, text in whole if text == wanted} or {key for key, text in single if text == wanted}
        if len(hits) == 1:
            result.exact[source] = next(iter(hits))
            continue
        if len(hits) > 1:
            result.unresolved[source] = f"mehrdeutig ({len(hits)} gleiche Kandidaten)"
            continue
        # „Archie Shepp - Mama Rose“ ↔ Interpret „Archie Shepp / Jasper van't Hof“, Titel „Mama Rose“: jeder Teil steckt in
        # einem Feld des Kandidaten und mindestens einer stimmt genau – zwei unabhängige Belege, nur bei EINEM Kandidaten
        parts = [p for v in source for p in (normalize(x) for x in _PARTS.split("" if v is None else str(v))) if p]
        if len(parts) > 1:
            fitting = {key for key, values in candidates if _fits(parts, [normalize(v) for v in values if normalize(v)])}
            if len(fitting) == 1:
                result.exact[source] = next(iter(fitting))
                continue
        scored = sorted(((difflib.SequenceMatcher(None, wanted, text).ratio(), key) for key, text in whole if text),
                        key=lambda item: item[0], reverse=True)
        if not scored:
            result.unresolved[source] = "keine Kandidaten"
            continue
        best, key = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        # Ähnlichkeit allein reicht nie: mindestens ein Teil (z. B. der Interpret) muss genau einem Feld entsprechen
        if best >= min_ratio and best - second >= min_margin and set(parts) & fields[key]:
            result.similar[source] = (key, round(best, 3))
        else:
            result.unresolved[source] = f"kein eindeutiger Kandidat (beste Ähnlichkeit {best:.2f})"
    return result

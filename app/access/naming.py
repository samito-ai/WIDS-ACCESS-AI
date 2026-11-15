"""Regeln für Access-Objektnamen und sichere Bezeichner in SQL."""
from __future__ import annotations

import re

from app.core.errors import InvalidNameError

_FORBIDDEN = set(".!`[]\"")
MAX_NAME_LENGTH = 64


def validate_name(name: str, kind: str = "Objekt") -> str:
    """Prüft einen Tabellen-, Feld-, Abfrage-, Formular- oder Berichtsnamen.

    Access-Regeln: 1–64 Zeichen, keine Zeichen ``. ! ` [ ]``, kein führendes Leerzeichen,
    keine Steuerzeichen. Gibt den (unveränderten) Namen zurück oder wirft ``InvalidNameError``.
    """
    if not isinstance(name, str) or not name:
        raise InvalidNameError(f"{kind}: Der Name darf nicht leer sein.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidNameError(f"{kind} „{name[:20]}…“: Namen dürfen höchstens {MAX_NAME_LENGTH} Zeichen lang sein.")
    if name[0] == " ":
        raise InvalidNameError(f"{kind} „{name}“: Namen dürfen nicht mit einem Leerzeichen beginnen.")
    if name != name.rstrip():
        raise InvalidNameError(f"{kind} „{name}“: Namen dürfen nicht mit einem Leerzeichen enden.")
    bad = sorted({c for c in name if c in _FORBIDDEN})
    if bad:
        raise InvalidNameError(f"{kind} „{name}“: Unzulässige Zeichen {' '.join(bad)}")
    if any(ord(c) < 32 for c in name):
        raise InvalidNameError(f"{kind} „{name}“: Steuerzeichen sind nicht erlaubt.")
    return name


def quote_ident(name: str) -> str:
    """Klammert einen Bezeichner für Access-SQL: ``Name`` -> ``[Name]``."""
    validate_name(name, "Bezeichner")
    return f"[{name}]"


def sql_literal(value: object) -> str:
    """Wandelt einen Python-Wert in ein Access-SQL-Literal um."""
    import datetime as _dt
    from decimal import Decimal

    if value is None:
        return "Null"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int | float | Decimal):
        return repr(value) if not isinstance(value, Decimal) else str(value)
    if isinstance(value, _dt.datetime):
        return value.strftime("#%m/%d/%Y %H:%M:%S#")
    if isinstance(value, _dt.date):
        return value.strftime("#%m/%d/%Y#")
    text = str(value).replace('"', '""')
    return f'"{text}"'


_DATE_DE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def parse_date_literal(text: str) -> str | None:
    """Erkennt ``TT.MM.JJJJ`` oder ``JJJJ-MM-TT`` und liefert ein ``#MM/TT/JJJJ#``-Literal."""
    text = text.strip().strip("#")
    match = _DATE_DE.match(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000 if year < 30 else 1900
    else:
        match = _DATE_ISO.match(text)
        if not match:
            return None
        year, month, day = (int(g) for g in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"#{month:02d}/{day:02d}/{year:04d}#"


def unique_name(base: str, existing: set[str]) -> str:
    """Erzeugt einen noch nicht vergebenen Namen (``Name``, ``Name_2``, ``Name_3`` …)."""
    lowered = {e.lower() for e in existing}
    if base.lower() not in lowered:
        return base
    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = f"{base[: MAX_NAME_LENGTH - len(suffix)]}{suffix}"
        if candidate.lower() not in lowered:
            return candidate
        counter += 1

"""Umwandlung von Werten zwischen Python/JSON und COM/DAO."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from app.access.constants import NUMERIC_TYPES, AccessType
from app.core.errors import DataIntegrityError

_DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%H:%M:%S",
    "%H:%M",
)
_TRUE = {"true", "wahr", "ja", "yes", "1", "-1", "x", "on"}
_FALSE = {"false", "falsch", "nein", "no", "0", "", "off"}


def parse_datetime(value: str) -> dt.datetime:
    text = value.strip().strip("#")
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):
            # reine Uhrzeit -> Access-Basisdatum
            return dt.datetime(1899, 12, 30, parsed.hour, parsed.minute, parsed.second)
        return parsed
    raise DataIntegrityError(f"„{value}“ ist kein gültiges Datum.")


def to_dao(value: Any, access_type: AccessType, field_name: str = "") -> Any:
    """Wandelt einen JSON-/Python-Wert in einen zum Feldtyp passenden COM-Wert um."""
    if value is None:
        return None
    label = f"Feld „{field_name}“: " if field_name else ""
    try:
        if access_type == AccessType.YES_NO:
            if isinstance(value, bool):
                return value
            if isinstance(value, int | float):
                return bool(value)
            text = str(value).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise ValueError(f"kein Ja/Nein-Wert: {value!r}")
        if access_type == AccessType.DATETIME:
            # pywin32 deutet naive datetime-Werte als Ortszeit und rechnet sie nach UTC um.
            # Access-Datumswerte kennen keine Zeitzone – deshalb explizit als UTC markieren,
            # damit der Wert unverändert in der Datenbank ankommt.
            if isinstance(value, dt.datetime):
                return value.replace(tzinfo=dt.UTC)
            if isinstance(value, dt.date):
                return dt.datetime(value.year, value.month, value.day, tzinfo=dt.UTC)
            if isinstance(value, str) and not value.strip():
                return None
            return parse_datetime(str(value)).replace(tzinfo=dt.UTC)
        if access_type in NUMERIC_TYPES:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int | float):
                number: float | int = value
            else:
                text = str(value).strip().replace("€", "").replace(" ", "")
                if not text:
                    return None
                if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", text):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", ".")
                number = float(text)
            if access_type in (
                AccessType.BYTE,
                AccessType.INTEGER,
                AccessType.LONG,
                AccessType.AUTONUMBER,
                AccessType.BIGINT,
            ):
                if float(number) != int(number):
                    raise ValueError(f"keine ganze Zahl: {value!r}")
                return int(number)
            return float(number)
        return str(value)
    except DataIntegrityError as exc:
        raise DataIntegrityError(f"{label}{exc.user_message}") from exc
    except (ValueError, TypeError) as exc:
        raise DataIntegrityError(f"{label}Der Wert {value!r} passt nicht zum Datentyp ({exc}).") from exc


def from_dao(value: Any) -> Any:
    """Wandelt einen COM-Wert in einen JSON-tauglichen Python-Wert um."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dt.datetime):
        naive = value.replace(tzinfo=None)
        if naive.year == 1899 and naive.month == 12 and naive.day == 30:
            return naive.strftime("%H:%M:%S")
        if naive.hour == naive.minute == naive.second == 0:
            return naive.strftime("%d.%m.%Y")
        return naive.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(value, Decimal):
        as_float = float(value)
        return int(as_float) if as_float == int(as_float) and abs(as_float) < 1e15 else as_float
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<Binärdaten, {len(bytes(value))} Bytes>"
    return str(value)


def default_expression(value: str, access_type: AccessType) -> str:
    """Bringt einen Standardwert in die Ausdrucksform, die Access für ``DefaultValue`` erwartet."""
    text = value.strip()
    if not text:
        return ""
    if text.startswith("="):
        return text
    if access_type in (AccessType.SHORT_TEXT, AccessType.LONG_TEXT):
        if text.startswith('"') and text.endswith('"') and len(text) >= 2:
            return text
        return '"' + text.replace('"', '""') + '"'
    if access_type == AccessType.YES_NO:
        lower = text.lower()
        if lower in _TRUE:
            return "True"
        if lower in _FALSE:
            return "False"
        return text
    if access_type == AccessType.DATETIME:
        lower = text.lower().replace(" ", "")
        if lower in ("datum()", "date()", "heute"):
            return "Date()"
        if lower in ("jetzt()", "now()"):
            return "Now()"
        if text.startswith("#"):
            return text
        try:
            parsed = parse_datetime(text)
        except DataIntegrityError:
            return text
        return parsed.strftime("#%m/%d/%Y#")
    if access_type in NUMERIC_TYPES:
        return text.replace(",", ".") if re.fullmatch(r"-?\d+,\d+", text) else text
    return text

"""Deterministische Rechnungen für Rechenfragen – das Ergebnis berechnet WIDS, nicht das Sprachmodell.

Erlaubt sind nur Zahlen, + − × ÷, Potenz (kleine Exponenten), Rest, Klammern und ``round(x, n)``; kein Name, kein
Funktionsaufruf außer ``round``, kein Zugriff auf irgendetwas anderes. Jede Zahl im Ausdruck muss in den gegebenen
Angaben der Aufgabe stehen (bzw. eine übliche Umrechnungskonstante sein) – erfundene Werte werden abgewiesen.
"""
from __future__ import annotations

import ast
import re
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

#: größter zulässiger Betrag eines Zwischen- oder Endergebnisses (Schulrechnungen; schützt vor Speicher-DoS)
LIMIT = 10**15
#: Umrechnungskonstanten, die nicht in der Aufgabe stehen müssen (Prozent, Promille, Zeit, Halbieren)
CONSTANTS = {Fraction(n) for n in (1, 2, 7, 10, 12, 24, 52, 60, 100, 365, 1000)}
_NUMBER = re.compile(r"(?<![\w.,])-?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?(?![\w])|(?<![\w.,])-?\d+(?:[.,]\d+)?(?![\w])")


class CalcError(ValueError):
    pass


def _eval(node: ast.AST, exact: bool = True) -> Any:
    """Erlaubte Knoten auswerten – exakt (Fraction) oder als zweiter Rechenweg in Gleitkomma (float); jedes
    Zwischenergebnis bleibt unter ``LIMIT`` (verschachtelte Potenzen könnten sonst beliebig wachsen)."""
    value = _eval_node(node, exact)
    if abs(value) > LIMIT or (exact and value.denominator > LIMIT):
        raise CalcError("Zahl zu groß bzw. zu fein für eine Rechenfrage")
    return value


def _eval_node(node: ast.AST, exact: bool) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, exact)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return Fraction(str(node.value)) if exact else float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _eval(node.operand, exact)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, exact), _eval(node.right, exact)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div | ast.Mod):
            if right == 0:
                raise CalcError("Division durch null")
            return left / right if isinstance(node.op, ast.Div) else left % right
        if isinstance(node.op, ast.Pow):
            if int(right) != right or abs(right) > 10:
                raise CalcError("nur ganzzahlige Exponenten bis 10")
            if left == 0 and right < 0:
                raise CalcError("Division durch null")
            if abs(left) > 1 and abs(right) * len(str(int(abs(left)))) > 16:
                raise CalcError("Zahl zu groß für eine Rechenfrage")  # vor dem Rechnen: nie riesige Zwischenwerte
            return left ** int(right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "round"
            and not node.keywords and 1 <= len(node.args) <= 2):
        value = _eval(node.args[0], exact)
        digits = int(_eval(node.args[1], exact)) if len(node.args) == 2 else 0
        if not 0 <= digits <= 10:
            raise CalcError("round: 0 bis 10 Nachkommastellen")
        rounded = round_half_up(Fraction(value) if exact else Fraction(str(value)), digits)
        return Fraction(rounded) if exact else float(rounded)
    raise CalcError(f"nicht erlaubter Ausdruck: {ast.dump(node)[:60]}")


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression.replace("×", "*").replace("÷", "/"), mode="eval")  # Dezimalpunkt; Komma trennt Argumente
    except SyntaxError as exc:
        raise CalcError(f"Ausdruck nicht lesbar: {exc.msg}") from exc


def evaluate(expression: str) -> Fraction:
    """Exakter Wert des Ausdrucks (Bruchrechnung, keine Gleitkomma-Rundungsfehler)."""
    return Fraction(_eval(_parse(expression)))


def evaluate_float(expression: str) -> float:
    """Zweiter Rechenweg (Gleitkomma) für die Gegenprüfung – dieselben erlaubten Knoten, kein eval."""
    try:
        return float(_eval(_parse(expression), exact=False))
    except (OverflowError, ZeroDivisionError) as exc:
        raise CalcError(str(exc)) from exc


def round_half_up(value: Fraction, digits: int) -> Decimal:
    """Kaufmännisch runden (2,5 → 3), wie in der Schule – nicht Pythons Bankrundung."""
    quantum = Decimal(1).scaleb(-digits)
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(quantum, rounding=ROUND_HALF_UP)


def literals(expression: str) -> list[Fraction]:
    tree = _parse(expression)
    return [Fraction(str(n.value)) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, int | float) and not isinstance(n.value, bool)]


def numbers_in(text: str) -> set[Fraction]:
    """Zahlen in deutschem oder englischem Format: „1.234,50“, „240,5“, „15“, „3.5“."""
    found: set[Fraction] = set()
    for raw in _NUMBER.findall(text):
        token = raw.replace(" ", "")
        if "," in token:
            token = token.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")  # Tausenderpunkt
        found.add(Fraction(token))
    return found


def unknown_numbers(expression: str, given: str) -> list[str]:
    """Zahlen im Ausdruck, die weder in den gegebenen Angaben stehen noch Umrechnungskonstanten sind."""
    allowed = numbers_in(given) | CONSTANTS
    return [str(n) for n in literals(expression) if n not in allowed and n not in {-x for x in allowed}]


def format_value(value: Fraction, decimals: int | None) -> str:
    """Deutsches Zahlenformat; ohne Vorgabe ganze Zahlen exakt, sonst höchstens 4 Nachkommastellen."""
    if decimals is None:
        if value.denominator == 1:
            return str(value.numerator)
        text = str(round_half_up(value, 4)).rstrip("0").rstrip(".")
    else:
        text = str(round_half_up(value, decimals))
    return text.replace(".", ",")


def quoted_in(given: str, task_text: str) -> bool:
    """Steht der zitierte Ausschnitt (Leerraum egal) tatsächlich in der Aufgabe?"""
    def squash(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()
    return bool(given.strip()) and squash(given) in squash(task_text)

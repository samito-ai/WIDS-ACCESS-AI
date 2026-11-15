"""Erzeugung und Analyse von Access-SQL (Jet/ACE-Dialekt).

* :func:`build_sql` wandelt eine strukturierte :class:`QuerySpec` in Access-SQL um.
* :func:`translate_criteria` übersetzt Kriterien aus dem Access-Entwurfsraster
  (auch deutsche Schlüsselwörter wie ``Wie``, ``Zwischen … Und``) in SQL-Bedingungen.
* :func:`classify_sql` / :func:`ensure_single_statement` prüfen frei formuliertes SQL.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from app.access.constants import NUMERIC_TYPES, AccessType
from app.access.models import DatabaseSchema, RelationInfo
from app.access.naming import parse_date_literal, quote_ident
from app.access.specs import JoinSpec, QueryFieldSpec, QuerySpec
from app.core.errors import PlanValidationError, SqlSyntaxError

# --------------------------------------------------------------------------- Tokenisierung
_TOKEN_RE = re.compile(
    r"""
    (?P<string>"(?:[^"]|"")*"|'(?:[^']|'')*')   # Zeichenfolgen
  | (?P<date>\#[^#\n]*\#)                         # Datumsliterale
  | (?P<bracket>\[[^\]]*\])                       # [Bezeichner] / [Parameter]
  | (?P<number>\d+(?:[.,]\d+)?)                   # Zahlen
  | (?P<op><=|>=|<>|!=|=|<|>)                     # Vergleichsoperatoren
  | (?P<paren>[()])                               # Klammern
  | (?P<comma>[,;])
  | (?P<word>[^\W\d][\w.]*[*?]?|[*?][\w*?.]*)     # Wörter (inkl. Platzhalterresten)
  | (?P<other>\S)
    """,
    re.VERBOSE | re.UNICODE,
)

_GERMAN_KEYWORDS = {
    "wie": "Like",
    "und": "And",
    "oder": "Or",
    "nicht": "Not",
    "zwischen": "Between",
    "ist": "Is",
    "wahr": "True",
    "falsch": "False",
    "ja": "True",
    "nein": "False",
    "in": "In",
    "null": "Null",
}
_ENGLISH_KEYWORDS = {
    "like": "Like",
    "and": "And",
    "or": "Or",
    "not": "Not",
    "between": "Between",
    "is": "Is",
    "true": "True",
    "false": "False",
    "yes": "True",
    "no": "False",
    "in": "In",
    "null": "Null",
}
_KEYWORDS = {**_GERMAN_KEYWORDS, **_ENGLISH_KEYWORDS}
_DATE_WORD = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")


class _Tok:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - nur Debughilfe
        return f"{self.kind}:{self.text}"


def _tokenize(text: str, *, wildcards: bool = True) -> list[_Tok]:
    """Zerlegt ein Kriterium bzw. einen Ausdruck in Tokens.

    ``wildcards=True`` (Kriterien): ``A*`` oder ``*berg`` gelten als Platzhaltertext.
    ``wildcards=False`` (Ausdrücke): ``*`` ist immer der Multiplikationsoperator.
    """
    tokens: list[_Tok] = []
    pos = 0
    # Datumsangaben TT.MM.JJJJ vorab erkennen (würden sonst als Zahl.Zahl.Zahl zerfallen)
    date_re = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        date_match = date_re.match(text, pos)
        if date_match and (date_match.end() == len(text) or not text[date_match.end()].isalnum()):
            tokens.append(_Tok("dateword", date_match.group()))
            pos = date_match.end()
            continue
        match = _TOKEN_RE.match(text, pos)
        if match is None:  # pragma: no cover - \S fängt alles ab
            raise SqlSyntaxError(f"Kriterium nicht lesbar: {text!r}")
        kind = match.lastgroup or "other"
        value = match.group()
        end = match.end()
        if kind == "word" and value[0] in "*?":
            prev = tokens[-1] if tokens else None
            starts_value = prev is None or prev.kind in ("kw", "op", "comma") or prev.text == "("
            if not (wildcards and starts_value):
                # Rechenoperator, kein Platzhalter
                tokens.append(_Tok("other", value[0]))
                pos += 1
                continue
        elif kind == "word" and value[-1] in "*?":
            following = text[end] if end < len(text) else ""
            if not wildcards or following.isalnum() or following in ("[", "("):
                value = value[:-1]
                end -= 1
        if kind == "word" and value.lower() in _KEYWORDS and "*" not in value and "?" not in value:
            tokens.append(_Tok("kw", _KEYWORDS[value.lower()]))
        else:
            tokens.append(_Tok(kind, value))
        pos = end
    return tokens


def _split_top_level(tokens: list[_Tok], keyword: str) -> list[list[_Tok]]:
    """Teilt an ``And``/``Or`` auf oberster Klammerebene; ``And`` nach ``Between`` bleibt erhalten."""
    parts: list[list[_Tok]] = [[]]
    depth = 0
    pending_between = 0
    for tok in tokens:
        if tok.kind == "paren":
            depth += 1 if tok.text == "(" else -1
        if depth == 0 and tok.kind == "kw":
            if tok.text == "Between":
                pending_between += 1
            elif tok.text == "And" and pending_between:
                pending_between -= 1
                parts[-1].append(tok)
                continue
            elif tok.text == keyword:
                parts.append([])
                continue
        parts[-1].append(tok)
    return parts


def _join(tokens: Sequence[_Tok]) -> str:
    out: list[str] = []
    for tok in tokens:
        text = _value_text(tok)
        if (out and (text in (")", ",") or out[-1].endswith("("))) or (tok.text == "(" and out and re.search(r"[\w\]]$", out[-1]) and not _is_keyword_text(out[-1])):
            out[-1] += text
        else:
            out.append(text)
    return " ".join(out)


def _is_keyword_text(text: str) -> bool:
    return text in {"Like", "And", "Or", "Not", "Between", "Is", "In", "="}


def _value_text(tok: _Tok) -> str:
    if tok.kind == "comma":  # deutsches Listentrennzeichen ";" -> SQL-Komma
        return ","
    if tok.kind == "number":
        return tok.text.replace(",", ".")
    if tok.kind == "dateword":
        return parse_date_literal(tok.text) or tok.text
    if tok.kind == "date":
        return parse_date_literal(tok.text) or tok.text
    if tok.kind == "string" and tok.text.startswith("'"):
        inner = tok.text[1:-1].replace("''", "'").replace('"', '""')
        return f'"{inner}"'
    return tok.text


def _quote_bare(words: Sequence[_Tok]) -> str:
    text = " ".join(t.text for t in words).replace('"', '""')
    return f'"{text}"'


def _term_to_sql(expr: str, tokens: list[_Tok]) -> str:
    if not tokens:
        raise SqlSyntaxError("Leeres Kriterium.")
    first = tokens[0]
    # ( ... ) -> rekursiv
    if first.text == "(" and tokens[-1].text == ")" and _balanced(tokens[1:-1]):
        return f"({_criteria_tokens_to_sql(expr, tokens[1:-1])})"
    if first.kind == "op":
        op = "<>" if first.text == "!=" else first.text
        return f"{expr} {op} {_operand(tokens[1:])}"
    if first.kind == "kw":
        if first.text == "Not":
            rest = tokens[1:]
            if rest and rest[0].kind == "kw" and rest[0].text in ("Like", "Between", "In"):
                return f"{expr} Not {_keyword_clause(rest)}"
            if rest and rest[0].kind == "kw" and rest[0].text == "Null":
                return f"{expr} Is Not Null"
            if rest and rest[0].kind == "op":
                return f"Not ({_term_to_sql(expr, rest)})"
            return f"{expr} <> {_operand(rest)}"
        if first.text in ("Like", "Between", "In"):
            return f"{expr} {_keyword_clause(tokens)}"
        if first.text == "Is":
            rest_text = " ".join(t.text for t in tokens[1:])
            if rest_text not in ("Null", "Not Null"):
                raise SqlSyntaxError(f"Ungültiges Kriterium: Ist {rest_text}")
            return f"{expr} Is {rest_text}"
        if first.text == "Null" and len(tokens) == 1:
            return f"{expr} Is Null"
        if first.text in ("True", "False") and len(tokens) == 1:
            return f"{expr} = {first.text}"
    # reiner Wert
    if _is_bare_text(tokens):
        literal = _quote_bare(tokens)
        if "*" in literal or "?" in literal:
            return f"{expr} Like {literal}"
        return f"{expr} = {literal}"
    if len(tokens) == 1 and first.kind == "string" and ("*" in first.text or "?" in first.text):
        return f"{expr} Like {_value_text(first)}"
    return f"{expr} = {_operand(tokens)}"


def _keyword_clause(tokens: list[_Tok]) -> str:
    head = tokens[0].text
    rest = tokens[1:]
    if head == "Like":
        if _is_bare_text(rest):
            return f"Like {_quote_bare(rest)}"
        return f"Like {_operand(rest)}"
    if head == "Between":
        parts = _split_between(rest)
        return f"Between {_operand(parts[0])} And {_operand(parts[1])}"
    # In (...)
    return f"In {_join(_quote_in_list(rest))}"


def _quote_in_list(tokens: list[_Tok]) -> list[_Tok]:
    """Setzt unquotierte Wörter innerhalb einer In-Liste in Anführungszeichen."""
    out: list[_Tok] = []
    for tok in tokens:
        if tok.kind == "word":
            out.append(_Tok("string", _quote_bare([tok])))
        elif tok.kind == "comma":
            out.append(_Tok("comma", ","))
        else:
            out.append(tok)
    return out


def _split_between(tokens: list[_Tok]) -> tuple[list[_Tok], list[_Tok]]:
    depth = 0
    for index, tok in enumerate(tokens):
        if tok.kind == "paren":
            depth += 1 if tok.text == "(" else -1
        if depth == 0 and tok.kind == "kw" and tok.text == "And":
            return tokens[:index], tokens[index + 1 :]
    raise SqlSyntaxError("Bei „Zwischen“ fehlt „Und“.")


def _balanced(tokens: Sequence[_Tok]) -> bool:
    depth = 0
    for tok in tokens:
        if tok.kind == "paren":
            depth += 1 if tok.text == "(" else -1
            if depth < 0:
                return False
    return depth == 0


def _is_bare_text(tokens: Sequence[_Tok]) -> bool:
    """Unquotierter Text wie ``Tirol`` oder ``A*`` (Access setzt ihn automatisch in Anführungszeichen)."""
    if not tokens:
        return False
    if any(t.kind not in ("word", "other") for t in tokens):
        return False
    return not any(t.text in "()" for t in tokens)


def _operand(tokens: Sequence[_Tok]) -> str:
    if not tokens:
        raise SqlSyntaxError("Im Kriterium fehlt ein Vergleichswert.")
    if _is_bare_text(tokens):
        # Funktionsaufrufe wie Datum() bestehen nicht nur aus Wörtern -> landen nicht hier
        return _quote_bare(tokens)
    return _join(_localize_functions(list(tokens)))


_FUNCTION_NAMES_DE = {
    "datum": "Date",
    "jetzt": "Now",
    "jahr": "Year",
    "monat": "Month",
    "tag": "Day",
    "zeit": "Time",
    "links": "Left",
    "rechts": "Right",
    "teil": "Mid",
    "länge": "Len",
    "wenn": "IIf",
    "runden": "Round",
    "datdiff": "DateDiff",
    "datadd": "DateAdd",
    "datteil": "DatePart",
    "istnull": "IsNull",
    "nz": "Nz",
    "format": "Format",
    "summe": "Sum",
    "anzahl": "Count",
    "mittelwert": "Avg",
    "grossbst": "UCase",
    "kleinbst": "LCase",
    "glätten": "Trim",
}


def _localize_functions(tokens: list[_Tok]) -> list[_Tok]:
    out: list[_Tok] = []
    for index, tok in enumerate(tokens):
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if tok.kind == "word" and nxt is not None and nxt.text == "(":
            out.append(_Tok("word", _FUNCTION_NAMES_DE.get(tok.text.lower(), tok.text)))
        else:
            out.append(tok)
    return out


def _criteria_tokens_to_sql(expr: str, tokens: list[_Tok]) -> str:
    or_parts = _split_top_level(tokens, "Or")
    or_sql: list[str] = []
    for or_part in or_parts:
        and_parts = _split_top_level(or_part, "And")
        and_sql = [_term_to_sql(expr, part) for part in and_parts]
        joined = " And ".join(and_sql)
        # Nur echte Und-Verknüpfungen klammern (nicht das „And“ von Between)
        or_sql.append(f"({joined})" if len(and_sql) > 1 and len(or_parts) > 1 else joined)
    return " Or ".join(or_sql)


def _adapt_to_field_type(tokens: list[_Tok], field_type: AccessType) -> list[_Tok]:
    """Wie das Access-Entwurfsraster: Zahlen bei Textfeldern in Anführungszeichen setzen,
    Zahlen-Texte bei Zahlenfeldern entquoten – sonst meldet Access „Datentypenkonflikt“."""
    text_field = field_type in (AccessType.SHORT_TEXT, AccessType.LONG_TEXT)
    numeric = field_type in NUMERIC_TYPES
    out: list[_Tok] = []
    call_depth = 0  # Argumente von Funktionsaufrufen bleiben unverändert
    for index, tok in enumerate(tokens):
        if tok.text == "(" and (call_depth or (index and tokens[index - 1].kind == "word")):
            call_depth += 1
        elif tok.text == ")" and call_depth:
            call_depth -= 1
        elif not call_depth and text_field and tok.kind == "number":
            tok = _Tok("string", f'"{tok.text}"')
        elif not call_depth and numeric and tok.kind == "string" and re.fullmatch(r"-?\d+([.,]\d+)?", tok.text[1:-1]):
            tok = _Tok("number", tok.text[1:-1])
        out.append(tok)
    return out


def translate_criteria(expr: str, criteria: str, field_type: AccessType | None = None) -> str:
    """Übersetzt ein Entwurfsraster-Kriterium in eine SQL-Bedingung für ``expr``.

    >>> translate_criteria("[Ort]", '"Imst"')
    '[Ort] = "Imst"'
    >>> translate_criteria("[Preis]", ">100 Und <=500")
    '[Preis] > 100 And [Preis] <= 500'
    >>> translate_criteria("[Name]", "Wie A*")
    '[Name] Like "A*"'
    """
    text = criteria.strip()
    if text.startswith("="):
        text = text[1:].strip() or text
    if not text:
        raise SqlSyntaxError("Leeres Kriterium.")
    tokens = _tokenize(text)
    if field_type is not None:
        tokens = _adapt_to_field_type(tokens, field_type)
    return _criteria_tokens_to_sql(expr, tokens)


def localize_expression(expression: str) -> str:
    """Wandelt deutsche Funktionsnamen/Dezimalkommas eines Ausdrucks in SQL-Schreibweise um."""
    tokens = _tokenize(expression.strip().lstrip("="), wildcards=False)
    converted: list[_Tok] = []
    for tok in _localize_functions(tokens):
        if tok.kind == "comma" and tok.text == ";":
            converted.append(_Tok("comma", ","))
        elif tok.kind == "kw":
            converted.append(_Tok("word", tok.text))
        else:
            converted.append(tok)
    text = _join(converted)
    # Leerzeichen um Rechenoperatoren normalisieren
    text = re.sub(r"\s*([*/+\-&^])\s*", r" \1 ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# --------------------------------------------------------------------------- SQL-Erzeugung
_AGG_SQL = {
    "sum": "Sum",
    "count": "Count",
    "avg": "Avg",
    "min": "Min",
    "max": "Max",
    "first": "First",
    "last": "Last",
    "stdev": "StDev",
    "var": "Var",
}
_AGG_ALIAS_DE = {
    "sum": "Summevon",
    "count": "Anzahlvon",
    "avg": "Mittelwertvon",
    "min": "Minvon",
    "max": "Maxvon",
    "first": "ErsterWertvon",
    "last": "LetzterWertvon",
    "stdev": "StdAbwvon",
    "var": "Varianzvon",
}
_PARAM_DDL = {
    AccessType.SHORT_TEXT: "Text ( 255 )",
    AccessType.LONG_TEXT: "LongText",
    AccessType.BYTE: "Byte",
    AccessType.INTEGER: "Short",
    AccessType.LONG: "Long",
    AccessType.AUTONUMBER: "Long",
    AccessType.SINGLE: "IEEESingle",
    AccessType.DOUBLE: "IEEEDouble",
    AccessType.DECIMAL: "Decimal",
    AccessType.CURRENCY: "Currency",
    AccessType.DATETIME: "DateTime",
    AccessType.YES_NO: "Bit",
    AccessType.GUID: "Guid",
}


class _Context:
    def __init__(self, spec: QuerySpec, schema: DatabaseSchema | None) -> None:
        self.spec = spec
        self.schema = schema
        self.multi = len(spec.tables) > 1

    def qualify(self, fld: QueryFieldSpec) -> str:
        """Liefert den (ggf. qualifizierten) SQL-Ausdruck einer Spalte ohne Aggregat/Alias."""
        if fld.expression:
            return localize_expression(fld.expression)
        assert fld.field is not None
        name = fld.field.strip()
        table = fld.table
        if name == "*":
            return f"{quote_ident(table)}.*" if table else "*"
        if "." in name and not table and not name.startswith("["):
            table, name = name.split(".", 1)
        name = name.strip("[]")
        if table:
            table = table.strip("[]")
        if not table and self.multi:
            table = self._owner(name)
        return f"{quote_ident(table)}.{quote_ident(name)}" if table else quote_ident(name)

    def field_type(self, fld: QueryFieldSpec) -> AccessType | None:
        """Datentyp eines Tabellenfelds (``None`` bei Ausdrücken, Abfragen als Quelle oder ohne Schema)."""
        if self.schema is None or not fld.field or fld.expression:
            return None
        name = fld.field.strip("[]").split(".")[-1].strip("[]")
        for source in [fld.table] if fld.table else self.spec.tables:
            table = self.schema.table(source.strip("[]"))
            info = table.field(name) if table else None
            if info is not None:
                return info.type
        return None

    def _owner(self, field: str) -> str | None:
        if self.schema is None:
            return None
        owners = [t for t in self.spec.tables if field.lower() in (f.lower() for f in self.schema.source_fields(t))]
        if len(owners) == 1:
            return owners[0]
        if len(owners) > 1:
            raise PlanValidationError(
                f"Das Feld „{field}“ kommt in mehreren Tabellen vor ({', '.join(owners)}). "
                "Bitte die Tabelle angeben."
            )
        return None


def infer_joins(tables: Sequence[str], relations: Sequence[RelationInfo]) -> list[JoinSpec]:
    """Leitet Verknüpfungen aus den gespeicherten Beziehungen ab."""
    wanted = {t.lower() for t in tables}
    joins: list[JoinSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    for rel in relations:
        if rel.table.lower() in wanted and rel.foreign_table.lower() in wanted:
            for primary, foreign in rel.fields:
                key = (rel.table.lower(), primary.lower(), rel.foreign_table.lower(), foreign.lower())
                if key in seen:
                    continue
                seen.add(key)
                joins.append(
                    JoinSpec(
                        left_table=rel.table,
                        right_table=rel.foreign_table,
                        left_field=primary,
                        right_field=foreign,
                        type="inner",
                    )
                )
    return joins


def _from_clause(tables: Sequence[str], joins: Sequence[JoinSpec]) -> str:
    if not joins:
        return ", ".join(quote_ident(t) for t in tables)
    remaining = list(joins)
    first = remaining.pop(0)
    included = {first.left_table.lower(), first.right_table.lower()}
    clause = _join_sql(quote_ident(first.left_table), first, first.right_table, flipped=False)
    progress = True
    while remaining and progress:
        progress = False
        for join in list(remaining):
            left_in = join.left_table.lower() in included
            right_in = join.right_table.lower() in included
            if left_in and right_in:
                # zusätzliche Bedingung zwischen bereits verbundenen Tabellen -> als AND anhängen
                clause += (
                    f" AND {quote_ident(join.left_table)}.{quote_ident(join.left_field)} = "
                    f"{quote_ident(join.right_table)}.{quote_ident(join.right_field)}"
                )
            elif left_in:
                clause = _join_sql(f"({clause})", join, join.right_table, flipped=False)
                included.add(join.right_table.lower())
            elif right_in:
                clause = _join_sql(f"({clause})", join, join.left_table, flipped=True)
                included.add(join.left_table.lower())
            else:
                continue
            remaining.remove(join)
            progress = True
    if remaining:
        names = ", ".join(f"{j.left_table}–{j.right_table}" for j in remaining)
        raise PlanValidationError(f"Die Verknüpfungen bilden keine zusammenhängende Kette: {names}")
    extra = [t for t in tables if t.lower() not in included]
    if extra:
        clause = ", ".join([clause, *(quote_ident(t) for t in extra)])
    return clause


def _join_sql(left_sql: str, join: JoinSpec, new_table: str, *, flipped: bool) -> str:
    kind = join.type
    if flipped and kind != "inner":
        kind = "right" if kind == "left" else "left"
    keyword = {"inner": "INNER JOIN", "left": "LEFT JOIN", "right": "RIGHT JOIN"}[kind]
    condition = (
        f"{quote_ident(join.left_table)}.{quote_ident(join.left_field)} = "
        f"{quote_ident(join.right_table)}.{quote_ident(join.right_field)}"
    )
    return f"{left_sql} {keyword} {quote_ident(new_table)} ON {condition}"


def _default_alias(fld: QueryFieldSpec) -> str | None:
    if fld.alias:
        return fld.alias
    if fld.aggregate in _AGG_ALIAS_DE and fld.field and fld.field != "*":
        return f"{_AGG_ALIAS_DE[fld.aggregate]}{fld.field.strip('[]').split('.')[-1]}"
    if fld.aggregate == "count" and fld.field == "*":
        return "Anzahl"
    return None


def _column_expr(ctx: _Context, fld: QueryFieldSpec, *, totals: bool) -> str:
    base = ctx.qualify(fld)
    if totals and fld.aggregate in _AGG_SQL:
        return f"{_AGG_SQL[fld.aggregate]}({base})"
    return base


def build_sql(spec: QuerySpec, schema: DatabaseSchema | None = None) -> str:
    """Erzeugt Access-SQL aus einer strukturierten Abfragedefinition."""
    ctx = _Context(spec, schema)
    joins = list(spec.joins)
    if ctx.multi and not joins and schema is not None:
        joins = infer_joins(spec.tables, schema.relations)
    from_sql = _from_clause(spec.tables, joins)

    params_sql = ""
    if spec.parameters:
        decl = ", ".join(
            f"{quote_ident(p.name)} {_PARAM_DDL.get(p.type, 'Text ( 255 )')}" for p in spec.parameters
        )
        params_sql = f"PARAMETERS {decl};\n"

    if spec.kind == "crosstab":
        return params_sql + _build_crosstab(ctx, from_sql)

    totals = any(f.aggregate for f in spec.fields)
    select_cols: list[str] = []
    group_by: list[str] = []
    order_by: list[str] = []
    rows = 1 + max((len(f.or_criteria) for f in spec.fields), default=0)
    where_rows: list[list[str]] = [[] for _ in range(rows)]
    having_rows: list[list[str]] = [[] for _ in range(rows)]

    for fld in spec.fields:
        aggregate = fld.aggregate
        if totals and aggregate is None:
            aggregate = "group_by"
        base = ctx.qualify(fld)
        expr = _column_expr(ctx, fld, totals=totals)
        is_star = base.endswith("*")
        if totals and aggregate == "group_by" and not is_star:
            group_by.append(base)
        show = fld.show and aggregate != "where"
        if show:
            alias = _default_alias(fld) if (fld.expression or aggregate in _AGG_SQL) else fld.alias
            select_cols.append(f"{expr} AS {quote_ident(alias)}" if alias else expr)
        if fld.sort and not is_star:
            order_by.append(f"{expr}{' DESC' if fld.sort == 'DESC' else ''}")
        criteria_rows = [fld.criteria, *fld.or_criteria]
        for index, criteria in enumerate(criteria_rows):
            if not criteria or not criteria.strip():
                continue
            if aggregate in _AGG_SQL:  # Kriterium gilt dem berechneten Wert, nicht dem Feld
                condition = translate_criteria(expr, criteria)
            else:
                condition = translate_criteria(base, criteria, ctx.field_type(fld))
            if totals and aggregate != "where":
                having_rows[index].append(condition)
            else:
                where_rows[index].append(condition)

    where_sql = _combine_rows(where_rows)
    if spec.where:
        extra = spec.where.strip().rstrip(";")
        where_sql = f"({where_sql}) AND ({extra})" if where_sql else extra
    having_sql = _combine_rows(having_rows)
    if spec.having:
        extra = spec.having.strip().rstrip(";")
        having_sql = f"({having_sql}) AND ({extra})" if having_sql else extra

    if spec.kind == "update":
        assignments = ", ".join(
            f"{_qualify_name(ctx, name)} = {localize_expression(value)}" for name, value in spec.set_values.items()
        )
        sql = f"UPDATE {from_sql} SET {assignments}"
        if where_sql:
            sql += f"\nWHERE {where_sql}"
        return params_sql + sql + ";"
    if spec.kind == "delete":
        target = quote_ident(spec.tables[0])
        sql = f"DELETE {target}.*\nFROM {from_sql}"
        if where_sql:
            sql += f"\nWHERE {where_sql}"
        return params_sql + sql + ";"

    if not select_cols:
        raise PlanValidationError("Die Abfrage zeigt kein Feld an.")
    head = "SELECT"
    if spec.distinct:
        head += " DISTINCT"
    if spec.top:
        head += f" TOP {spec.top}" + (" PERCENT" if spec.top_percent else "")
    select_sql = f"{head} {', '.join(select_cols)}"

    if spec.kind == "append":
        assert spec.target_table
        columns = []
        for fld in spec.fields:
            if fld.show and (fld.aggregate != "where"):
                columns.append(quote_ident(fld.alias or (fld.field or "").strip("[]").split(".")[-1]))
        sql = f"INSERT INTO {quote_ident(spec.target_table)} ( {', '.join(columns)} )\n{select_sql}\nFROM {from_sql}"
    elif spec.kind == "make_table":
        assert spec.target_table
        sql = f"{select_sql} INTO {quote_ident(spec.target_table)}\nFROM {from_sql}"
    else:
        sql = f"{select_sql}\nFROM {from_sql}"
    if where_sql:
        sql += f"\nWHERE {where_sql}"
    if group_by:
        sql += f"\nGROUP BY {', '.join(dict.fromkeys(group_by))}"
    if having_sql:
        sql += f"\nHAVING {having_sql}"
    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    return params_sql + sql + ";"


def _qualify_name(ctx: _Context, name: str) -> str:
    return ctx.qualify(QueryFieldSpec(field=name))


def _combine_rows(rows: list[list[str]]) -> str:
    parts = [" AND ".join(f"({c})" for c in row) for row in rows if row]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " OR ".join(f"({part})" for part in parts)


def _build_crosstab(ctx: _Context, from_sql: str) -> str:
    spec = ctx.spec
    assert spec.crosstab is not None
    cross = spec.crosstab
    value = ctx.qualify(QueryFieldSpec(field=cross.value_field))
    agg = _AGG_SQL.get(cross.value_aggregate, "Sum")
    rows = [ctx.qualify(QueryFieldSpec(field=r)) for r in cross.row_headings]
    pivot = ctx.qualify(QueryFieldSpec(field=cross.column_heading))
    where_parts: list[str] = []
    for fld in spec.fields:
        if fld.criteria:
            where_parts.append(f"({translate_criteria(ctx.qualify(fld), fld.criteria, ctx.field_type(fld))})")
    if spec.where:
        where_parts.append(f"({spec.where.strip().rstrip(';')})")
    sql = f"TRANSFORM {agg}({value}) AS {quote_ident(_AGG_ALIAS_DE.get(cross.value_aggregate, 'Summevon') + cross.value_field)}"
    sql += f"\nSELECT {', '.join(rows)}\nFROM {from_sql}"
    if where_parts:
        sql += f"\nWHERE {' AND '.join(where_parts)}"
    sql += f"\nGROUP BY {', '.join(rows)}\nPIVOT {pivot};"
    return sql


# --------------------------------------------------------------------------- SQL-Analyse
_MASK_RE = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|\[[^\]]*\]|#[^#\n]*#')


def mask_literals(sql: str) -> str:
    """Ersetzt Zeichenfolgen, Datumsliterale und [Bezeichner] durch Platzhalter gleicher Länge."""
    return _MASK_RE.sub(lambda m: "_" * len(m.group()), sql)


def ensure_single_statement(sql: str) -> str:
    """Stellt sicher, dass genau eine SQL-Anweisung vorliegt, und gibt sie bereinigt zurück."""
    text = sql.strip()
    if not text:
        raise SqlSyntaxError("Die SQL-Anweisung ist leer.")
    masked = mask_literals(text)
    body = masked.rstrip().rstrip(";")
    upper = body.upper()
    if upper.lstrip().startswith("PARAMETERS"):
        first = body.find(";")
        if first == -1:
            raise SqlSyntaxError("Nach der PARAMETERS-Deklaration fehlt ein Semikolon.")
        body = body[first + 1 :]
    if ";" in body:
        raise SqlSyntaxError("Es ist nur eine einzelne SQL-Anweisung erlaubt.")
    return text


def classify_sql(sql: str) -> str:
    """Bestimmt die Art einer SQL-Anweisung.

    Rückgabe: ``select``, ``union``, ``crosstab``, ``update``, ``delete``, ``append``,
    ``make_table``, ``ddl`` oder ``unknown``.
    """
    masked = mask_literals(sql).strip()
    upper = re.sub(r"\s+", " ", masked.upper())
    if upper.startswith("PARAMETERS"):
        first = upper.find(";")
        upper = upper[first + 1 :].strip() if first != -1 else upper
    upper = upper.lstrip("( ")
    if upper.startswith("TRANSFORM"):
        return "crosstab"
    if upper.startswith("SELECT"):
        # zuerst INTO: „SELECT * INTO Neu FROM (… UNION …)“ ist eine Tabellenerstellungsabfrage, keine Union
        into = re.search(r"\bINTO\b", upper)
        frm = re.search(r"\bFROM\b", upper)
        if into and (frm is None or into.start() < frm.start()):
            return "make_table"
        if re.search(r"\bUNION\b", upper):
            return "union"
        return "select"
    if upper.startswith("UPDATE"):
        return "update"
    if upper.startswith("DELETE"):
        return "delete"
    if upper.startswith("INSERT"):
        return "append"
    if upper.startswith(("CREATE", "ALTER", "DROP")):
        return "ddl"
    return "unknown"


READ_ONLY_KINDS = frozenset({"select", "union", "crosstab"})
DESTRUCTIVE_KINDS = frozenset({"delete", "update", "ddl", "make_table", "append"})


def ensure_safe_fragment(fragment: str, what: str = "Bedingung") -> str:
    """Prüft ein SQL-Fragment (z. B. WHERE-Bedingung) auf eingeschleuste Anweisungen."""
    text = fragment.strip().rstrip(";").strip()
    if not text:
        raise SqlSyntaxError(f"Die {what} ist leer.")
    masked = mask_literals(text)
    if ";" in masked:
        raise SqlSyntaxError(f"Die {what} darf kein Semikolon enthalten.")
    if re.search(r"\b(DROP|ALTER|CREATE|INSERT|UPDATE|DELETE|EXEC|EXECUTE|SHELL)\b", masked, re.IGNORECASE):
        raise SqlSyntaxError(f"Die {what} enthält ein unzulässiges Schlüsselwort.")
    return text

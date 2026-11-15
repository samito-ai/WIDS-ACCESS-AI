"""Regelbasierte Aufgabenklassifikation (ohne KI).

Dient als schnelle Vorabschätzung, als Rückfallebene ohne API-Schlüssel und zur Erkennung
wiederkehrender Aufgabentypen im Verlauf. Die eigentliche Planung übernimmt die KI.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.tasks.models import TaskType

_RULES: list[tuple[TaskType, float, list[str]]] = [
    (TaskType.REPORT, 3.0, [r"\bbericht", r"\breport\b", r"berichtskopf", r"seitenfu[ßs]", r"gruppierebene"]),
    (TaskType.FORM, 3.0, [r"\bformular", r"\bform\b", r"kombinationsfeld", r"kontrollk[äa]stchen", r"steuerelement",
                          r"bezeichnungsfeld", r"schaltfl[äa]che", r"unterformular", r"navigationsformular",
                          r"bedingte formatierung", r"registerkarte"]),
    (TaskType.RELATIONSHIP, 3.0, [r"\bbeziehung", r"referenzielle[rn]? integrit[äa]t", r"fremdschl[üu]ssel",
                                  r"\b1\s*:\s*n\b", r"\b1\s*:\s*1\b", r"\bn\s*:\s*m\b", r"verkn[üu]pf\w* (sie )?die tabelle",
                                  r"aktualisierungsweitergabe", r"l[öo]schweitergabe"]),
    (TaskType.CROSSTAB_QUERY, 3.5, [r"kreuztabelle", r"crosstab", r"\bpivot\b"]),
    (TaskType.ACTION_QUERY, 3.0, [r"aktualisierungsabfrage", r"l[öo]schabfrage", r"anf[üu]geabfrage",
                                  r"tabellenerstellungsabfrage", r"aktionsabfrage", r"\bupdate\b.*\bset\b"]),
    (TaskType.PARAMETER_QUERY, 3.0, [r"parameterabfrage", r"\bparameter\b", r"eingabeaufforderung",
                                     r"beim (ausf[üu]hren|[öo]ffnen) (der abfrage )?(nach|abgefragt|eingegeben)",
                                     r"\[[^\]]*(eingeben|bitte|welche[rs]?)[^\]]*\]"]),
    (TaskType.AGGREGATE_QUERY, 2.5, [r"\bsumme\b", r"\banzahl\b", r"mittelwert", r"durchschnitt", r"\bgruppier",
                                     r"group by", r"\bhaving\b", r"\bmaximum\b", r"\bminimum\b", r"funktionen?zeile",
                                     r"\bsum\(|\bcount\(|\bavg\(", r"aggregat", r"insgesamt je", r"\bpro (kunde|kategorie|ort|land)"]),
    (TaskType.CALCULATED_FIELD, 2.5, [r"berechnete[sn]? feld", r"berechne[nt]?\b", r"\bausdruck\b", r"multiplizier",
                                      r"\[\w+\]\s*[*/+\-]\s*\[\w+\]", r"neue[sn]? feld .* (ergibt|berechnet)"]),
    (TaskType.SELECT_QUERY, 2.0, [r"\babfrage\b", r"auswahlabfrage", r"\bquery\b", r"\bselect\b", r"\bqry\w*"]),
    (TaskType.CRITERIA, 1.5, [r"kriteri", r"\bfilter", r"nur (die|jene|alle)", r"beginnen mit", r"gr[öo][ßs]er als",
                              r"kleiner als", r"\bzwischen\b", r"\bwie\b \"", r"\blike\b", r"\baus (tirol|wien|\w+) (stammen|kommen)",
                              r"enth[äa]lt", r"platzhalter"]),
    (TaskType.SORTING, 1.5, [r"sortier", r"aufsteigend", r"absteigend", r"alphabetisch", r"order by"]),
    (TaskType.PRIMARY_KEY, 2.5, [r"prim[äa]rschl[üu]ssel", r"primary key", r"schl[üu]sselfeld"]),
    (TaskType.DATATYPE, 2.0, [r"datentyp", r"felddatentyp", r"autowert", r"kurzer text", r"langer text", r"w[äa]hrung",
                              r"ja/nein", r"datum/uhrzeit", r"long integer", r"\bmemo\b"]),
    (TaskType.FIELD_DEFINITION, 2.0, [r"feldgr[öo][ßs]e", r"standardwert", r"g[üu]ltigkeitsregel", r"g[üu]ltigkeitsmeldung",
                                      r"eingabe erforderlich", r"pflichtfeld", r"eingabeformat", r"feldeigenschaft",
                                      r"\bindiziert\b", r"\bindex\b", r"dezimalstellen", r"beschriftung des feldes",
                                      r"f[üu]gen sie .* feld", r"neues feld", r"feld .* (umbenennen|l[öo]schen|hinzuf[üu]gen)"]),
    (TaskType.TABLE, 2.0, [r"\btabelle\b.*(erstell|anleg|neu)", r"(erstell|leg)\w* .*\btabelle\b", r"tabellenentwurf",
                           r"entwurfsansicht der tabelle", r"tabelle .* (umbenennen|kopieren|l[öo]schen)", r"\btbl\w+"]),
    (TaskType.DATA_EDITING, 2.0, [r"datens[äa]tz\w* (ein|an|hinzu)f[üu]g", r"f[üu]gen sie .* datens[äa]tz",
                                  r"datens[äa]tz\w* .* l[öo]sch", r"l[öo]schen sie .* datens[äa]tz",
                                  r"[äa]ndern sie .* (wert|eintrag|datensatz)", r"geben sie .* (daten|datens[äa]tze) ein",
                                  r"neue[rn]? (kunde|artikel|datensatz)", r"erfassen sie", r"importier",
                                  r"exportier", r"textdatei", r"excel-?(tabelle|datei|arbeitsmappe)",
                                  r"sql[- ]server", r"odbc", r"datenbankserver"]),
]

_MC_PATTERNS = [
    r"(?m)^\s*(?:[a-eA-E][\)\.:]|\(\s*[a-eA-E]\s*\)|[☐☑□■○●])\s+\S",
    r"welche (der folgenden )?(aussage|antwort|option)\w* (ist|sind|trifft|treffen)",
    r"kreuzen sie",
    r"richtig oder falsch",
    r"wahr oder falsch",
    r"(?m)^\s*\[\s?\]\s+\S",
]
_THEORY_PATTERNS = [
    r"^\s*(was|wie|warum|weshalb|wozu|wof[üu]r|welche[rsn]?|wann|worin|wodurch)\b.*\?",
    r"erkl[äa]ren sie",
    r"erl[äa]utern sie",
    r"beschreiben sie",
    r"definieren sie",
    r"nennen sie",
    r"was versteht man unter",
    r"unterschied zwischen",
    r"was bedeutet",
    r"wozu dient",
]
_ACTION_VERBS = (
    r"\b(erstell|leg\w* .* an\b|[öo]ffn|speicher|f[üu]g\w* .* (hinzu|ein)|[äa]nder|l[öo]sch|benenn|setz|sortier|"
    r"verkn[üu]pf|kopier|richt\w* .* ein|defin\w* .* (prim|feld|beziehung)|bearbeit|gruppier|formatier)\w*"
)


class Classification(BaseModel):
    task_type: TaskType = TaskType.UNKNOWN
    additional_types: list[TaskType] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    is_question: bool = False

    @property
    def all_types(self) -> list[TaskType]:
        return [self.task_type, *self.additional_types]


def classify_task(text: str) -> Classification:
    """Ordnet einen Aufgabentext einem (oder mehreren) Aufgabentypen zu."""
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()
    if not lowered:
        return Classification()
    scores: dict[TaskType, float] = {}
    for task_type, weight, patterns in _RULES:
        hits = sum(1 for pattern in patterns if re.search(pattern, lowered))
        if hits:
            scores[task_type] = weight * (1 + 0.35 * (hits - 1))

    has_action = bool(re.search(_ACTION_VERBS, lowered))
    mc_hits = sum(1 for pattern in _MC_PATTERNS if re.search(pattern, text, re.IGNORECASE))
    option_lines = len(re.findall(r"(?m)^\s*(?:[a-eA-E][\)\.:]|\(\s*[a-eA-E]\s*\)|[☐☑□○]|\[\s?\])\s+\S", text))
    theory_hits = sum(1 for pattern in _THEORY_PATTERNS if re.search(pattern, lowered))

    if option_lines >= 2 or (mc_hits >= 2 and not has_action):
        scores[TaskType.MULTIPLE_CHOICE] = 4.0 + option_lines * 0.5
    elif theory_hits and not has_action:
        scores[TaskType.THEORY] = 3.5 + theory_hits * 0.5
    elif theory_hits and has_action:
        scores[TaskType.THEORY] = 1.0

    # Verfeinerungen: spezifischere Abfragetypen verdrängen die allgemeine Auswahlabfrage
    specific_queries = {
        TaskType.PARAMETER_QUERY,
        TaskType.AGGREGATE_QUERY,
        TaskType.CALCULATED_FIELD,
        TaskType.ACTION_QUERY,
        TaskType.CROSSTAB_QUERY,
    }
    if TaskType.SELECT_QUERY in scores:
        for specific in specific_queries & scores.keys():
            scores[specific] += 1.0
    else:
        # „Summe“, „berechnen“ usw. ohne Abfragebezug sind schwache Signale (z. B. Berichtssummen)
        for specific in (TaskType.AGGREGATE_QUERY, TaskType.CALCULATED_FIELD):
            if specific in scores and (TaskType.REPORT in scores or TaskType.FORM in scores):
                scores[specific] *= 0.4
    if TaskType.REPORT in scores and TaskType.SELECT_QUERY in scores and not re.search(r"abfrage\w* (erstell|speicher)|erstell\w* .* abfrage", lowered):
        scores[TaskType.SELECT_QUERY] *= 0.5

    if not scores:
        return Classification(is_question=bool(theory_hits) or lowered.endswith("?"))

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    structural = [t for t, s in ranked if t not in (TaskType.CRITERIA, TaskType.SORTING) and s >= 2.0]
    task_type = best_type
    if best_type not in (TaskType.MULTIPLE_CHOICE, TaskType.THEORY):
        families = {_family(t) for t in structural}
        if len(families) >= 3:
            task_type = TaskType.MIXED
    additional = [t for t, s in ranked if t != task_type and s >= 1.5][:5]
    total = sum(scores.values())
    confidence = min(0.95, 0.35 + 0.6 * (best_score / total)) if total else 0.0
    if task_type == TaskType.MIXED:
        confidence = min(confidence, 0.6)
    return Classification(
        task_type=task_type,
        additional_types=additional,
        scores={t.value: round(s, 2) for t, s in ranked},
        confidence=round(confidence, 2),
        is_question=task_type in (TaskType.MULTIPLE_CHOICE, TaskType.THEORY),
    )


def _family(task_type: TaskType) -> str:
    if task_type in (
        TaskType.SELECT_QUERY,
        TaskType.PARAMETER_QUERY,
        TaskType.AGGREGATE_QUERY,
        TaskType.CALCULATED_FIELD,
        TaskType.ACTION_QUERY,
        TaskType.CROSSTAB_QUERY,
    ):
        return "query"
    if task_type in (TaskType.TABLE, TaskType.DATATYPE, TaskType.FIELD_DEFINITION, TaskType.PRIMARY_KEY):
        return "table"
    return task_type.value

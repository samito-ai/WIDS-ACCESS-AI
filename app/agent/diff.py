"""Tatsächliche Änderungen zwischen zwei Datenbankständen – unabhängig davon, was geplant war."""
from __future__ import annotations

import re

from app.access.models import DatabaseSchema

_FIELD_ATTRS = ("type", "size", "required", "primary_key", "default_value", "validation_rule", "indexed",
                "description", "caption", "format")


def _by_name(items: list) -> dict:  # type: ignore[type-arg]
    return {item.name.lower(): item for item in items}


def _datasheet_change(old: str, new: str, limit: int = 300) -> str:
    """Nur die geänderten Teile der Datenblattbeschreibung („Formate: …; Spaltenbreiten: …“), gekürzt – die
    Zeile landet auch im Prompt der Anforderungsprüfung."""
    a, b = old.split("; ") if old else [], new.split("; ") if new else []

    def short(parts: list[str]) -> str:
        text = "; ".join(parts) or "(nichts)"
        return text if len(text) <= limit else text[:limit] + " …"

    return f"{short([p for p in a if p not in b])} → {short([p for p in b if p not in a])}"


def schema_diff(before: DatabaseSchema, after: DatabaseSchema) -> list[str]:
    lines: list[str] = []
    for label, old, new in (("Tabelle", before.tables, after.tables), ("Abfrage", before.queries, after.queries),
                            ("Formular", before.forms, after.forms), ("Bericht", before.reports, after.reports)):
        old_names, new_names = _by_name(old), _by_name(new)
        lines += [f"neu: {label} „{new_names[k].name}“" for k in new_names.keys() - old_names.keys()]
        lines += [f"entfernt: {label} „{old_names[k].name}“" for k in old_names.keys() - new_names.keys()]
    for label, old_objs, new_objs in (("Formular", before.forms, after.forms), ("Bericht", before.reports, after.reports)):
        old_by, new_by = _by_name(old_objs), _by_name(new_objs)
        for key in sorted(old_by.keys() & new_by.keys()):
            a, b = old_by[key], new_by[key]
            if not (a.detailed and b.detailed):
                continue
            for prop in sorted(set(a.properties) | set(b.properties)):
                if a.properties.get(prop) != b.properties.get(prop):
                    lines.append(f"{label} „{b.name}“: {prop} {str(a.properties.get(prop, '(nichts)'))[:150]} → "
                                 f"{str(b.properties.get(prop, '(nichts)'))[:150]}")
    old_tables, new_tables = _by_name(before.tables), _by_name(after.tables)
    for key in sorted(old_tables.keys() & new_tables.keys()):
        old, new = old_tables[key], new_tables[key]
        old_fields, new_fields = _by_name(old.fields), _by_name(new.fields)
        lines += [f"neu: Feld „{new.name}.{new_fields[k].name}“ ({new_fields[k].type_label})"
                  for k in new_fields.keys() - old_fields.keys()]
        lines += [f"entfernt: Feld „{old.name}.{old_fields[k].name}“" for k in old_fields.keys() - new_fields.keys()]
        for k in old_fields.keys() & new_fields.keys():
            a, b = old_fields[k], new_fields[k]
            changed = [attr for attr in _FIELD_ATTRS if getattr(a, attr) != getattr(b, attr)]
            if changed:
                lines.append(f"geändert: Feld „{new.name}.{b.name}“ ({', '.join(f'{c}: {getattr(a, c)!r} → {getattr(b, c)!r}' for c in changed)})")
        if old.record_count != new.record_count:
            lines.append(f"Datensätze in „{new.name}“: {old.record_count} → {new.record_count}")
        if old.order_by != new.order_by:
            lines.append(f"Sortierung von „{new.name}“: {old.order_by or '(keine)'} → {new.order_by or '(keine)'}")
        if old.filter != new.filter:
            lines.append(f"Filter von „{new.name}“: {old.filter or '(keiner)'} → {new.filter or '(keiner)'}")
        if old.datasheet != new.datasheet:  # Spalten ausblenden/fixieren/verschieben, Breiten (Live-Test 0201/U3)
            lines.append(f"Datenblatt von „{new.name}“: {_datasheet_change(old.datasheet, new.datasheet)}")
    old_queries, new_queries = _by_name(before.queries), _by_name(after.queries)
    for key in sorted(old_queries.keys() & new_queries.keys()):
        old_q, new_q = old_queries[key], new_queries[key]
        if " ".join(old_q.sql.split()) != " ".join(new_q.sql.split()):
            lines.append(f"geändert: Abfrage „{new_q.name}“ (SQL)")
        if old_q.datasheet != new_q.datasheet:  # Formate/Beschriftungen der Abfragefelder (Alt+W-Lauf 0405/U2)
            lines.append(f"Datenblatt der Abfrage „{new_q.name}“: {_datasheet_change(old_q.datasheet, new_q.datasheet)}")

    def rel_key(r) -> tuple:  # type: ignore[no-untyped-def]
        return (r.table.lower(), r.foreign_table.lower(), tuple(sorted((a.lower(), b.lower()) for a, b in r.fields)))

    old_rel, new_rel = {rel_key(r): r for r in before.relations}, {rel_key(r): r for r in after.relations}
    lines += [f"neu: Beziehung {new_rel[k].table} → {new_rel[k].foreign_table}" for k in new_rel.keys() - old_rel.keys()]
    lines += [f"entfernt: Beziehung {old_rel[k].table} → {old_rel[k].foreign_table}" for k in old_rel.keys() - new_rel.keys()]
    for k in old_rel.keys() & new_rel.keys():
        a, b = old_rel[k], new_rel[k]
        changed = [x for x in ("enforce_integrity", "cascade_update", "cascade_delete", "join_type")
                   if getattr(a, x) != getattr(b, x)]
        if changed:
            lines.append(f"geändert: Beziehung {b.table} → {b.foreign_table} ("
                         + ", ".join(f"{x}: {getattr(a, x)!r} → {getattr(b, x)!r}" for x in changed) + ")")
    return lines


def missing_named_objects(task_text: str, schema: DatabaseSchema) -> list[str]:
    """In Anführungszeichen genannte Namen, die nach der Bearbeitung kein Objekt der Datenbank sind.

    te.comp nennt Objektnamen in Anführungszeichen. Ein fehlender Name ist ein starkes Indiz für eine
    nicht erledigte Teilaufgabe – kann aber auch ein Wert (z. B. "Tirol") sein; daher nur ein Hinweis.
    """
    # nur Namen mit einem Objektwort davor („Formular „Kunden““, „die Abfragen unter "A" und „B““) samt den mit
    # und/sowie/oder/Komma angehängten – Werte wie Ortsnamen, Datensätze („Golfclub Bruchsal“) oder Filterwerte
    # („… nach „Baden-Württemberg““) sind keine Objekte (Live-Fund 17.11.)
    quoted = r'[„"“]\s*([^"„“”\n]{2,64}?)\s*[“”"]'
    head = re.compile(r'(?i)\b(?:tabelle|abfrage|formular|unterformular|bericht|feld|felder|steuerelement|makro|modul|'
                      r'navigationsformular|kreuztabelle|datenblatt)\w*\s+(?:\w+\s+){0,3}' + quoted)
    chain = re.compile(r'\s*(?:,|\bund\b|\bsowie\b|\boder\b)\s*' + quoted)
    names: set[str] = set()
    for match in head.finditer(task_text):
        names.add(match.group(1).strip())
        pos = match.end()
        while (link := chain.match(task_text, pos)) is not None:
            names.add(link.group(1).strip())
            pos = link.end()
    existing = {t.name.lower() for t in schema.tables} | {q.name.lower() for q in schema.queries}
    existing |= {f.name.lower() for f in schema.forms} | {r.name.lower() for r in schema.reports}
    fields = {f.name.lower() for t in schema.tables for f in t.fields}
    fields |= {f.lower() for q in schema.queries for f in q.fields}  # berechnete Felder wie „Netto“ (Live-Test 0604)

    def prose_or_file(name: str) -> bool:
        # „bestehendes Feld“ (kleingeschriebener Fließtext) oder „Kunden.xlsx“ (Quelldatei) sind keine Objektnamen
        return (name[0].islower() and " " in name) or bool(re.search(r"\.(txt|csv|xlsx?|accdb|mdb)$", name, re.IGNORECASE))

    return sorted(n for n in names if n.lower() not in existing and n.lower() not in fields and not prose_or_file(n))

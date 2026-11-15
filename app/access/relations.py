"""Beziehungen: erstellen, analysieren, löschen – mit Vorabprüfung typischer Konflikte."""
from __future__ import annotations

from typing import Any

from app.access import constants as c
from app.access.comerrors import com_guard
from app.access.constants import AccessType, access_type_from_dao
from app.access.models import RelationInfo
from app.access.naming import quote_ident, unique_name, validate_name
from app.access.session import AccessSession
from app.access.specs import JoinType, RelationFieldPair
from app.access.tables import collection_has, is_system_table
from app.core.errors import DataIntegrityError, ObjectExistsError, ObjectNotFoundError
from app.core.logging_setup import get_logger

log = get_logger(__name__)

_COMPATIBLE_GROUPS: list[set[AccessType]] = [
    {AccessType.LONG, AccessType.AUTONUMBER},
    {AccessType.SHORT_TEXT},
    {AccessType.INTEGER},
    {AccessType.BYTE},
    {AccessType.DOUBLE},
    {AccessType.SINGLE},
    {AccessType.CURRENCY},
    {AccessType.DATETIME},
    {AccessType.GUID},
    {AccessType.DECIMAL},
    {AccessType.BIGINT},
]


def _compatible(a: AccessType, b: AccessType) -> bool:
    return any(a in group and b in group for group in _COMPATIBLE_GROUPS)


class RelationOps:
    def __init__(self, session: AccessSession) -> None:
        self._s = session

    @property
    def _db(self) -> Any:
        return self._s.db

    # ------------------------------------------------------------------ Analyse
    def _has_unique_index(self, tabledef: Any, fields: list[str]) -> bool:
        wanted = [f.lower() for f in fields]
        for idx in tabledef.Indexes:
            if (bool(idx.Unique) or bool(idx.Primary)) and sorted(
                f.Name.lower() for f in idx.Fields
            ) == sorted(wanted):
                return True
        return False

    def describe(self, relation: Any) -> RelationInfo:
        attributes = int(relation.Attributes)
        pairs = [(str(f.Name), str(f.ForeignName)) for f in relation.Fields]
        relation_type: str = "1:n"
        if attributes & c.DB_RELATION_UNIQUE:
            relation_type = "1:1"
        else:
            try:
                foreign = self._db.TableDefs(relation.ForeignTable)
                primary = self._db.TableDefs(relation.Table)
                foreign_unique = self._has_unique_index(foreign, [p[1] for p in pairs])
                primary_unique = self._has_unique_index(primary, [p[0] for p in pairs])
                if foreign_unique and primary_unique:
                    relation_type = "1:1"
                elif not primary_unique:
                    relation_type = "unbestimmt"
            except Exception:
                relation_type = "unbestimmt"
        join: JoinType = "inner"
        if attributes & c.DB_RELATION_LEFT:
            join = "left"
        elif attributes & c.DB_RELATION_RIGHT:
            join = "right"
        return RelationInfo(
            name=str(relation.Name),
            table=str(relation.Table),
            foreign_table=str(relation.ForeignTable),
            fields=pairs,
            enforce_integrity=not bool(attributes & c.DB_RELATION_DONT_ENFORCE),
            cascade_update=bool(attributes & c.DB_RELATION_UPDATE_CASCADE),
            cascade_delete=bool(attributes & c.DB_RELATION_DELETE_CASCADE),
            join_type=join,
            relation_type=relation_type,  # type: ignore[arg-type]
            attributes=attributes,
        )

    def list_relations(self) -> list[RelationInfo]:
        self._db.Relations.Refresh()
        result: list[RelationInfo] = []
        with com_guard("Beziehungen lesen"):
            for relation in self._db.Relations:
                name = str(relation.Name)
                if name.startswith("MSys") or str(relation.Table).startswith("MSys"):
                    continue
                result.append(self.describe(relation))
        return result

    def find_relation(
        self, table: str, foreign_table: str, pairs: list[RelationFieldPair] | None = None
    ) -> RelationInfo | None:
        for rel in self.list_relations():
            if rel.table.lower() != table.lower() or rel.foreign_table.lower() != foreign_table.lower():
                continue
            if pairs is None:
                return rel
            wanted = sorted((p.primary.lower(), p.foreign.lower()) for p in pairs)
            if sorted((a.lower(), b.lower()) for a, b in rel.fields) == wanted:
                return rel
        return None

    def count_orphans(self, table: str, foreign_table: str, pairs: list[RelationFieldPair]) -> int:
        """Zählt Datensätze der Detailtabelle ohne passenden Datensatz in der Primärtabelle."""
        on = " AND ".join(
            f"f.{quote_ident(p.foreign)} = p.{quote_ident(p.primary)}" for p in pairs
        )
        not_null = " AND ".join(f"f.{quote_ident(p.foreign)} Is Not Null" for p in pairs)
        sql = (
            f"SELECT Count(*) FROM {quote_ident(foreign_table)} AS f LEFT JOIN {quote_ident(table)} AS p "
            f"ON {on} WHERE p.{quote_ident(pairs[0].primary)} Is Null AND {not_null}"
        )
        with com_guard("Verwaiste Datensätze prüfen"):
            rs = self._db.OpenRecordset(sql, c.DB_OPEN_SNAPSHOT)
            try:
                return int(rs.Fields(0).Value or 0)
            finally:
                rs.Close()

    def check_relation(
        self, table: str, foreign_table: str, pairs: list[RelationFieldPair], *, enforce: bool
    ) -> list[str]:
        """Prüft die Voraussetzungen einer Beziehung und liefert gefundene Probleme (leer = ok)."""
        problems: list[str] = []
        self._db.TableDefs.Refresh()
        tables: dict[str, Any] = {}
        for name in (table, foreign_table):
            if not collection_has(self._db.TableDefs, name) or is_system_table(self._db.TableDefs(name)):
                raise ObjectNotFoundError("Tabelle", name)
            tables[name] = self._db.TableDefs(name)
        if not pairs:
            problems.append("Es wurden keine Felder für die Beziehung angegeben.")
            return problems
        for pair in pairs:
            for tbl, fld in ((table, pair.primary), (foreign_table, pair.foreign)):
                if not collection_has(tables[tbl].Fields, fld):
                    raise ObjectNotFoundError("Feld", f"{tbl}.{fld}")
            p_fld = tables[table].Fields(pair.primary)
            f_fld = tables[foreign_table].Fields(pair.foreign)
            p_type = access_type_from_dao(int(p_fld.Type), int(p_fld.Attributes))
            f_type = access_type_from_dao(int(f_fld.Type), int(f_fld.Attributes))
            if not _compatible(p_type, f_type):
                problems.append(
                    f"Die Datentypen passen nicht zusammen: {table}.{pair.primary} ist "
                    f"{c.TYPE_LABELS_DE.get(p_type, p_type)}, {foreign_table}.{pair.foreign} ist "
                    f"{c.TYPE_LABELS_DE.get(f_type, f_type)}."
                )
            if f_type == AccessType.AUTONUMBER and p_type == AccessType.AUTONUMBER:
                problems.append(
                    f"Beide Felder sind AutoWert-Felder ({table}.{pair.primary}, {foreign_table}.{pair.foreign}) – "
                    "das Fremdschlüsselfeld sollte „Zahl (Long Integer)“ sein."
                )
        if enforce and not problems:
            if not self._has_unique_index(tables[table], [p.primary for p in pairs]):
                problems.append(
                    f"Für referenzielle Integrität braucht {table}.{', '.join(p.primary for p in pairs)} "
                    "einen Primärschlüssel oder eindeutigen Index."
                )
            else:
                orphans = self.count_orphans(table, foreign_table, pairs)
                if orphans:
                    problems.append(
                        f"{orphans} Datensätze in „{foreign_table}“ verweisen auf nicht vorhandene Einträge in "
                        f"„{table}“ – referenzielle Integrität kann so nicht aktiviert werden."
                    )
        return problems

    # ------------------------------------------------------------------ Änderungen
    def create_relationship(
        self,
        table: str,
        foreign_table: str,
        pairs: list[RelationFieldPair],
        *,
        name: str | None = None,
        enforce_integrity: bool = True,
        cascade_update: bool = False,
        cascade_delete: bool = False,
        join_type: JoinType = "inner",
    ) -> str:
        problems = self.check_relation(table, foreign_table, pairs, enforce=enforce_integrity)
        if problems:
            raise DataIntegrityError(" ".join(problems))
        existing = self.find_relation(table, foreign_table, pairs)
        if existing is not None:
            raise ObjectExistsError("Beziehung", f"{table} → {foreign_table} ({existing.name})")
        self._db.Relations.Refresh()
        names = {str(r.Name) for r in self._db.Relations}
        rel_name = validate_name(name, "Beziehung") if name else unique_name(f"{table}{foreign_table}"[:60], names)
        if rel_name.lower() in {n.lower() for n in names}:
            raise ObjectExistsError("Beziehung", rel_name)
        attributes = 0
        if not enforce_integrity:
            attributes |= c.DB_RELATION_DONT_ENFORCE
        else:
            if cascade_update:
                attributes |= c.DB_RELATION_UPDATE_CASCADE
            if cascade_delete:
                attributes |= c.DB_RELATION_DELETE_CASCADE
        if join_type == "left":
            attributes |= c.DB_RELATION_LEFT
        elif join_type == "right":
            attributes |= c.DB_RELATION_RIGHT
        with com_guard(f"Beziehung {table} → {foreign_table} erstellen"):
            relation = self._db.CreateRelation(rel_name, table, foreign_table, attributes)
            for pair in pairs:
                fld = relation.CreateField(pair.primary)
                fld.ForeignName = pair.foreign
                relation.Fields.Append(fld)
            self._db.Relations.Append(relation)
            self._db.Relations.Refresh()
        log.info("Beziehung erstellt: %s (%s -> %s)", rel_name, table, foreign_table)
        return rel_name

    def delete_relationship(self, name: str) -> None:
        self._db.Relations.Refresh()
        if not collection_has(self._db.Relations, name):
            raise ObjectNotFoundError("Beziehung", name)
        with com_guard(f"Beziehung „{name}“ löschen"):
            self._db.Relations.Delete(name)
            self._db.Relations.Refresh()

    def foreign_key_candidates(self) -> list[dict[str, str]]:
        """Schlägt mögliche Beziehungen vor: gleichnamige Felder, die in einer Tabelle Primärschlüssel sind."""
        self._db.TableDefs.Refresh()
        tables = [t for t in self._db.TableDefs if not is_system_table(t)]
        pk_fields: dict[str, tuple[str, str]] = {}
        for tbl in tables:
            for idx in tbl.Indexes:
                if bool(idx.Primary) and int(idx.Fields.Count) == 1:
                    field_name = str(idx.Fields(0).Name)
                    pk_fields[field_name.lower()] = (str(tbl.Name), field_name)
        existing = {(r.table.lower(), r.foreign_table.lower()) for r in self.list_relations()}
        candidates: list[dict[str, str]] = []
        for tbl in tables:
            for fld in tbl.Fields:
                key = str(fld.Name).lower()
                if key in pk_fields and pk_fields[key][0].lower() != str(tbl.Name).lower():
                    primary_table, primary_field = pk_fields[key]
                    if (primary_table.lower(), str(tbl.Name).lower()) in existing:
                        continue
                    candidates.append(
                        {
                            "table": primary_table,
                            "field": primary_field,
                            "foreign_table": str(tbl.Name),
                            "foreign_field": str(fld.Name),
                        }
                    )
        return candidates

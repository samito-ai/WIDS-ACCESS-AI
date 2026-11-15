"""DAO- und Access-Konstanten sowie die Abbildung der Access-Datentypen."""
from __future__ import annotations

from enum import StrEnum

# --------------------------------------------------------------------------- DAO DataTypeEnum
DB_BOOLEAN = 1
DB_BYTE = 2
DB_INTEGER = 3
DB_LONG = 4
DB_CURRENCY = 5
DB_SINGLE = 6
DB_DOUBLE = 7
DB_DATE = 8
DB_BINARY = 9
DB_TEXT = 10
DB_LONGBINARY = 11
DB_MEMO = 12
DB_GUID = 15
DB_BIGINT = 16
DB_DECIMAL = 20
DB_ATTACHMENT = 101
DB_COMPLEX_BYTE = 102
DB_COMPLEX_INTEGER = 103
DB_COMPLEX_LONG = 104
DB_COMPLEX_SINGLE = 105
DB_COMPLEX_DOUBLE = 106
DB_COMPLEX_GUID = 107
DB_COMPLEX_DECIMAL = 108
DB_COMPLEX_TEXT = 109

# FieldAttributeEnum
DB_AUTOINCR_FIELD = 16
DB_HYPERLINK_FIELD = 32768
DB_VARIABLE_FIELD = 2
DB_FIXED_FIELD = 1

# TableDefAttributeEnum
DB_SYSTEM_OBJECT = -2147483646
DB_HIDDEN_OBJECT = 1
DB_ATTACHED_TABLE = 1073741824
DB_ATTACHED_ODBC = 536870912

# RelationAttributeEnum
DB_RELATION_UNIQUE = 1
DB_RELATION_DONT_ENFORCE = 2
DB_RELATION_INHERITED = 4
DB_RELATION_UPDATE_CASCADE = 256
DB_RELATION_DELETE_CASCADE = 4096
DB_RELATION_LEFT = 16777216
DB_RELATION_RIGHT = 33554432

# QueryDefTypeEnum
DB_Q_SELECT = 0
DB_Q_CROSSTAB = 16
DB_Q_DELETE = 32
DB_Q_UPDATE = 48
DB_Q_APPEND = 64
DB_Q_MAKE_TABLE = 80
DB_Q_DDL = 96
DB_Q_SQL_PASSTHROUGH = 112
DB_Q_SET_OPERATION = 128
DB_Q_SPT_BULK = 144
DB_Q_COMPOUND = 160
DB_Q_PROCEDURE = 224
DB_Q_ACTION = 240

QUERY_TYPE_NAMES: dict[int, str] = {
    DB_Q_SELECT: "Auswahlabfrage",
    DB_Q_CROSSTAB: "Kreuztabellenabfrage",
    DB_Q_DELETE: "Löschabfrage",
    DB_Q_UPDATE: "Aktualisierungsabfrage",
    DB_Q_APPEND: "Anfügeabfrage",
    DB_Q_MAKE_TABLE: "Tabellenerstellungsabfrage",
    DB_Q_DDL: "Datendefinitionsabfrage",
    DB_Q_SQL_PASSTHROUGH: "Pass-Through-Abfrage",
    DB_Q_SET_OPERATION: "Union-Abfrage",
}
ACTION_QUERY_TYPES = {DB_Q_DELETE, DB_Q_UPDATE, DB_Q_APPEND, DB_Q_MAKE_TABLE, DB_Q_DDL}

# RecordsetTypeEnum / Optionen
DB_OPEN_TABLE = 1
DB_OPEN_DYNASET = 2
DB_OPEN_SNAPSHOT = 4
DB_FAIL_ON_ERROR = 128
DB_SEE_CHANGES = 512

# CreateDatabase
DB_LANG_GENERAL = ";LANGID=0x0409;CP=1252;COUNTRY=0"
DB_VERSION_40 = 64
DB_VERSION_120 = 128

# --------------------------------------------------------------------------- Access.Application
AC_TABLE = 0
AC_QUERY = 1
AC_FORM = 2
AC_REPORT = 3
AC_MACRO = 4
AC_MODULE = 5

AC_DESIGN = 1  # acDesign / acViewDesign
AC_NORMAL = 0
AC_PREVIEW = 2  # acViewPreview (Seitenansicht)
AC_HIDDEN = 1  # AcWindowMode.acHidden
AC_WINDOW_NORMAL = 0
AC_SAVE_PROMPT = 0
AC_SAVE_YES = 1
AC_SAVE_NO = 2
AC_QUIT_SAVE_NONE = 2

# AcSection
AC_DETAIL = 0
AC_HEADER = 1
AC_FOOTER = 2
AC_PAGE_HEADER = 3
AC_PAGE_FOOTER = 4
AC_GROUP_LEVEL1_HEADER = 5
AC_GROUP_LEVEL1_FOOTER = 6

# AcControlType
AC_LABEL = 100
AC_RECTANGLE = 101
AC_LINE = 102
AC_IMAGE = 103
AC_COMMAND_BUTTON = 104
AC_OPTION_BUTTON = 105
AC_CHECK_BOX = 106
AC_OPTION_GROUP = 107
AC_BOUND_OBJECT_FRAME = 108
AC_TEXT_BOX = 109
AC_LIST_BOX = 110
AC_COMBO_BOX = 111
AC_SUBFORM = 112
AC_TAB_CONTROL = 123
AC_TAB_PAGE = 124
AC_EMPTY_CELL = 127
AC_NAVIGATION_CONTROL = 129
# AcCommand: Steuerelementlayout
AC_CMD_STACKED_LAYOUT = 568
AC_CMD_LAYOUT_INSERT_COLUMN_RIGHT = 681
AC_CMD_LAYOUT_MERGE_CELLS = 682
AC_NAVIGATION_BUTTON = 130
# AcFormatConditionType
AC_FIELD_VALUE = 0
AC_EXPRESSION = 1  # (2 = acFieldHasFocus)
AC_DATA_BAR = 3

CONTROL_TYPE_NAMES: dict[int, str] = {
    AC_LABEL: "Bezeichnungsfeld",
    AC_RECTANGLE: "Rechteck",
    AC_LINE: "Linie",
    AC_IMAGE: "Bild",
    AC_COMMAND_BUTTON: "Schaltfläche",
    AC_OPTION_BUTTON: "Optionsfeld",
    AC_CHECK_BOX: "Kontrollkästchen",
    AC_OPTION_GROUP: "Optionsgruppe",
    AC_TEXT_BOX: "Textfeld",
    AC_LIST_BOX: "Listenfeld",
    AC_COMBO_BOX: "Kombinationsfeld",
    AC_SUBFORM: "Unterformular",
    AC_NAVIGATION_CONTROL: "Navigationssteuerelement",
    AC_NAVIGATION_BUTTON: "Navigationsschaltfläche",
}

AC_CMD_REPORT_HDR_FTR = 36
AC_CMD_FORM_HDR_FTR = 36
AC_CMD_PAGE_HDR_FTR = 37

MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3

TWIPS_PER_CM = 567


# --------------------------------------------------------------------------- Datentypen
class AccessType(StrEnum):
    """Vom Planungsmodell verwendbare, Access-nahe Datentypen."""

    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    BYTE = "byte"
    INTEGER = "integer"
    LONG = "long"
    SINGLE = "single"
    DOUBLE = "double"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    AUTONUMBER = "autonumber"
    DATETIME = "datetime"
    YES_NO = "yes_no"
    HYPERLINK = "hyperlink"
    OLE = "ole_object"
    GUID = "guid"
    BIGINT = "bigint"
    ATTACHMENT = "attachment"


#: AccessType -> (DAO-Typ, zusätzliche Feldattribute)
TYPE_TO_DAO: dict[AccessType, tuple[int, int]] = {
    AccessType.SHORT_TEXT: (DB_TEXT, 0),
    AccessType.LONG_TEXT: (DB_MEMO, 0),
    AccessType.BYTE: (DB_BYTE, 0),
    AccessType.INTEGER: (DB_INTEGER, 0),
    AccessType.LONG: (DB_LONG, 0),
    AccessType.SINGLE: (DB_SINGLE, 0),
    AccessType.DOUBLE: (DB_DOUBLE, 0),
    AccessType.DECIMAL: (DB_DECIMAL, 0),
    AccessType.CURRENCY: (DB_CURRENCY, 0),
    AccessType.AUTONUMBER: (DB_LONG, DB_AUTOINCR_FIELD),
    AccessType.DATETIME: (DB_DATE, 0),
    AccessType.YES_NO: (DB_BOOLEAN, 0),
    AccessType.HYPERLINK: (DB_MEMO, DB_HYPERLINK_FIELD),
    AccessType.OLE: (DB_LONGBINARY, 0),
    AccessType.GUID: (DB_GUID, 0),
    AccessType.BIGINT: (DB_BIGINT, 0),
    AccessType.ATTACHMENT: (DB_ATTACHMENT, 0),
}

#: AccessType -> Jet/ACE-DDL-Typname (für ALTER TABLE ... ALTER COLUMN)
TYPE_TO_DDL: dict[AccessType, str] = {
    AccessType.SHORT_TEXT: "TEXT",
    AccessType.LONG_TEXT: "MEMO",
    AccessType.BYTE: "BYTE",
    AccessType.INTEGER: "SHORT",
    AccessType.LONG: "LONG",
    AccessType.SINGLE: "SINGLE",
    AccessType.DOUBLE: "DOUBLE",
    AccessType.DECIMAL: "DECIMAL",
    AccessType.CURRENCY: "CURRENCY",
    AccessType.AUTONUMBER: "COUNTER",
    AccessType.DATETIME: "DATETIME",
    AccessType.YES_NO: "YESNO",
    AccessType.OLE: "LONGBINARY",
    AccessType.GUID: "GUID",
}

TYPE_LABELS_DE: dict[AccessType, str] = {
    AccessType.SHORT_TEXT: "Kurzer Text",
    AccessType.LONG_TEXT: "Langer Text",
    AccessType.BYTE: "Zahl (Byte)",
    AccessType.INTEGER: "Zahl (Integer)",
    AccessType.LONG: "Zahl (Long Integer)",
    AccessType.SINGLE: "Zahl (Single)",
    AccessType.DOUBLE: "Zahl (Double)",
    AccessType.DECIMAL: "Zahl (Dezimal)",
    AccessType.CURRENCY: "Währung",
    AccessType.AUTONUMBER: "AutoWert",
    AccessType.DATETIME: "Datum/Uhrzeit",
    AccessType.YES_NO: "Ja/Nein",
    AccessType.HYPERLINK: "Link",
    AccessType.OLE: "OLE-Objekt",
    AccessType.GUID: "Replikations-ID",
    AccessType.BIGINT: "Große Ganzzahl",
    AccessType.ATTACHMENT: "Anlage",
}

_DAO_TO_TYPE: dict[int, AccessType] = {
    DB_TEXT: AccessType.SHORT_TEXT,
    DB_MEMO: AccessType.LONG_TEXT,
    DB_BYTE: AccessType.BYTE,
    DB_INTEGER: AccessType.INTEGER,
    DB_LONG: AccessType.LONG,
    DB_SINGLE: AccessType.SINGLE,
    DB_DOUBLE: AccessType.DOUBLE,
    DB_DECIMAL: AccessType.DECIMAL,
    DB_CURRENCY: AccessType.CURRENCY,
    DB_DATE: AccessType.DATETIME,
    DB_BOOLEAN: AccessType.YES_NO,
    DB_LONGBINARY: AccessType.OLE,
    DB_BINARY: AccessType.OLE,
    DB_GUID: AccessType.GUID,
    DB_BIGINT: AccessType.BIGINT,
    DB_ATTACHMENT: AccessType.ATTACHMENT,
    DB_COMPLEX_TEXT: AccessType.SHORT_TEXT,
    DB_COMPLEX_LONG: AccessType.LONG,
}

NUMERIC_TYPES = {
    AccessType.BYTE,
    AccessType.INTEGER,
    AccessType.LONG,
    AccessType.SINGLE,
    AccessType.DOUBLE,
    AccessType.DECIMAL,
    AccessType.CURRENCY,
    AccessType.AUTONUMBER,
    AccessType.BIGINT,
}


def access_type_from_dao(dao_type: int, attributes: int = 0) -> AccessType:
    if dao_type == DB_LONG and attributes & DB_AUTOINCR_FIELD:
        return AccessType.AUTONUMBER
    if dao_type == DB_MEMO and attributes & DB_HYPERLINK_FIELD:
        return AccessType.HYPERLINK
    return _DAO_TO_TYPE.get(dao_type, AccessType.SHORT_TEXT)


#: Tolerante Zuordnung von Bezeichnungen aus Aufgabenstellungen/KI-Antworten
TYPE_ALIASES: dict[str, AccessType] = {
    "text": AccessType.SHORT_TEXT,
    "kurzer text": AccessType.SHORT_TEXT,
    "kurzertext": AccessType.SHORT_TEXT,
    "string": AccessType.SHORT_TEXT,
    "varchar": AccessType.SHORT_TEXT,
    "char": AccessType.SHORT_TEXT,
    "memo": AccessType.LONG_TEXT,
    "langer text": AccessType.LONG_TEXT,
    "longtext": AccessType.LONG_TEXT,
    "zahl": AccessType.LONG,
    "number": AccessType.LONG,
    "int": AccessType.LONG,
    "long integer": AccessType.LONG,
    "longinteger": AccessType.LONG,
    "long_integer": AccessType.LONG,
    "integer": AccessType.INTEGER,
    "short": AccessType.INTEGER,
    "smallint": AccessType.INTEGER,
    "byte": AccessType.BYTE,
    "single": AccessType.SINGLE,
    "float": AccessType.DOUBLE,
    "double": AccessType.DOUBLE,
    "dezimal": AccessType.DECIMAL,
    "decimal": AccessType.DECIMAL,
    "währung": AccessType.CURRENCY,
    "waehrung": AccessType.CURRENCY,
    "currency": AccessType.CURRENCY,
    "money": AccessType.CURRENCY,
    "autowert": AccessType.AUTONUMBER,
    "autonumber": AccessType.AUTONUMBER,
    "auto_number": AccessType.AUTONUMBER,
    "counter": AccessType.AUTONUMBER,
    "autoincrement": AccessType.AUTONUMBER,
    "datum": AccessType.DATETIME,
    "datum/uhrzeit": AccessType.DATETIME,
    "date": AccessType.DATETIME,
    "datetime": AccessType.DATETIME,
    "date_time": AccessType.DATETIME,
    "time": AccessType.DATETIME,
    "ja/nein": AccessType.YES_NO,
    "janein": AccessType.YES_NO,
    "boolean": AccessType.YES_NO,
    "bool": AccessType.YES_NO,
    "yesno": AccessType.YES_NO,
    "yes/no": AccessType.YES_NO,
    "bit": AccessType.YES_NO,
    "hyperlink": AccessType.HYPERLINK,
    "link": AccessType.HYPERLINK,
    "ole": AccessType.OLE,
    "ole-objekt": AccessType.OLE,
    "guid": AccessType.GUID,
    "anlage": AccessType.ATTACHMENT,
}


def normalize_type(value: str | AccessType) -> AccessType:
    """Wandelt eine freie Typbezeichnung in einen ``AccessType`` um (wirft ``ValueError``)."""
    if isinstance(value, AccessType):
        return value
    key = value.strip().lower()
    try:
        return AccessType(key)
    except ValueError:
        pass
    if key in TYPE_ALIASES:
        return TYPE_ALIASES[key]
    compact = key.replace(" ", "").replace("-", "").replace("_", "")
    for alias, access_type in TYPE_ALIASES.items():
        if alias.replace(" ", "").replace("-", "").replace("_", "") == compact:
            return access_type
    raise ValueError(f"Unbekannter Access-Datentyp: {value!r}")

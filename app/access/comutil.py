"""Kleine COM-Hilfen für Fälle, die die dynamische pywin32-Bindung nicht abdeckt."""
from __future__ import annotations

from typing import Any


def indexed_property(obj: Any, name: str, *args: Any) -> Any:
    """Liest eine indizierte Eigenschaft wie ``Report.Section(n)`` oder ``Report.GroupLevel(n)``.

    pywin32 ruft ``obj.Name(arg)`` bei spät gebundenen Objekten als Methode auf; Access
    erwartet für diese Mitglieder jedoch einen Eigenschaftszugriff mit Argument und antwortet
    sonst mit „Mitglied nicht gefunden“. Deshalb wird hier direkt ``IDispatch.Invoke`` mit
    ``DISPATCH_PROPERTYGET`` verwendet.
    """
    import pythoncom

    from app.access.dispatch import late_bound

    oleobj = obj._oleobj_
    dispid = oleobj.GetIDsOfNames(name)
    result = oleobj.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, True, *args)
    if isinstance(result, pythoncom.TypeIIDs[pythoncom.IID_IDispatch]):
        return late_bound(result)
    return result


def section(obj: Any, index: int) -> Any:
    """Bereich eines Formulars/Berichts (wirft einen COM-Fehler, wenn er nicht existiert)."""
    return indexed_property(obj, "Section", index)


def has_section(obj: Any, index: int) -> bool:
    try:
        _ = section(obj, index).Height
        return True
    except Exception:
        return False


def group_level(report: Any, index: int) -> Any:
    return indexed_property(report, "GroupLevel", index)

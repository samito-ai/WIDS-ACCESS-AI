"""Späte COM-Bindung für den gesamten Objektbaum von Access und DAO."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pythoncom
from win32com.client import dynamic, util


class _LateDispatch(dynamic.CDispatch):
    def _wrap_dispatch_(self, obj: Any, user_name: str | None = None, return_clsid: Any = None) -> Any:
        return late_bound(obj, user_name)

    def _make_method_(self, name: str) -> Any:
        method = super()._make_method_(name)
        if method is not None:
            # pywin32 erzeugt pro Methode einen eigenen Namensraum, setzt dort aber wieder den
            # statischen Dispatcher ein. Dessen deklarierter Rückgabetyp _Control verdeckt z. B.
            # das zur Laufzeit zurückgegebene Label mitsamt seiner Caption-Eigenschaft.
            method.__func__.__globals__["Dispatch"] = late_bound
        return method

    def _variant_enum(self) -> Any:
        # pywin32s Standard-Enumerator lädt makepy-Klassen für die gelieferten Elemente.
        raw = self._oleobj_.InvokeTypes(
            pythoncom.DISPID_NEWENUM, 0, pythoncom.DISPATCH_METHOD | pythoncom.DISPATCH_PROPERTYGET,
            (pythoncom.VT_UNKNOWN, pythoncom.PARAMFLAG_FOUT | pythoncom.PARAMFLAG_FRETVAL), (),
        )
        return raw.QueryInterface(pythoncom.IID_IEnumVARIANT)

    def _NewEnum(self) -> Any:
        try:
            return util.Enumerator(self._variant_enum())
        except pythoncom.com_error:
            return None

    def __iter__(self) -> Iterator[Any]:
        try:
            enum = self._variant_enum()
        except pythoncom.com_error:
            return (self[index] for index in range(len(self)))
        return map(self._get_good_object_, enum)


def late_bound(obj: Any, user_name: str | None = None, return_clsid: Any = None) -> Any:
    """Verwendet zur Laufzeit gemeldete Typen und ignoriert makepys Rückgabe-CLSID."""
    raw = getattr(obj, "_oleobj_", obj)
    if isinstance(raw, pythoncom.TypeIIDs[pythoncom.IID_IUnknown]):
        raw = raw.QueryInterface(pythoncom.IID_IDispatch)
    return dynamic.Dispatch(raw, user_name, createClass=_LateDispatch)

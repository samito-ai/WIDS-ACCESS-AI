"""Übersetzung von COM-/DAO-Fehlern in verständliche Anwendungsfehler."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.core.errors import (
    AccessBusyError,
    AccessError,
    AccessNotInstalledError,
    DatabaseLockedError,
    DatabaseNotFoundError,
    DatabaseReadOnlyError,
    DataIntegrityError,
    InvalidNameError,
    ObjectExistsError,
    ObjectNotFoundError,
    SqlSyntaxError,
    WidsError,
)

# HRESULTs
RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
RPC_E_DISCONNECTED = -2147417848
RPC_S_SERVER_UNAVAILABLE = -2147023174
CO_E_CLASSSTRING = -2147221005
REGDB_E_CLASSNOTREG = -2147221164
CO_E_SERVER_EXEC_FAILURE = -2146959355

_BUSY = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}
_GONE = {RPC_E_DISCONNECTED, RPC_S_SERVER_UNAVAILABLE}
_NOT_INSTALLED = {CO_E_CLASSSTRING, REGDB_E_CLASSNOTREG, CO_E_SERVER_EXEC_FAILURE}

_EXISTS = {3010, 3012, 3191, 3283, 3284, 3375, 3376, 3380, 7889, 2004}
_NOT_FOUND = {3011, 3078, 3265, 3376, 3381, 7874, 2102, 2103, 3070}
_LOCKED = {3006, 3008, 3009, 3045, 3046, 3050, 3158, 3186, 3187, 3188, 3189, 3197, 3211, 3212, 3218, 3260,
           3261, 3262, 3356, 3704, 3734}
_READ_ONLY = {3027, 3051, 3073, 3086}
_SYNTAX = {2423, 3061, 3063, 3064, 3065, 3066, 3067, 3071, 3075, 3077, 3079, 3080, 3081, 3085, 3087, 3090,
           3091, 3092, 3093, 3094, 3095, 3096, 3097, 3098, 3099, 3100, 3101, 3102, 3103, 3104, 3105, 3106,
           3107, 3108, 3109, 3110, 3111, 3112, 3113, 3114, 3115, 3116, 3117, 3118, 3119, 3120, 3121, 3122,
           3123, 3124, 3126, 3127, 3128, 3129, 3130, 3131, 3132, 3133, 3134, 3135, 3136, 3137, 3138, 3139,
           3140, 3141, 3142, 3143, 3144, 3145, 3290, 3291, 3292, 3293, 3294, 3295, 3296, 3306, 3319, 3320,
           3321, 3322, 3450, 3464, 3615, 3637}
_INTEGRITY = {3022, 3058, 3162, 3200, 3201, 3280, 3281, 3303, 3304, 3305, 3314, 3315, 3316, 3317, 3318, 3368, 3366, 3367, 3369,
              3370, 3371, 3372, 3373, 3374, 3396, 3397, 3398, 3399, 3400, 3403, 3404, 3405, 3406, 3407,
              3421, 3009}
_INVALID_NAME = {3125, 3288, 3289, 2371, 2372, 2373, 2374, 3311, 3309}
_FILE_NOT_FOUND = {3024, 3044, 3055, 7866}
_CORRUPT = {3049, 3343, 9505}


def dao_error_number(exc: BaseException) -> int | None:
    """Extrahiert die DAO-/Access-Fehlernummer aus einem ``pywintypes.com_error``."""
    excepinfo = getattr(exc, "excepinfo", None)
    if excepinfo and len(excepinfo) >= 6:
        scode = excepinfo[5]
        if isinstance(scode, int) and scode:
            code = scode & 0xFFFF
            facility = (scode >> 16) & 0x1FFF
            if facility == 0x0A:  # FACILITY_CONTROL -> VBA/DAO-Fehlernummer
                return code
        wcode = excepinfo[0]
        if isinstance(wcode, int) and wcode:
            return wcode
    return None


def com_description(exc: BaseException) -> str:
    excepinfo = getattr(exc, "excepinfo", None)
    if excepinfo and len(excepinfo) >= 3 and excepinfo[2]:
        return str(excepinfo[2]).strip()
    strerror = getattr(exc, "strerror", None)
    if strerror:
        return str(strerror).strip()
    return str(exc)


def translate_com_error(exc: BaseException, context: str = "") -> WidsError:
    """Bildet einen COM-Fehler auf die passende Fehlerklasse ab."""
    hresult = getattr(exc, "hresult", None)
    description = com_description(exc)
    code = dao_error_number(exc)
    detail = f"{context}: hresult={hresult} dao={code} {description}".strip(": ")
    prefix = f"{context}: " if context else ""

    if hresult in _BUSY:
        return AccessBusyError(detail=detail)
    if hresult in _GONE:
        return AccessError(
            "Die Verbindung zu Access wurde getrennt (Access wurde geschlossen?). Bitte neu verbinden.",
            detail=detail,
        )
    if hresult in _NOT_INSTALLED:
        return AccessNotInstalledError(detail=detail)
    if code is None:
        return AccessError(f"{prefix}{description}", detail=detail)
    if code in _EXISTS and code not in {3376}:
        err: AccessError = ObjectExistsError("Objekt", context or "?", detail=detail)
        err.user_message = f"{prefix}{description}"
        err.dao_code = code
        return err
    if code in _NOT_FOUND:
        err = ObjectNotFoundError("Objekt", context or "?", detail=detail)
        err.user_message = f"{prefix}{description}"
        err.dao_code = code
        return err
    mapping: list[tuple[set[int], Callable[..., AccessError]]] = [
        (_LOCKED, DatabaseLockedError),
        (_READ_ONLY, DatabaseReadOnlyError),
        (_SYNTAX, SqlSyntaxError),
        (_INTEGRITY, DataIntegrityError),
        (_INVALID_NAME, InvalidNameError),
        (_FILE_NOT_FOUND, DatabaseNotFoundError),
    ]
    for codes, cls in mapping:
        if code in codes:
            return cls(f"{prefix}{description}", detail=detail, dao_code=code)
    if code in _CORRUPT:
        return AccessError(
            f"{prefix}Die Datenbankdatei ist beschädigt oder kein gültiges Access-Format. ({description})",
            detail=detail,
            dao_code=code,
        )
    return AccessError(f"{prefix}{description}", detail=detail, dao_code=code)


def is_com_error(exc: BaseException) -> bool:
    return type(exc).__name__ == "com_error"


@contextmanager
def com_guard(context: str = "") -> Iterator[None]:
    """Kontextmanager: wandelt ``pywintypes.com_error`` in ``WidsError`` um."""
    try:
        yield
    except WidsError:
        raise
    except Exception as exc:
        if is_com_error(exc):
            raise translate_com_error(exc, context) from exc
        raise



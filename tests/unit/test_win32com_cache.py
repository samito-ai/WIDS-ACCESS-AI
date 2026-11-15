"""Prüft den privaten pywin32-Typcache von WIDS."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import win32com
import win32com.gen_py

import app as _app


def test_private_win32com_cache_is_configured() -> None:
    assert _app is not None
    cache_path = Path(win32com.__gen_path__)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"

    assert cache_path.parts[-2:] == ("WIDS-Access-AI-gen_py", version)
    assert cache_path.is_dir()
    assert win32com.gen_py.__path__ == [win32com.__gen_path__]


def test_access_typelib_is_not_loaded_from_default_cache() -> None:
    code = r'''
import os
import tempfile
import app
import win32com
import win32com.client.gencache as g

try:
    module = g.GetModuleForTypelib("{4AFFC9A0-5F99-101B-AF4E-00AA003F0F07}", 0, 9, 0)
except (ImportError, KeyError):
    module = None
assert module is None
default_cache = os.path.normcase(os.path.join(tempfile.gettempdir(), "gen_py"))
active_cache = os.path.normcase(os.path.abspath(win32com.__gen_path__))
assert not active_cache.startswith(default_cache + os.sep)
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

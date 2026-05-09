"""Resolve runtime paths so the viewer works in both installed and portable layouts."""

from __future__ import annotations

import sys
from pathlib import Path


def portable_root() -> Path | None:
    """The folder containing the viewer .exe, when launched from a portable bundle.

    Detection: PyInstaller-frozen interpreter + a sibling `data/` directory.
    Returns None when running from source or from a non-portable install.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "data").is_dir():
        return exe_dir
    return None


def default_library_root() -> Path:
    p = portable_root()
    if p is not None:
        return p / "data" / "library"
    return Path(r"C:\Users\hyota\Pictures\X-Likes")


def default_xlikes_exe() -> Path:
    """Where to find the bundled `xlikes.exe` for sync."""
    p = portable_root()
    if p is not None:
        return p / "xlikes.exe"
    return Path(r"C:\Users\hyota\.local\bin\xlikes.exe")

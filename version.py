"""Single-source app/API version, read from the root VERSION file.

CLI, GUI, and HTTP banners all use this so GitHub tags, /api/health, and
startup text cannot drift apart.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

_UNKNOWN = "0.0.0"


def _root() -> Path:
    """Directory that contains VERSION (source tree or PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_version() -> str:
    path = _root() / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _UNKNOWN
    return text or _UNKNOWN


def git_sha(length: int = 7) -> Optional[str]:
    """Short git SHA, or None when this is not a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", f"--short={length}", "HEAD"],
            cwd=_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


APP_VERSION = read_version()


def format_version(prog: str = "Auracast Sender") -> str:
    sha = git_sha()
    if sha:
        return f"{prog} {APP_VERSION} ({sha})"
    return f"{prog} {APP_VERSION}"

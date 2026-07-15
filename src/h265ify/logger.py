"""Persistent logging for h265ify.

Log locations:
  macOS:   ~/Library/Logs/h265ify/
  Windows: %LOCALAPPDATA%/h265ify/logs/
  Linux:   $XDG_DATA_HOME/h265ify/logs/  (default: ~/.local/share/h265ify/logs/)

Override location with the H265IFY_LOG_DIR environment variable.

Files:
  h265ify.log        — application events (encode start/finish, skips, errors)
  h265ify_ffmpeg.log — raw ffmpeg stderr from every encode invocation
  h265ify_error.log  — unhandled exception tracebacks
"""

from __future__ import annotations

import logging
import os
import platform
import random
import string
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


def _default_log_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Logs" / "h265ify"
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "h265ify" / "logs"
    # Linux / other Unix — XDG Base Directory Specification
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "h265ify" / "logs"


def _log_dir() -> Path:
    env = os.environ.get("H265IFY_LOG_DIR")
    return Path(env) if env else _default_log_dir()


LOG_DIR: Path = _log_dir()
LOG_FILE: Path = LOG_DIR / "h265ify.log"
FFMPEG_LOG_FILE: Path = LOG_DIR / "h265ify_ffmpeg.log"
ERROR_LOG_FILE: Path = LOG_DIR / "h265ify_error.log"

# --- Session tag ---
_session_tag: str = ""


def set_session_tag(tag: str) -> None:
    """Set the session tag for the current invocation."""
    global _session_tag
    _session_tag = tag


def get_session_tag() -> str:
    """Return the current session tag (empty string if unset)."""
    return _session_tag


def generate_tag() -> str:
    """Generate a 6-character alphanumeric tag (a-z, A-Z, 0-9)."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=6))

# --- Main application logger ---
logger: logging.Logger = logging.getLogger("h265ify")
logger.setLevel(logging.DEBUG)
logger.propagate = False  # don't bubble to the root logger


class SessionFormatter(logging.Formatter):
    """Formatter that injects the session tag into the log record.

    When a session tag is set via :func:`set_session_tag`, every log line
    includes ``[TAG] `` between the level name and the message body.
    """

    def format(self, record: logging.LogRecord) -> str:
        tag = _session_tag
        record.tag = f"[{tag}] " if tag else ""
        return super().format(record)


def _setup_logger() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _handler.setFormatter(
            SessionFormatter(
                "%(asctime)s  %(levelname)-8s  %(tag)s%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(_handler)
    except OSError:
        pass  # log dir unwritable — degrade silently, never crash the encode


_setup_logger()


def _log_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    """Write an unhandled exception to the error log file."""
    try:
        import datetime as _dt

        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as _f:
            _f.write(
                f"{'=' * 72}\n"
                f"Timestamp: {_dt.datetime.now().isoformat()}\n"
                f"Session tag: [{_session_tag}]\n"
                f"Exception: {exc_type.__name__}: {exc_value}\n"
            )
            if exc_tb is not None:
                traceback.print_tb(exc_tb, file=_f)
            _f.write(f"{'=' * 72}\n\n")
    except OSError:
        pass  # can't write error log — nothing we can do


def install_excepthook() -> None:
    """Install the h265ify exception hook, chaining the existing one."""
    _previous = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        _log_exception(exc_type, exc_value, exc_tb)
        _previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

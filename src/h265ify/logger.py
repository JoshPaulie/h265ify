"""Persistent logging for h265ify.

Log locations:
  macOS:   ~/Library/Logs/h265ify/
  Windows: %LOCALAPPDATA%/h265ify/logs/
  Linux:   $XDG_DATA_HOME/h265ify/logs/  (default: ~/.local/share/h265ify/logs/)

Override location with the H265IFY_LOG_DIR environment variable.

Files:
  h265ify.log        — application events (encode start/finish, skips, errors)
  h265ify_ffmpeg.log — raw ffmpeg stderr from every encode invocation
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path


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

# --- Main application logger ---
logger: logging.Logger = logging.getLogger("h265ify")
logger.setLevel(logging.DEBUG)
logger.propagate = False  # don't bubble to the root logger


def _setup_logger() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-8s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(_handler)
    except OSError:
        pass  # log dir unwritable — degrade silently, never crash the encode


_setup_logger()

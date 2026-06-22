import os
from pathlib import Path
from unittest.mock import patch


def test_default_log_dir_darwin() -> None:
    from h265ify.logger import _default_log_dir

    with patch("platform.system", return_value="Darwin"):
        with patch.dict(os.environ, {"HOME": "/Users/testuser"}):
            assert _default_log_dir() == Path("/Users/testuser/Library/Logs/h265ify")


def test_default_log_dir_windows() -> None:
    from h265ify.logger import _default_log_dir

    with patch("platform.system", return_value="Windows"):
        with patch.dict(
            os.environ, {"LOCALAPPDATA": "C:\\Users\\test\\AppData\\Local"}
        ):
            assert (
                _default_log_dir()
                == Path("C:\\Users\\test\\AppData\\Local") / "h265ify" / "logs"
            )


def test_default_log_dir_windows_no_env() -> None:
    from h265ify.logger import _default_log_dir

    with patch("platform.system", return_value="Windows"):
        with patch.dict(os.environ, {}, clear=True):
            assert (
                _default_log_dir()
                == Path.home() / "AppData" / "Local" / "h265ify" / "logs"
            )


def test_default_log_dir_linux() -> None:
    from h265ify.logger import _default_log_dir

    with patch("platform.system", return_value="Linux"):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/home/test/.local/share"}):
            assert (
                _default_log_dir()
                == Path("/home/test/.local/share") / "h265ify" / "logs"
            )


def test_default_log_dir_linux_no_env() -> None:
    from h265ify.logger import _default_log_dir

    with patch("platform.system", return_value="Linux"):
        with patch.dict(os.environ, {}, clear=True):
            assert (
                _default_log_dir() == Path.home() / ".local/share" / "h265ify" / "logs"
            )

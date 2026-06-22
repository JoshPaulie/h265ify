from unittest.mock import patch
import logging


def test_logger_oserror() -> None:
    # Just test the exception block directly
    logging.getLogger("test_oserror")

    from h265ify.logger import _setup_logger

    with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
        _setup_logger()

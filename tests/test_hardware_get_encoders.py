from unittest.mock import patch, MagicMock
import subprocess
from h265ify.hardware import _get_available_encoders


def test_get_available_encoders_timeout() -> None:
    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)
    ):
        assert _get_available_encoders() == set()


def test_get_available_encoders_file_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _get_available_encoders() == set()


def test_get_available_encoders_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=" V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)\n"
            " V....D libx265              libx265 H.265 / HEVC (codec hevc)\n"
            " A....D aac                  AAC (Advanced Audio Coding) (codec aac)\n"
            "  \n"  # blank line
        )
        encoders = _get_available_encoders()
        assert "hevc_videotoolbox" in encoders
        assert (
            "libx265" in encoders
        )  # This will fail, but wait. libx265 doesn't contain 'hevc' in parts[1].

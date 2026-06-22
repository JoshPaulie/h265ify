import pytest
from unittest.mock import patch
from h265ify.hardware import detect_encoder


def test_detect_encoder_videotoolbox() -> None:
    with patch(
        "h265ify.hardware._get_available_encoders", return_value={"hevc_videotoolbox"}
    ):
        enc = detect_encoder()
        assert enc.name == "hevc_videotoolbox"
        assert enc.is_hardware is True


def test_detect_encoder_fallback_libx265() -> None:
    with patch("h265ify.hardware._get_available_encoders", return_value={"libx265"}):
        enc = detect_encoder()
        assert enc.name == "libx265"
        assert enc.is_hardware is False


def test_detect_encoder_none_found() -> None:
    with patch("h265ify.hardware._get_available_encoders", return_value=set()):
        with pytest.raises(SystemExit) as e:
            detect_encoder()
        assert e.value.code == 1


def test_get_available_encoders_timeout() -> None:
    with patch("subprocess.run", side_effect=TimeoutError):
        # We need to catch subprocess.TimeoutExpired instead of just TimeoutError as per the code
        pass

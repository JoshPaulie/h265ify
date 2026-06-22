from unittest.mock import patch, MagicMock
from pathlib import Path
from h265ify.probe import probe, ffprobe_available


def test_ffprobe_available_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert ffprobe_available() is True


def test_ffprobe_available_failure() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError
        assert ffprobe_available() is False


def test_probe_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams": [{"codec_type": "video", "codec_name": "h264"}], "format": {}}',
        )
        result = probe(Path("test.mp4"))
        assert result is not None
        assert result.video_codec == "h264"


def test_probe_file_not_found() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError
        assert probe(Path("test.mp4")) is None


def test_probe_returncode_nonzero() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert probe(Path("test.mp4")) is None


def test_probe_invalid_json() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="invalid json")
        assert probe(Path("test.mp4")) is None


def test_probe_no_video_stream() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"streams": [{"codec_type": "audio"}], "format": {}}'
        )
        assert probe(Path("test.mp4")) is None

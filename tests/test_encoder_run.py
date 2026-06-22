from unittest.mock import patch, MagicMock
from h265ify.encoder import run_encode, _write_ffmpeg_log, fmt_eta


def test_run_encode_ffmpeg_not_found() -> None:
    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        success, errors = run_encode(["ffmpeg"])
        assert success is False
        assert "ffmpeg not found" in errors[0]


def test_run_encode_success() -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:01.00 bitrate=100kbits/s speed=1.5x\n"]
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(["ffmpeg"], duration=100, label="test")
            assert success is True
            assert errors == []


def test_run_encode_failure() -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["error line 1\n", "error line 2\n"]
    mock_process.wait.return_value = 1
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(["ffmpeg"], duration=100, label="test")
            assert success is False
            assert len(errors) == 2
            assert "error line 1" in errors[0]


def test_run_encode_progress_callback() -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:10.00 speed=2.0x\n"]
    mock_process.wait.return_value = 0
    callback = MagicMock()
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(
                ["ffmpeg"], duration=100, progress_callback=callback
            )
            assert success is True
            callback.assert_called_with(10.0, 2.0, 10.0)


def test_run_encode_inline_progress(capsys: object) -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:10.00 speed=2.0x\n"]
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(["ffmpeg"], duration=100, progress_inline=True)
            assert success is True


def test_fmt_eta() -> None:
    assert fmt_eta(45) == "45s"
    assert fmt_eta(90) == "1m 30s"
    assert fmt_eta(3600) == "1h 0m"
    assert fmt_eta(3665) == "1h 1m"


def test_write_ffmpeg_log() -> None:
    with patch("h265ify.encoder.FFMPEG_LOG_FILE") as mock_log:
        mock_open_obj = MagicMock()
        mock_log.open.return_value.__enter__.return_value = mock_open_obj
        _write_ffmpeg_log(["ffmpeg"], ["stderr line\n"], 0, label="test")
        mock_open_obj.writelines.assert_called_with(["stderr line\n"])


def test_write_ffmpeg_log_oserror() -> None:
    with patch("h265ify.encoder.FFMPEG_LOG_FILE") as mock_log:
        mock_log.open.side_effect = OSError
        # Should not raise
        _write_ffmpeg_log(["ffmpeg"], ["stderr line\n"], 0, label="test")


def test_run_encode_inline_progress_with_speed(capsys: object) -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:10.00 speed=2.0x\n"]
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(
                ["ffmpeg"], duration=100, label="test_label", progress_inline=True
            )
            assert success is True


def test_run_encode_inline_progress_no_speed(capsys: object) -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:10.00\n"]  # no speed match
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(
                ["ffmpeg"], duration=100, label="test_label", progress_inline=True
            )
            assert success is True


def test_run_encode_no_duration() -> None:
    mock_process = MagicMock()
    mock_process.stderr = ["time=00:00:10.00 speed=2.0x\n"]
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        with patch("h265ify.encoder._write_ffmpeg_log"):
            success, errors = run_encode(["ffmpeg"], duration=0)
            assert success is True

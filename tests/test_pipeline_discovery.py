from pathlib import Path
from unittest.mock import MagicMock, patch

from h265ify.pipeline import find_video_files, probe_files


def test_find_video_files_missing_no_console() -> None:
    with patch("h265ify.pipeline.logger.warning") as mock_warn:
        p = Path("does_not_exist.mp4").resolve()
        find_video_files([Path("does_not_exist.mp4")])
        mock_warn.assert_called_with(f"{p} does not exist, skipping")


def test_probe_files_no_ffprobe() -> None:
    with patch("h265ify.pipeline.ffprobe_available", return_value=False):
        console = MagicMock()
        results = probe_files([Path("test.mp4")], console)
        assert results == []
        console.print.assert_called_with("[red]error:[/] ffprobe not found.")


def test_probe_files_probe_fails() -> None:
    with patch("h265ify.pipeline.ffprobe_available", return_value=True):
        with patch("h265ify.pipeline.probe", return_value=None):
            console = MagicMock()
            results = probe_files([Path("test.mp4")], console)
            assert results == []
            console.print.assert_any_call(
                "  [yellow]warning:[/] could not probe test.mp4, skipping"
            )

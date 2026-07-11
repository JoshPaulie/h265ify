import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from h265ify import _cmd_replace, _cmd_encode
from h265ify.pipeline import ReplacePair


def test_cmd_replace_permanent_abort() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.input.return_value = "no"
    args = MagicMock(permanent=True, dry_run=False)
    with pytest.raises(SystemExit) as e:
        _cmd_replace(args, console)
    assert e.value.code == 0
    console.input.assert_called_once()


def test_cmd_replace_permanent_confirm(tmp_path: Path) -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.input.return_value = "yes"
    h265 = tmp_path / "in_h265.mp4"
    orig = tmp_path / "in.mp4"
    h265.write_bytes(b"smaller file")
    orig.write_bytes(b"original file content here")
    args = MagicMock(permanent=True, dry_run=False, paths=[tmp_path])
    with patch(
        "h265ify.find_replace_pairs",
        return_value=[ReplacePair(h265, orig)],
    ):
        with patch("h265ify.run_replace", return_value=(1, 0)):
            _cmd_replace(args, console)
            assert console.print.call_count > 0


def test_cmd_replace_no_pairs() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(permanent=False, dry_run=False, paths=[Path(".")])
    with patch("h265ify.find_replace_pairs", return_value=[]):
        with pytest.raises(SystemExit) as e:
            _cmd_replace(args, console)
        assert e.value.code == 0


def test_cmd_replace_dry_run(tmp_path: Path) -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    h265 = tmp_path / "in_h265.mp4"
    orig = tmp_path / "in.mp4"
    h265.write_bytes(b"smaller file")
    orig.write_bytes(b"original file content here")
    args = MagicMock(permanent=False, dry_run=True, paths=[tmp_path])
    with patch(
        "h265ify.find_replace_pairs",
        return_value=[ReplacePair(h265, orig)],
    ):
        with patch("h265ify.run_replace", return_value=(1, 0)):
            _cmd_replace(args, console)
            assert console.print.call_count > 0


def test_cmd_encode_cpu() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[]):
        with patch("h265ify.probe_files", return_value=[]):
            with patch("h265ify.run_pipeline", return_value=([], False)):
                with pytest.raises(SystemExit) as e:
                    _cmd_encode(args, console)
                assert e.value.code == 0


def test_cmd_encode_no_video_files() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=False,
        crf=23,
        preset="medium",
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    from h265ify.hardware import Encoder

    with patch(
        "h265ify.detect_encoder", return_value=Encoder("hevc_videotoolbox", True, "VT")
    ):
        with patch("h265ify.find_video_files", return_value=[]):
            with pytest.raises(SystemExit) as e:
                _cmd_encode(args, console)
            assert e.value.code == 0


def test_cmd_replace_ignored_args() -> None:
    from h265ify import main

    with patch(
        "sys.argv",
        [
            "h265ify",
            "--replace",
            "--resize",
            "720p",
            "--preset",
            "fast",
            "--reencode-audio",
            "--format",
            "mkv",
            "test.mp4",
        ],
    ):
        with patch("h265ify.find_replace_pairs", return_value=[]):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0

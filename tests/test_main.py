import pytest
from unittest.mock import patch
from h265ify import main


def test_main_mutually_exclusive() -> None:
    with patch("sys.argv", ["h265ify", "--replace", "--yolo", "test.mp4"]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1


def test_main_replace_mode() -> None:
    with patch("sys.argv", ["h265ify", "--replace", "--crf", "20", "test.mp4"]):
        with patch("h265ify.find_replace_pairs", return_value=[]):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0


def test_main_invalid_crf() -> None:
    with patch("sys.argv", ["h265ify", "--crf", "100", "test.mp4"]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1


def test_main_invalid_resize() -> None:
    with patch("sys.argv", ["h265ify", "--resize", "invalid", "test.mp4"]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1


def test_main_encode_mode() -> None:
    with patch("sys.argv", ["h265ify", "test.mp4"]):
        with patch("h265ify.find_video_files", return_value=[]):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0

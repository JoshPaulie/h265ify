from pathlib import Path
from h265ify.pipeline import get_output_path


def test_get_output_path_normalize_webm() -> None:
    in_path = Path("video.webm")
    out_path = get_output_path(in_path, replace=False)
    assert out_path.name == "video_h265.mp4"


def test_get_output_path_normalize_avi() -> None:
    in_path = Path("video.avi")
    out_path = get_output_path(in_path, replace=False)
    assert out_path.name == "video_h265.mp4"

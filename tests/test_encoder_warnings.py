from pathlib import Path
from h265ify.encoder import build_command
from h265ify.hardware import Encoder
from h265ify.probe import ProbeResult, SubtitleStream


def test_bitmap_dropped_warning() -> None:
    probe = ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=10.0,
        file_size=1000,
        subtitle_streams=[
            SubtitleStream(index=2, codec="hdmv_pgs_subtitle", is_text=False)
        ],
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")
    warnings: list[str] = []
    build_command(
        Path("in.mp4"), Path("out.mp4"), probe, enc, crf=23, warnings=warnings
    )
    assert len(warnings) == 1
    assert "dropping bitmap subtitles (hdmv_pgs_subtitle)" in warnings[0]


def test_resize_warnings() -> None:
    probe = ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=10.0,
        file_size=1000,
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    warnings1: list[str] = []
    build_command(
        Path("in.mp4"),
        Path("out.mp4"),
        probe,
        enc,
        crf=23,
        resize="invalidx100",
        warnings=warnings1,
    )
    assert len(warnings1) == 1
    assert "invalid resize spec" in warnings1[0]

    warnings2: list[str] = []
    build_command(
        Path("in.mp4"),
        Path("out.mp4"),
        probe,
        enc,
        crf=23,
        resize="invalid_width_only",
        warnings=warnings2,
    )
    assert len(warnings2) == 1
    assert "invalid resize spec" in warnings2[0]

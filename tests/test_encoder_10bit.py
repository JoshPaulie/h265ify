from pathlib import Path
from h265ify.encoder import build_command
from h265ify.hardware import Encoder
from h265ify.probe import ProbeResult, ColorInfo


def test_10bit_nvenc() -> None:
    probe = ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=10.0,
        file_size=1000,
        color=ColorInfo(bit_depth=10),
    )
    enc = Encoder(name="hevc_nvenc", is_hardware=True, label="NVENC")
    cmd = build_command(Path("in.mp4"), Path("out.mp4"), probe, enc, crf=23)
    assert "-pix_fmt" in cmd
    assert "p010le" in cmd


def test_10bit_qsv() -> None:
    probe = ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=10.0,
        file_size=1000,
        color=ColorInfo(bit_depth=10),
    )
    enc = Encoder(name="hevc_qsv", is_hardware=True, label="QSV")
    cmd = build_command(Path("in.mp4"), Path("out.mp4"), probe, enc, crf=23)
    assert "-pix_fmt" in cmd
    assert "p010le" in cmd


def test_10bit_amf() -> None:
    probe = ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=10.0,
        file_size=1000,
        color=ColorInfo(bit_depth=10),
    )
    enc = Encoder(name="hevc_amf", is_hardware=True, label="AMF")
    cmd = build_command(Path("in.mp4"), Path("out.mp4"), probe, enc, crf=23)
    assert "-pix_fmt" in cmd
    assert "p010le" in cmd

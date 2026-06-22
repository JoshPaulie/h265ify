"""Tests for encoder.py - format_size, format_duration, build_command."""

from __future__ import annotations

from pathlib import Path

from h265ify.encoder import (
    _build_scale_filter,
    build_command,
    format_duration,
    format_size,
)
from h265ify.hardware import Encoder
from h265ify.probe import AudioStream, ColorInfo, ProbeResult, SubtitleStream


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------
class TestFormatSize:
    def test_bytes(self) -> None:
        assert format_size(0) == "0.0 B"
        assert format_size(500) == "500.0 B"
        assert format_size(1023) == "1023.0 B"

    def test_kb(self) -> None:
        assert format_size(1024) == "1.0 KB"
        assert format_size(1536) == "1.5 KB"
        assert format_size(1024 * 1023) == "1023.0 KB"

    def test_mb(self) -> None:
        assert format_size(1024 * 1024) == "1.0 MB"

    def test_gb(self) -> None:
        assert format_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_tb(self) -> None:
        assert format_size(1024 * 1024 * 1024 * 1024) == "1.0 TB"

    def test_pb(self) -> None:
        assert format_size(1024 * 1024 * 1024 * 1024 * 1024) == "1.0 PB"


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------
class TestFormatDuration:
    def test_seconds(self) -> None:
        assert format_duration(0) == "0s"
        assert format_duration(30.7) == "31s"
        assert format_duration(59) == "59s"

    def test_minutes(self) -> None:
        assert format_duration(60) == "1m 0s"
        assert format_duration(90) == "1m 30s"
        assert format_duration(3599) == "59m 59s"

    def test_hours(self) -> None:
        assert format_duration(3600) == "1h 0m 0s"
        assert format_duration(3661) == "1h 1m 1s"
        assert format_duration(7322) == "2h 2m 2s"


# ---------------------------------------------------------------------------
# build_command helpers
# ---------------------------------------------------------------------------
def _probe(
    *,
    is_h265: bool = False,
    bit_depth: int = 8,
    width: int = 1920,
    height: int = 1080,
    color_space: str | None = None,
    color_transfer: str | None = None,
    color_primaries: str | None = None,
    mastering_display: str | None = None,
    max_content_light: int | None = None,
    max_average_light: int | None = None,
    audio_streams: list[AudioStream] | None = None,
    subtitle_streams: list[SubtitleStream] | None = None,
) -> ProbeResult:
    return ProbeResult(
        path=Path("/tmp/test.mkv"),
        is_h265=is_h265,
        video_codec="hevc" if is_h265 else "h264",
        width=width,
        height=height,
        duration=120.0,
        file_size=100_000_000,
        color=ColorInfo(
            bit_depth=bit_depth,
            color_space=color_space,
            color_transfer=color_transfer,
            color_primaries=color_primaries,
            mastering_display=mastering_display,
            max_content_light=max_content_light,
            max_average_light=max_average_light,
        ),
        audio_streams=audio_streams or [],
        subtitle_streams=subtitle_streams or [],
    )


def _libx265() -> Encoder:
    return Encoder(name="libx265", is_hardware=False, label="CPU (libx265)")


def _has_flag(cmd: list[str], flag: str) -> bool:
    return flag in cmd


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# ---------------------------------------------------------------------------
# build_command - MP4 output (default)
# ---------------------------------------------------------------------------
class TestBuildCommandMP4:
    def test_basic_libx265_mp4(self) -> None:
        cmd = build_command(
            Path("/tmp/video.mkv"),
            Path("/tmp/video_h265.mp4"),
            _probe(),
            _libx265(),
            crf=23,
            output_format="mp4",
        )
        assert cmd[0] == "ffmpeg"
        assert _has_flag(cmd, "-c:v") and _flag_value(cmd, "-c:v") == "libx265"
        assert _has_flag(cmd, "-crf") and _flag_value(cmd, "-crf") == "23"
        assert _has_flag(cmd, "-preset") and _flag_value(cmd, "-preset") == "medium"
        assert _has_flag(cmd, "-tag:v") and _flag_value(cmd, "-tag:v") == "hvc1"
        assert _has_flag(cmd, "-movflags")
        assert "+faststart" in _flag_value(cmd, "-movflags")

    def test_mp4_output_path_ends_cmd(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert cmd[-1] == "/tmp/v_h265.mp4"

    def test_videotoolbox_mp4(self) -> None:
        encoder = Encoder(name="hevc_videotoolbox", is_hardware=True, label="VT")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            encoder,
            crf=23,
        )
        assert _flag_value(cmd, "-c:v") == "hevc_videotoolbox"
        assert _has_flag(cmd, "-q:v")
        assert _has_flag(cmd, "-realtime")
        assert _has_flag(cmd, "-allow_sw")

    def test_nvenc_mp4(self) -> None:
        encoder = Encoder(name="hevc_nvenc", is_hardware=True, label="NVENC")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            encoder,
            crf=23,
        )
        assert _has_flag(cmd, "-cq") and _flag_value(cmd, "-cq") == "23"
        assert _has_flag(cmd, "-preset") and _flag_value(cmd, "-preset") == "p4"

    def test_qsv_mp4(self) -> None:
        encoder = Encoder(name="hevc_qsv", is_hardware=True, label="QSV")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            encoder,
            crf=20,
        )
        assert _has_flag(cmd, "-global_quality")
        assert _has_flag(cmd, "-look_ahead")
        assert _has_flag(cmd, "-preset") and _flag_value(cmd, "-preset") == "slow"

    def test_preset_and_tune_passed_through(self) -> None:
        encoder = Encoder(name="libx265", is_hardware=False, label="CPU")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            encoder,
            crf=20,
            preset="fast",
            tune="animation",
        )
        assert _has_flag(cmd, "-preset") and _flag_value(cmd, "-preset") == "fast"
        assert _has_flag(cmd, "-tune") and _flag_value(cmd, "-tune") == "animation"

    def test_amf_cqp_mp4(self) -> None:
        encoder = Encoder(name="hevc_amf", is_hardware=True, label="AMF")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            encoder,
            crf=18,
        )
        assert _has_flag(cmd, "-rc") and _flag_value(cmd, "-rc") == "cqp"
        assert _has_flag(cmd, "-qp_p")
        assert _has_flag(cmd, "-qp_i")

    def test_10bit_software_mp4(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(bit_depth=10),
            _libx265(),
            crf=23,
        )
        assert (
            _has_flag(cmd, "-pix_fmt") and _flag_value(cmd, "-pix_fmt") == "yuv420p10le"
        )

    def test_10bit_videotoolbox_mp4(self) -> None:
        encoder = Encoder(name="hevc_videotoolbox", is_hardware=True, label="VT")
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(bit_depth=10),
            encoder,
            crf=23,
        )
        assert _has_flag(cmd, "-pix_fmt") and _flag_value(cmd, "-pix_fmt") == "p010le"

    def test_8bit_no_pix_fmt(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(bit_depth=8),
            _libx265(),
            crf=23,
        )
        assert not _has_flag(cmd, "-pix_fmt")

    def test_hdr_metadata_mp4(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(
                color_space="bt2020nc",
                color_transfer="smpte2084",
                color_primaries="bt2020",
                mastering_display="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)",
                max_content_light=1000,
                max_average_light=400,
            ),
            _libx265(),
            crf=23,
        )
        assert _flag_value(cmd, "-color_primaries") == "bt2020"
        assert _flag_value(cmd, "-color_trc") == "smpte2084"
        assert _flag_value(cmd, "-colorspace") == "bt2020nc"
        # HDR10 mastering display and CLL are injected for libx265 via -x265-params
        assert _has_flag(cmd, "-x265-params")
        x265p = _flag_value(cmd, "-x265-params")
        assert "master-display=" in x265p
        assert "max-cll=1000,400" in x265p

    def test_no_hdr_no_flags(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(),
            _libx265(),
            crf=23,
        )
        for flag in (
            "-color_primaries",
            "-color_trc",
            "-colorspace",
            "-x265-params",
        ):
            assert flag not in cmd

    # -- Audio --
    def test_audio_stream_copy_mp4(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(
                audio_streams=[
                    AudioStream(index=0, codec="aac", channels=2),
                    AudioStream(index=1, codec="ac3", channels=6),
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:a?" in maps
        assert _flag_value(cmd, "-c:a") == "copy"

    def test_reencode_audio_mp4_aac(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(audio_streams=[AudioStream(index=0, codec="dts", channels=6)]),
            _libx265(),
            crf=23,
            output_format="mp4",
            reencode_audio=True,
        )
        assert _flag_value(cmd, "-c:a") == "aac"
        assert _flag_value(cmd, "-b:a") == "192k"

    def test_reencode_audio_mkv_opus(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(audio_streams=[AudioStream(index=0, codec="dts", channels=6)]),
            _libx265(),
            crf=23,
            output_format="mkv",
            reencode_audio=True,
        )
        assert _flag_value(cmd, "-c:a") == "libopus"
        assert _flag_value(cmd, "-b:a") == "128k"

    def test_no_audio_no_map(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(audio_streams=[]),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:a?" not in maps
        assert "-c:a" not in cmd

    # -- Subtitles (MP4) --
    def test_text_subs_mp4_mov_text(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(
                subtitle_streams=[
                    SubtitleStream(index=2, codec="subrip", is_text=True),
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:s?" in maps
        assert _flag_value(cmd, "-c:s") == "mov_text"

    def test_bitmap_subs_mp4_dropped(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(
                subtitle_streams=[
                    SubtitleStream(index=2, codec="hdmv_pgs_subtitle", is_text=False),
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:s?" not in maps
        assert "-c:s" not in cmd

    def test_mixed_subs_mp4_text_only(self) -> None:
        """Mixed text+bitmap subs → only text subs mapped by index (not 0:s?)."""
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(
                subtitle_streams=[
                    SubtitleStream(index=2, codec="subrip", is_text=True),
                    SubtitleStream(index=3, codec="hdmv_pgs_subtitle", is_text=False),
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        # Should map only the text sub by its stream index, not the wildcard
        assert "0:2" in maps
        assert "0:3" not in maps
        assert "0:s?" not in maps
        assert _flag_value(cmd, "-c:s") == "mov_text"


# ---------------------------------------------------------------------------
# build_command - MKV output
# ---------------------------------------------------------------------------
class TestBuildCommandMKV:
    def test_mkv_no_hvc1_tag(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert "-tag:v" not in cmd

    def test_mkv_no_faststart(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert "-movflags" not in cmd

    def test_mkv_subs_stream_copy_all(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(
                subtitle_streams=[
                    SubtitleStream(index=2, codec="subrip", is_text=True),
                    SubtitleStream(index=3, codec="hdmv_pgs_subtitle", is_text=False),
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:s?" in maps
        assert _flag_value(cmd, "-c:s") == "copy"

    def test_mkv_no_subs_no_map(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(subtitle_streams=[]),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:s?" not in maps


# ---------------------------------------------------------------------------
# build_command - MOV output (same mp4_based path as MP4)
# ---------------------------------------------------------------------------
class TestBuildCommandMOV:
    def test_mov_has_hvc1_tag(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mov"),
            Path("/tmp/v_h265.mov"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert _has_flag(cmd, "-tag:v") and _flag_value(cmd, "-tag:v") == "hvc1"

    def test_mov_has_faststart(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mov"),
            Path("/tmp/v_h265.mov"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert _has_flag(cmd, "-movflags")
        assert "+faststart" in _flag_value(cmd, "-movflags")

    def test_mov_text_subs_converted(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mov"),
            Path("/tmp/v_h265.mov"),
            _probe(
                subtitle_streams=[SubtitleStream(index=2, codec="subrip", is_text=True)]
            ),
            _libx265(),
            crf=23,
        )
        assert _flag_value(cmd, "-c:s") == "mov_text"

    def test_mov_bitmap_subs_dropped(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mov"),
            Path("/tmp/v_h265.mov"),
            _probe(
                subtitle_streams=[
                    SubtitleStream(index=2, codec="hdmv_pgs_subtitle", is_text=False)
                ]
            ),
            _libx265(),
            crf=23,
        )
        maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
        assert "0:s?" not in maps

    def test_mov_reencode_audio_uses_aac(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mov"),
            Path("/tmp/v_h265.mov"),
            _probe(audio_streams=[AudioStream(index=0, codec="pcm_s16le", channels=2)]),
            _libx265(),
            crf=23,
            reencode_audio=True,
        )
        assert _flag_value(cmd, "-c:a") == "aac"
        assert _flag_value(cmd, "-b:a") == "192k"


# ---------------------------------------------------------------------------
# build_command - reencode_audio
# ---------------------------------------------------------------------------
class TestBuildCommandReencodeAudio:
    def test_default_is_stream_copy(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(audio_streams=[AudioStream(index=0, codec="dts", channels=6)]),
            _libx265(),
            crf=23,
        )
        assert _flag_value(cmd, "-c:a") == "copy"

    def test_reencode_flag_mp4_aac(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mp4"),
            _probe(audio_streams=[AudioStream(index=0, codec="dts", channels=6)]),
            _libx265(),
            crf=23,
            reencode_audio=True,
        )
        assert _flag_value(cmd, "-c:a") == "aac"

    def test_reencode_flag_mkv_opus(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(audio_streams=[AudioStream(index=0, codec="dts", channels=6)]),
            _libx265(),
            crf=23,
            reencode_audio=True,
        )
        assert _flag_value(cmd, "-c:a") == "libopus"


class TestBuildCommandResize:
    def test_720p_resize(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=1920, height=1080),
            _libx265(),
            crf=23,
            resize="720p",
        )
        assert _has_flag(cmd, "-vf")
        assert _flag_value(cmd, "-vf") == "scale=1280:-2"

    def test_1080p_resize(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=3840, height=2160),
            _libx265(),
            crf=23,
            resize="1080p",
        )
        assert _flag_value(cmd, "-vf") == "scale=1920:-2"

    def test_4k_resize(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=7680, height=4320),
            _libx265(),
            crf=23,
            resize="4k",
        )
        assert _flag_value(cmd, "-vf") == "scale=3840:-2"

    def test_explicit_resize(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=1920, height=1080),
            _libx265(),
            crf=23,
            resize="1280x720",
        )
        assert "scale=1280:720" in _flag_value(cmd, "-vf")

    def test_no_upscale_skips_when_smaller(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=640, height=480),
            _libx265(),
            crf=23,
            resize="720p",
            no_upscale=True,
        )
        assert not _has_flag(cmd, "-vf")

    def test_no_upscale_allows_when_larger(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(width=1920, height=1080),
            _libx265(),
            crf=23,
            resize="720p",
            no_upscale=True,
        )
        assert _has_flag(cmd, "-vf")

    def test_no_resize_no_vf(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(),
            _libx265(),
            crf=23,
        )
        assert not _has_flag(cmd, "-vf")

    def test_invalid_resize_spec_ignored(self) -> None:
        cmd = build_command(
            Path("/tmp/v.mkv"),
            Path("/tmp/v_h265.mkv"),
            _probe(),
            _libx265(),
            crf=23,
            resize="banana",
        )
        assert not _has_flag(cmd, "-vf")


# ---------------------------------------------------------------------------
# _build_scale_filter
# ---------------------------------------------------------------------------
class TestBuildScaleFilter:
    def test_720p(self) -> None:
        result = _build_scale_filter("720p", _probe(width=1920, height=1080), False)
        assert result == "scale=1280:-2"

    def test_1080p(self) -> None:
        result = _build_scale_filter("1080p", _probe(width=3840, height=2160), False)
        assert result == "scale=1920:-2"

    def test_4k(self) -> None:
        result = _build_scale_filter("4k", _probe(width=7680, height=4320), False)
        assert result == "scale=3840:-2"

    def test_explicit_wxh(self) -> None:
        result = _build_scale_filter("1280x720", _probe(), False)
        assert result == "scale=1280:720:force_original_aspect_ratio=decrease"

    def test_width_only(self) -> None:
        result = _build_scale_filter("1280", _probe(), False)
        assert result == "scale=1280:-2"

    def test_no_upscale_when_smaller(self) -> None:
        result = _build_scale_filter(
            "720p", _probe(width=640, height=480), no_upscale=True
        )
        assert result is None

    def test_no_upscale_when_equal(self) -> None:
        result = _build_scale_filter(
            "720p", _probe(width=1280, height=720), no_upscale=True
        )
        assert result is None

    def test_upscale_when_larger_allowed(self) -> None:
        result = _build_scale_filter(
            "720p", _probe(width=1920, height=1080), no_upscale=True
        )
        assert result is not None

    def test_invalid_spec_returns_none(self) -> None:
        result = _build_scale_filter("abc", _probe(), False)
        assert result is None

    def test_invalid_wxh_returns_none(self) -> None:
        result = _build_scale_filter("abcxdef", _probe(), False)
        assert result is None

    def test_rounds_to_even(self) -> None:
        result = _build_scale_filter("1281x719", _probe(), False)
        # 1281→1280, 719→718
        assert result is not None
        assert "1280:718" in result

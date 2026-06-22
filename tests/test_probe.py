"""Tests for probe.py - pure parsing functions (no ffprobe invocation)."""

from __future__ import annotations

from pathlib import Path

from h265ify.probe import (
    _format_mastering_display,
    _get_language,
    _parse_color_info,
    _parse_probe,
)


# ---------------------------------------------------------------------------
# _parse_color_info
# ---------------------------------------------------------------------------
class TestParseColorInfo:
    def test_default_sdr(self) -> None:
        info = _parse_color_info({"pix_fmt": "yuv420p"})
        assert info.pix_fmt == "yuv420p"
        assert info.bit_depth == 8
        assert info.color_space is None
        assert info.color_transfer is None
        assert info.color_primaries is None
        assert not info.is_hdr

    def test_10bit(self) -> None:
        info = _parse_color_info({"pix_fmt": "yuv420p10le"})
        assert info.bit_depth == 10

    def test_12bit(self) -> None:
        info = _parse_color_info({"pix_fmt": "yuv420p12le"})
        assert info.bit_depth == 12

    def test_8bit_explicit(self) -> None:
        info = _parse_color_info({"pix_fmt": "yuv422p"})
        assert info.bit_depth == 8

    def test_hdr_detection(self) -> None:
        info = _parse_color_info(
            {
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "color_primaries": "bt2020",
            }
        )
        assert info.is_hdr
        assert info.color_transfer == "smpte2084"
        assert info.color_space == "bt2020nc"
        assert info.color_primaries == "bt2020"

    def test_hdr_hlg(self) -> None:
        info = _parse_color_info({"color_transfer": "arib-std-b67"})
        assert info.is_hdr

    def test_no_transfer_not_hdr(self) -> None:
        info = _parse_color_info({})
        assert not info.is_hdr

    def test_mastering_display_side_data(self) -> None:
        info = _parse_color_info(
            {
                "pix_fmt": "yuv420p10le",
                "side_data_list": [
                    {
                        "side_data_type": "Mastering display metadata",
                        "red_x": "34000/50000",
                        "red_y": "16000/50000",
                        "green_x": "13250/50000",
                        "green_y": "34500/50000",
                        "blue_x": "7500/50000",
                        "blue_y": "3000/50000",
                        "white_point_x": "15635/50000",
                        "white_point_y": "16450/50000",
                        "min_luminance": "50/10000",
                        "max_luminance": "10000000/10000",
                    }
                ],
            }
        )
        assert info.mastering_display is not None
        assert "G(13250,34500)" in info.mastering_display

    def test_content_light_level_side_data(self) -> None:
        info = _parse_color_info(
            {
                "pix_fmt": "yuv420p10le",
                "side_data_list": [
                    {
                        "side_data_type": "Content light level metadata",
                        "max_content": 1000,
                        "max_average": 400,
                    }
                ],
            }
        )
        assert info.max_content_light == 1000
        assert info.max_average_light == 400

    def test_no_side_data(self) -> None:
        info = _parse_color_info({"pix_fmt": "yuv420p"})
        assert info.mastering_display is None
        assert info.max_content_light is None
        assert info.max_average_light is None


# ---------------------------------------------------------------------------
# _format_mastering_display
# ---------------------------------------------------------------------------
class TestFormatMasteringDisplay:
    def test_typical_hdr10(self) -> None:
        sd: dict[str, str] = {
            "red_x": "34000/50000",
            "red_y": "16000/50000",
            "green_x": "13250/50000",
            "green_y": "34500/50000",
            "blue_x": "7500/50000",
            "blue_y": "3000/50000",
            "white_point_x": "15635/50000",
            "white_point_y": "16450/50000",
            "min_luminance": "50/10000",
            "max_luminance": "10000000/10000",
        }
        result = _format_mastering_display(sd)
        # G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)
        assert "G(13250,34500)" in result
        assert "B(7500,3000)" in result
        assert "R(34000,16000)" in result
        assert "WP(15635,16450)" in result
        assert "L(10000000,50)" in result

    def test_whole_numbers(self) -> None:
        sd = {
            "red_x": "1/1",
            "red_y": "2/1",
            "green_x": "3/1",
            "green_y": "4/1",
            "blue_x": "5/1",
            "blue_y": "6/1",
            "white_point_x": "7/1",
            "white_point_y": "8/1",
            "min_luminance": "0/1",
            "max_luminance": "1000/1",
        }
        result = _format_mastering_display(sd)
        assert "G(3,4)" in result
        assert "L(1000,0)" in result

    def test_missing_slash(self) -> None:
        sd = {
            "red_x": "1",
            "red_y": "2",
            "green_x": "3",
            "green_y": "4",
            "blue_x": "5",
            "blue_y": "6",
            "white_point_x": "7",
            "white_point_y": "8",
            "min_luminance": "0",
            "max_luminance": "1000",
        }
        result = _format_mastering_display(sd)
        assert "G(3,4)" in result
        assert "L(1000,0)" in result


# ---------------------------------------------------------------------------
# _get_language
# ---------------------------------------------------------------------------
class TestGetLanguage:
    def test_language_tag(self) -> None:
        assert _get_language({"tags": {"language": "eng"}}) == "eng"

    def test_lang_fallback(self) -> None:
        assert _get_language({"tags": {"lang": "jpn"}}) == "jpn"

    def test_language_preferred_over_lang(self) -> None:
        assert _get_language({"tags": {"language": "eng", "lang": "jpn"}}) == "eng"

    def test_no_tags(self) -> None:
        assert _get_language({}) is None

    def test_no_language_in_tags(self) -> None:
        assert _get_language({"tags": {"title": "hello"}}) is None


# ---------------------------------------------------------------------------
# _parse_probe
# ---------------------------------------------------------------------------
class TestParseProbe:
    """Integration-level tests of _parse_probe with realistic ffprobe JSON."""

    def test_basic_video(self) -> None:
        data = {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "tags": {"language": "eng"},
                },
            ],
            "format": {"duration": "120.5", "size": "50000000"},
        }
        result = _parse_probe(Path("/tmp/test.mp4"), data)
        assert result is not None
        assert not result.is_h265
        assert result.video_codec == "h264"
        assert result.width == 1920
        assert result.height == 1080
        assert result.duration == 120.5
        assert result.file_size == 50000000
        assert len(result.audio_streams) == 1
        assert result.audio_streams[0].codec == "aac"
        assert result.audio_streams[0].channels == 2
        assert result.audio_streams[0].language == "eng"

    def test_h265_detected(self) -> None:
        data = {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}],
            "format": {"duration": "0", "size": "0"},
        }
        result = _parse_probe(Path("/tmp/test.mkv"), data)
        assert result is not None
        assert result.is_h265

    def test_h265_alias_detected(self) -> None:
        data = {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h265"}],
            "format": {"duration": "0", "size": "0"},
        }
        result = _parse_probe(Path("/tmp/test.mkv"), data)
        assert result is not None
        assert result.is_h265

    def test_no_video_stream(self) -> None:
        data = {
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "mp3"}],
            "format": {},
        }
        assert _parse_probe(Path("/tmp/audio.mp3"), data) is None

    def test_empty_streams(self) -> None:
        assert _parse_probe(Path("/tmp/bad.mp4"), {"streams": [], "format": {}}) is None

    def test_subtitle_classification(self) -> None:
        data = {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "tags": {"language": "eng"},
                },
                {
                    "index": 3,
                    "codec_type": "subtitle",
                    "codec_name": "hdmv_pgs_subtitle",
                },
            ],
            "format": {"duration": "0", "size": "0"},
        }
        result = _parse_probe(Path("/tmp/test.mkv"), data)
        assert result is not None
        assert len(result.subtitle_streams) == 2
        assert result.subtitle_streams[0].is_text is True
        assert result.subtitle_streams[1].is_text is False
        assert len(result.dropped_subtitles) == 1
        assert result.dropped_subtitles[0].codec == "hdmv_pgs_subtitle"

    def test_unknown_subtitle_is_text(self) -> None:
        """Subtitles not in BITMAP set are treated as text."""
        data = {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "some_exotic_text_sub",
                },
            ],
            "format": {"duration": "0", "size": "0"},
        }
        result = _parse_probe(Path("/tmp/test.mkv"), data)
        assert result is not None
        assert result.subtitle_streams[0].is_text is True
        assert result.dropped_subtitles == []

    def test_missing_duration_and_size(self) -> None:
        data = {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
            "format": {},
        }
        result = _parse_probe(Path("/tmp/test.mp4"), data)
        assert result is not None
        assert result.duration == 0
        assert result.file_size == 0

"""File probing via ffprobe - codec detection, HDR metadata, stream analysis."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from .logger import logger

# Bitmap subtitle codecs (cannot convert to mov_text)
_BITMAP_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "dvb_teletext",
    "xsub",
    "pgssub",
    "vobsub",
}


@dataclass
class AudioStream:
    index: int
    codec: str
    channels: int
    language: str | None = None


@dataclass
class SubtitleStream:
    index: int
    codec: str
    is_text: bool  # True if convertible to mov_text
    language: str | None = None


@dataclass
class ColorInfo:
    """HDR/SDR color metadata from the source."""

    pix_fmt: str = "yuv420p"
    bit_depth: int = 8
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    is_hdr: bool = False
    # HDR10 mastering display metadata (as ffmpeg string)
    mastering_display: str | None = None
    max_content_light: int | None = None
    max_average_light: int | None = None


@dataclass
class ProbeResult:
    """Full probe result for a video file."""

    path: Path
    is_h265: bool
    video_codec: str
    width: int
    height: int
    duration: float  # seconds, 0 if unknown
    file_size: int  # bytes
    color: ColorInfo = field(default_factory=ColorInfo)
    audio_streams: list[AudioStream] = field(default_factory=list)
    subtitle_streams: list[SubtitleStream] = field(default_factory=list)
    # Indexes of bitmap subtitles that will be dropped
    dropped_subtitles: list[SubtitleStream] = field(default_factory=list)


def ffprobe_available() -> bool:
    """Return True if ffprobe is found on PATH."""
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def probe(path: Path) -> ProbeResult | None:
    """Run ffprobe on a file and return structured data.

    Returns None if ffprobe fails (not a video, corrupted, etc.).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning(f"ffprobe unavailable for {path.name}")
        return None

    if result.returncode != 0:
        logger.warning(f"ffprobe failed (rc={result.returncode}) for {path.name}")
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(f"ffprobe returned invalid JSON for {path.name}")
        return None

    parsed = _parse_probe(path, data)
    if parsed is None:
        logger.warning(f"no video stream found in {path.name}")
    return parsed


def _parse_probe(path: Path, data: dict[str, Any]) -> ProbeResult | None:
    """Parse ffprobe JSON output into a ProbeResult."""
    streams: list[dict[str, Any]] = data.get("streams", [])
    fmt: dict[str, Any] = data.get("format", {})

    # Find the first video stream
    video_stream: dict[str, Any] | None = None
    for s in streams:
        if s.get("codec_type") == "video":
            video_stream = s
            break

    if video_stream is None:
        return None  # No video stream - not a video file

    video_codec = video_stream.get("codec_name", "unknown")
    is_h265 = video_codec in ("hevc", "h265")

    # Parse video dimensions
    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)

    # Parse color info
    color = _parse_color_info(video_stream)

    # Parse audio streams
    audio_streams: list[AudioStream] = []
    for s in streams:
        if s.get("codec_type") == "audio":
            audio_streams.append(
                AudioStream(
                    index=s["index"],
                    codec=s.get("codec_name", "unknown"),
                    channels=s.get("channels", 0),
                    language=_get_language(s),
                )
            )

    # Parse subtitle streams
    subtitle_streams: list[SubtitleStream] = []
    dropped_subtitles: list[SubtitleStream] = []
    for s in streams:
        if s.get("codec_type") == "subtitle":
            codec = s.get("codec_name", "unknown")
            is_text = codec.lower() not in _BITMAP_SUBTITLE_CODECS
            sub = SubtitleStream(
                index=s["index"],
                codec=codec,
                is_text=is_text,
                language=_get_language(s),
            )
            subtitle_streams.append(sub)
            if not is_text:
                dropped_subtitles.append(sub)

    # Duration and filesize
    duration = float(fmt.get("duration", 0))
    file_size = int(fmt.get("size", 0))

    return ProbeResult(
        path=path,
        is_h265=is_h265,
        video_codec=video_codec,
        width=width,
        height=height,
        duration=duration,
        file_size=file_size,
        color=color,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
        dropped_subtitles=dropped_subtitles,
    )


def _parse_color_info(video_stream: dict[str, Any]) -> ColorInfo:
    """Extract color metadata from a ffprobe video stream dict."""
    pix_fmt = video_stream.get("pix_fmt", "yuv420p")

    # Determine bit depth from pix_fmt
    bit_depth = 8
    if "10" in pix_fmt:
        bit_depth = 10
    elif "12" in pix_fmt:
        bit_depth = 12

    color_space = video_stream.get("color_space")
    color_transfer = video_stream.get("color_transfer")
    color_primaries = video_stream.get("color_primaries")

    # Detect HDR
    hdr_transfers = {"smpte2084", "arib-std-b67"}
    is_hdr = color_transfer in hdr_transfers

    # Extract HDR side data
    mastering_display: str | None = None
    max_content_light: int | None = None
    max_average_light: int | None = None

    side_data_list = video_stream.get("side_data_list", [])
    for sd in side_data_list:
        sd_type = sd.get("side_data_type", "")
        if sd_type == "Mastering display metadata":
            mastering_display = _format_mastering_display(sd)
        elif sd_type == "Content light level metadata":
            max_content_light = sd.get("max_content")
            max_average_light = sd.get("max_average")

    return ColorInfo(
        pix_fmt=pix_fmt,
        bit_depth=bit_depth,
        color_space=color_space,
        color_transfer=color_transfer,
        color_primaries=color_primaries,
        is_hdr=is_hdr,
        mastering_display=mastering_display,
        max_content_light=max_content_light,
        max_average_light=max_average_light,
    )


def _format_mastering_display(sd: dict[str, Any]) -> str:
    """Format mastering display metadata for ffmpeg's -mastering_display_metadata flag."""

    # ffprobe gives rational values like "34000/50000" - ffmpeg expects the
    # raw numerator, not the computed float. Primaries are in 0.00002 units;
    # luminance values are in 0.0001 cd/m² units.
    def _xy(key: str) -> str:
        raw = str(sd.get(key, "0/1"))
        if "/" in raw:
            num, _den = raw.split("/", 1)
            return num
        return raw

    _COLOR_KEY: dict[str, str] = {
        "R": "red",
        "G": "green",
        "B": "blue",
        "WP": "white_point",
    }
    parts = []
    for color in ("G", "B", "R", "WP"):
        prefix = _COLOR_KEY[color]
        x = _xy(f"{prefix}_x")
        y = _xy(f"{prefix}_y")
        parts.append(f"{color}({x},{y})")

    min_lum = _xy("min_luminance")
    max_lum = _xy("max_luminance")
    parts.append(f"L({max_lum},{min_lum})")

    return "".join(parts)


def _get_language(stream: dict[str, Any]) -> str | None:
    """Extract language tag from a stream dict."""
    tags = cast(dict[str, Any], stream.get("tags", {}))
    return cast("str | None", tags.get("language") or tags.get("lang") or None)

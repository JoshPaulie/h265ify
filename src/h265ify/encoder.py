"""ffmpeg command builder and subprocess runner."""

from __future__ import annotations

import datetime
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path


from .hardware import Encoder, encoder_quality_flags
from .logger import FFMPEG_LOG_FILE, logger
from .probe import ProbeResult

ProgressCallback = Callable[
    [float, float, float], None
]  # (pct 0-100, speed, current_seconds)

CancelCheck = Callable[[], bool]  # Return True to abort encoding early

# Regexes for parsing ffmpeg -stats output
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")

# Valid color metadata values accepted by ffmpeg (avoids encoder rejection errors)
_VALID_COLOR_PRIMARIES: frozenset[str] = frozenset(
    {
        "bt709",
        "bt470m",
        "bt470bg",
        "smpte170m",
        "smpte240m",
        "film",
        "bt2020",
        "smpte428",
        "smpte431",
        "smpte432",
        "jedec-p22",
    }
)
_VALID_COLOR_TRANSFERS: frozenset[str] = frozenset(
    {
        "bt709",
        "bt470m",
        "bt470bg",
        "smpte170m",
        "smpte240m",
        "linear",
        "log",
        "log_sqrt",
        "iec61966-2-4",
        "bt1361",
        "iec61966-2-1",
        "bt2020-10",
        "bt2020-12",
        "smpte2084",
        "smpte428",
        "arib-std-b67",
    }
)
_VALID_COLOR_SPACES: frozenset[str] = frozenset(
    {
        "bt709",
        "fcc",
        "bt470bg",
        "smpte170m",
        "smpte240m",
        "ycgco",
        "bt2020nc",
        "bt2020c",
        "smpte2085",
        "chroma-derived-nc",
        "chroma-derived-c",
        "ictcp",
    }
)


def build_command(
    input_path: Path,
    output_path: Path,
    probe: ProbeResult,
    encoder: Encoder,
    crf: int,
    output_format: str | None = None,
    reencode_audio: bool = False,
    resize: str | None = None,
    no_upscale: bool = False,
    preset: str | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Build the ffmpeg command for re-encoding a single file to h265.

    Handles:
    - Video encoding with hardware or software encoder
    - HDR metadata passthrough
    - Resize with aspect ratio preservation
    - Audio stream copy (or re-encode to AAC/Opus)
    - Container-aware subtitle handling (MP4: mov_text, MKV: stream-copy)
    - hvc1 tagging + faststart for MP4
    """
    container = output_path.suffix.lstrip(".")
    is_mp4_based = container in ("mp4", "mov")

    cmd: list[str] = ["ffmpeg", "-y"]

    # Hide banner, only show errors + progress on stderr
    cmd.extend(["-hide_banner", "-loglevel", "error", "-stats"])

    cmd.extend(["-i", str(input_path)])

    # --- Video ---
    cmd.extend(["-map", "0:V"])
    cmd.extend(["-c:v", encoder.name])
    cmd.extend(encoder_quality_flags(encoder.name, crf, preset=preset))

    # hvc1 tag (QuickTime compatibility - for MP4/MOV)
    if is_mp4_based:
        cmd.extend(["-tag:v", "hvc1"])

    # Resize filter
    if resize:
        scale_filter = _build_scale_filter(resize, probe, no_upscale, warnings)
        if scale_filter:
            cmd.extend(["-vf", scale_filter])

    # Pixel format / bit depth
    if probe.color.bit_depth >= 10:
        # Preserve 10-bit
        if encoder.name == "libx265":
            cmd.extend(["-pix_fmt", "yuv420p10le"])
        elif encoder.name == "hevc_videotoolbox":
            cmd.extend(["-pix_fmt", "p010le"])  # VideoToolbox 10-bit
        # NVENC, QSV, AMF handle 10-bit via -pix_fmt p010le or yuv420p10le
        elif encoder.name in ("hevc_nvenc", "hevc_qsv", "hevc_amf"):
            cmd.extend(["-pix_fmt", "p010le"])

    # Color metadata passthrough (validated to avoid encoder rejections)
    if (
        probe.color.color_primaries
        and probe.color.color_primaries in _VALID_COLOR_PRIMARIES
    ):
        cmd.extend(["-color_primaries", probe.color.color_primaries])
    if (
        probe.color.color_transfer
        and probe.color.color_transfer in _VALID_COLOR_TRANSFERS
    ):
        cmd.extend(["-color_trc", probe.color.color_transfer])
    if probe.color.color_space and probe.color.color_space in _VALID_COLOR_SPACES:
        cmd.extend(["-colorspace", probe.color.color_space])

    # HDR10 mastering display and content light level:
    # libx265 requires injection via -x265-params (master-display / max-cll).
    # Hardware encoders propagate these from decoded-frame side data automatically;
    # no explicit flags are needed or accepted by ffmpeg for them.
    if encoder.name == "libx265":
        x265_hdr: list[str] = []
        if probe.color.mastering_display:
            x265_hdr.append(f"master-display={probe.color.mastering_display}")
        if (
            probe.color.max_content_light is not None
            and probe.color.max_average_light is not None
        ):
            x265_hdr.append(
                f"max-cll={probe.color.max_content_light},{probe.color.max_average_light}"
            )
        if x265_hdr:
            cmd.extend(["-x265-params", ":".join(x265_hdr)])

    # --- Audio ---
    if probe.audio_streams:
        cmd.extend(["-map", "0:a?"])
        if reencode_audio:
            if is_mp4_based:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            else:
                cmd.extend(["-c:a", "libopus", "-b:a", "128k"])
        else:
            cmd.extend(["-c:a", "copy"])

    # --- Subtitles ---
    if probe.subtitle_streams:
        if is_mp4_based:
            # MP4/MOV: convert text subs to mov_text, warn about bitmap subs
            text_subs = [s for s in probe.subtitle_streams if s.is_text]
            bitmap_dropped = [s for s in probe.subtitle_streams if not s.is_text]
            if bitmap_dropped:
                names = ", ".join(s.codec for s in bitmap_dropped)
                msg = (
                    f"  [yellow]warning:[/] dropping bitmap subtitles ({names}) "
                    f"from {input_path.name} - MP4/MOV only support text-based subtitles. "
                    f"Use --format mkv to preserve all subtitle types."
                )
                if warnings is not None:
                    warnings.append(msg)
            if text_subs:
                if bitmap_dropped:
                    # Map only text subtitle streams by stream index to
                    # exclude bitmaps — `-map 0:s?` would include them all
                    # and `-c:s mov_text` cannot convert bitmap codecs.
                    for sub in text_subs:
                        cmd.extend(["-map", f"0:{sub.index}"])
                else:
                    cmd.extend(["-map", "0:s?"])
                cmd.extend(["-c:s", "mov_text"])
        else:
            # MKV: stream-copy all subtitle tracks
            cmd.extend(["-map", "0:s?"])
            cmd.extend(["-c:s", "copy"])

    # --- MP4/MOV muxer: faststart for streaming + QuickTime ---
    if is_mp4_based:
        cmd.extend(["-movflags", "+faststart"])

    cmd.append(str(output_path))
    return cmd


def _build_scale_filter(
    resize: str,
    probe: ProbeResult,
    no_upscale: bool,
    warnings: list[str] | None = None,
) -> str | None:
    """Build an ffmpeg scale filter from a resize spec.

    Accepts:
    - '720p', '1080p', '4k' → target width with auto height
    - '1280x720' → explicit width × height

    Returns None if no resize is needed.
    """
    # Parse target dimensions
    target_w: int
    target_h: int | None = None

    lowered = resize.lower().strip()

    # Preset shorthands
    presets: dict[str, int] = {
        "720p": 1280,
        "1080p": 1920,
        "4k": 3840,
    }
    if lowered in presets:
        target_w = presets[lowered]
        target_h = None
    elif "x" in lowered:
        parts = lowered.split("x", 1)
        try:
            target_w = int(parts[0])
            target_h = int(parts[1])
        except ValueError:
            msg = f"  [yellow]warning:[/] invalid resize spec '{resize}', ignoring"
            if warnings is not None:
                warnings.append(msg)
            return None
    else:
        # Try as width only (e.g., '1280')
        try:
            target_w = int(lowered)
            target_h = None
        except ValueError:
            msg = f"  [yellow]warning:[/] invalid resize spec '{resize}', ignoring"
            if warnings is not None:
                warnings.append(msg)
            return None

    # Round to even numbers (ffmpeg requirement for many codecs)
    target_w = target_w - (target_w % 2)
    if target_h is not None:
        target_h = target_h - (target_h % 2)

    # Check if upscaling would occur and --no-upscale is set
    if no_upscale and target_w >= probe.width:
        if target_h is None or target_h >= probe.height:
            return None  # already at or below target size

    # Build the scale filter
    if target_h is not None:
        # Explicit width × height
        return f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"
    else:
        # Target width, auto height maintaining aspect ratio
        return f"scale={target_w}:-2"


def run_encode(
    cmd: list[str],
    duration: float = 0.0,
    label: str | None = None,
    progress_inline: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> tuple[bool, list[str]]:
    """Run an ffmpeg encode command. Returns True on success.

    When *duration* is provided (from ffprobe), live progress with ETA
    is printed every 1% of the file.

    When *progress_callback* is provided, it is called with (pct, speed, current_seconds)
    on each 1% milestone instead of printing inline. Used by Rich display.

    When *progress_inline* is True (legacy, sequential mode), progress is
    printed with \\r to update a single line in-place.

    When *cancel_check* is provided, it is called roughly once per second.
    If it returns True the ffmpeg process is terminated and the function
    returns (True, []) — useful for early abort when output is too large.
    """
    try:
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return False, ["  [red]error:[/] ffmpeg not found. Is it installed?"]

    # Read stderr in a background thread to prevent the pipe buffer from
    # filling up. When stderr is piped and not drained fast enough, ffmpeg
    # blocks on the write, which stalls libx265's internal pipe and causes
    # "Error sending frames to consumers: Invalid argument".
    stderr_lines: list[str] = []
    stderr_lock = threading.Lock()

    def _drain_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            with stderr_lock:
                stderr_lines.append(line)

    drainer = threading.Thread(target=_drain_stderr, daemon=True)
    drainer.start()

    # Poll stderr_lines for progress updates
    last_idx = 0
    prefix = f"{label}: " if label else ""
    end = "\r" if progress_inline else "\n"

    def _parse_progress() -> None:
        """Parse newly appended stderr lines for progress updates."""
        nonlocal last_idx
        with stderr_lock:
            new_lines = stderr_lines[last_idx:]
            last_idx = len(stderr_lines)
        for line in new_lines:
            if duration <= 0:
                continue
            m = _TIME_RE.search(line)
            if not m:
                continue
            h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            current = h * 3600 + mi * 60 + s + ms / 100.0
            pct = current / duration * 100
            sm = _SPEED_RE.search(line)
            speed = float(sm.group(1)) if sm else 0.0
            if progress_callback:
                progress_callback(pct, speed, current)
            else:
                remaining = duration - current
                if speed > 0:
                    eta_sec = remaining / speed
                    eta_str = f"eta {fmt_eta(eta_sec)}"
                else:
                    eta_str = ""
                speed_str = f"{speed:.1f}x" if speed > 0 else "?"
                print(
                    f"  {prefix}{pct}% @ {speed_str}  {eta_str}",
                    end=end,
                    flush=True,
                )

    cancelled = False
    poll_ticks = 0
    while drainer.is_alive():
        _parse_progress()
        poll_ticks += 1
        # Check cancel hook roughly once per second (sleep is 0.1 s)
        if cancel_check and poll_ticks % 10 == 0:
            try:
                if cancel_check():
                    process.terminate()
                    cancelled = True
                    break
            except Exception:
                pass  # never let a broken cancel hook crash the encode
        time.sleep(0.1)

    if not cancelled:
        # Drain any remaining lines after the thread exits
        _parse_progress()

    # Final newline to complete the inline progress line
    if progress_inline and not progress_callback:
        print(flush=True)

    drainer.join(timeout=1)
    returncode = process.wait()

    if cancelled:
        return True, []

    _write_ffmpeg_log(cmd, stderr_lines, returncode, label)

    if returncode != 0:
        logger.error(f"ffmpeg failed (rc={returncode}): {label or cmd[-1]}")
        # Return last few lines of stderr for debugging
        errors = []
        with stderr_lock:
            tail = stderr_lines[-5:] if len(stderr_lines) > 5 else stderr_lines
        for line in tail:
            errors.append(f"  [red]ffmpeg:[/] {line.rstrip()}")
        return False, errors

    return True, []


def fmt_eta(seconds: float) -> str:
    """Compact ETA string for short durations."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}h {int(m)}m"


def _write_ffmpeg_log(
    cmd: list[str],
    stderr_lines: list[str],
    returncode: int,
    label: str | None = None,
) -> None:
    """Append a single ffmpeg invocation's stderr to the ffmpeg log file."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = label or cmd[-1]  # use label or output path as identifier
    try:
        with FFMPEG_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{'=' * 72}\n")
            fh.write(f"{ts}  rc={returncode}  {header}\n")
            fh.write(f"cmd: {' '.join(cmd)}\n")
            fh.write("-" * 72 + "\n")
            fh.writelines(stderr_lines)
    except OSError:
        pass  # never crash the encode because logging failed


def format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    size: float = size_bytes
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """Human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}h {int(m)}m {int(s)}s"

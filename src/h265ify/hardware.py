"""Hardware encoder detection for h265/HEVC encoding.

Detects available hardware encoders and returns the best one for the current
platform. Priority order: Apple VideoToolbox > NVIDIA NVENC > Intel QSV > AMD AMF.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from rich.console import Console

from .logger import logger

# Encoders in priority order (best first)
_ENCODER_PRIORITY = [
    "hevc_videotoolbox",  # Apple Silicon / macOS
    "hevc_nvenc",  # NVIDIA GPUs
    "hevc_qsv",  # Intel QuickSync
    "hevc_amf",  # AMD GPUs
]

_SOFTWARE_ENCODER = "libx265"


@dataclass
class Encoder:
    """A detected h265 encoder."""

    name: str
    is_hardware: bool
    label: str  # human-readable label for progress output


def _get_available_encoders() -> set[str]:
    """Parse `ffmpeg -encoders` and return a set of available encoder names."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()

    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        # Encoder lines look like: " V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)"
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        # Encoder type indicators: V=video, A=audio, S=subtitle, etc.
        if (
            len(parts) >= 2
            and parts[0].startswith("V")
            and ("hevc" in parts[1].lower() or "265" in parts[1].lower())
        ):
            encoders.add(parts[1])
    return encoders


def detect_encoder() -> Encoder:
    """Detect the best available h265 hardware encoder, falling back to libx265.

    Returns an Encoder describing what will be used.
    """
    available = _get_available_encoders()

    for name in _ENCODER_PRIORITY:
        if name in available:
            labels: dict[str, str] = {
                "hevc_videotoolbox": "Apple VideoToolbox",
                "hevc_nvenc": "NVIDIA NVENC",
                "hevc_qsv": "Intel QuickSync",
                "hevc_amf": "AMD AMF",
            }
            encoder = Encoder(
                name=name,
                is_hardware=True,
                label=labels.get(name, name),
            )
            logger.info(f"encoder selected: {encoder.name} ({encoder.label})")
            return encoder

    if _SOFTWARE_ENCODER in available:
        encoder = Encoder(
            name=_SOFTWARE_ENCODER,
            is_hardware=False,
            label="CPU (libx265)",
        )
        logger.info(f"encoder selected: {encoder.name} ({encoder.label})")
        return encoder

    # libx265 not found either - this is bad
    logger.error("no h265 encoder found in ffmpeg")
    Console(stderr=True, highlight=False).print(
        "[red]error:[/] no h265 encoder found. Install ffmpeg with libx265 support."
    )
    sys.exit(1)


# Preset name → libx265 preset (the canonical set)
_PRESET_CHOICES = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]

# Tune values (libx265 only; hardware encoders ignore tune)
_TUNE_CHOICES = ["animation", "grain", "stillimage", "fastdecode", "zerolatency"]

# Map canonical x265 preset names → NVENC presets (p1=fastest, p7=slowest)
_NVENC_PRESET_MAP: dict[str, str] = {
    "ultrafast": "p1",
    "superfast": "p1",
    "veryfast": "p2",
    "faster": "p3",
    "fast": "p4",
    "medium": "p4",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}

# Map canonical x265 preset names → QSV presets
_QSV_PRESET_MAP: dict[str, str] = {
    "ultrafast": "veryfast",
    "superfast": "veryfast",
    "veryfast": "faster",
    "faster": "fast",
    "fast": "medium",
    "medium": "slow",
    "slow": "veryslow",
    "slower": "veryslow",
    "veryslow": "veryslow",
}

# Map canonical x265 preset names → AMF quality presets
_AMF_PRESET_MAP: dict[str, str] = {
    "ultrafast": "speed",
    "superfast": "speed",
    "veryfast": "balanced",
    "faster": "balanced",
    "fast": "balanced",
    "medium": "quality",
    "slow": "quality",
    "slower": "quality",
    "veryslow": "quality",
}


def encoder_quality_flags(
    encoder_name: str,
    crf: int,
    preset: str | None = None,
    tune: str | None = None,
) -> list[str]:
    """Return encoder-specific quality flags for a given CRF value.

    Maps the user-facing CRF value (0-51, x265 scale) to each encoder's
    native quality parameter. The goal is roughly equivalent visual quality
    across encoders at the same CRF value.

    Preset/tune follow x265 naming. Tune is libx265-only; hardware encoders ignore it.
    """
    if preset is None:
        preset = "medium"

    if encoder_name == "libx265":
        flags = ["-crf", str(crf), "-preset", preset]
        if tune:
            flags.extend(["-tune", tune])
        return flags

    if encoder_name == "hevc_videotoolbox":
        # VideoToolbox -q:v uses 0-100 scale (higher = better quality).
        # Inverted linear mapping from CRF: CRF 0 (best) → q:v 85, CRF 51 (worst) → q:v 20.
        # CRF 18 → q:v ~62, CRF 23 → q:v ~55, CRF 28 → q:v ~49.
        q = max(0, min(100, int(85 - (crf / 51) * 65)))
        return [
            "-q:v",
            str(q),
            "-realtime",
            "0",  # max quality, no realtime constraints
            "-allow_sw",
            "1",  # fall back to software if HW can't handle
        ]

    if encoder_name == "hevc_nvenc":
        # NVENC -cq targets constant quality in VBR mode; -rc vbr must be
        # set explicitly or the rate control mode defaults to something else
        # and -cq is silently ignored.
        nvenc_preset = _NVENC_PRESET_MAP.get(preset, "p4")
        return ["-rc", "vbr", "-cq", str(crf), "-preset", nvenc_preset]

    if encoder_name == "hevc_qsv":
        # QSV ICQ (Intelligent Constant Quality) uses 1-51 scale.
        # -look_ahead 1 enables la_icq for better rate control.
        qsv_preset = _QSV_PRESET_MAP.get(preset, "slow")
        return [
            "-global_quality",
            str(crf),
            "-look_ahead",
            "1",
            "-preset",
            qsv_preset,
        ]

    if encoder_name == "hevc_amf":
        # AMF has no CRF-equivalent rate control mode.
        # Use CQP (Constant QP) which maps directly: CRF ≈ QP.
        qp_p = crf
        qp_i = max(0, crf - 2)
        amf_quality = _AMF_PRESET_MAP.get(preset, "quality")
        return [
            "-rc",
            "cqp",
            "-qp_p",
            str(qp_p),
            "-qp_i",
            str(qp_i),
            "-quality",
            amf_quality,
        ]

    return []

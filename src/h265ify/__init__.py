"""h265ify — a zero-fuss h265/HEVC video encoder wrapper for ffmpeg."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from rich.console import Console

from .commands.encode import cmd_encode
from .commands.replace import cmd_replace
from .commands.report import cmd_report
from .commands.shared import (
    positive_float_gt,
    positive_int_ge,
    valid_resize,
)
from .commands.vmaf import cmd_vmaf
from .logger import generate_tag, install_excepthook, logger, set_session_tag, LOG_FILE
from .vmaf import _CLIP_DURATION, _NUM_CLIPS

__all__ = ["cmd_encode", "cmd_replace", "cmd_vmaf", "cmd_report", "valid_resize"]


def main() -> None:
    """Entry point — parse args, validate, and dispatch to subcommand."""
    parser = argparse.ArgumentParser(
        prog="h265ify",
        description=(
            '"Zero-fuss" bulk h265/HEVC re-encoding'
            " (yet another ffmpeg wrapper!)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  h265ify video.mkv                re-encode to video_h265.mkv
  h265ify ~/Videos/                re-encode all videos in directory (recursive)
  h265ify --crf 20 video.mkv       higher quality (lower CRF = better)
  h265ify --resize 720p video.mkv  shrink to 720p, preserving aspect ratio
  h265ify --yolo video.mp4         re-encode and replace original immediately
  h265ify --replace ~/Videos/      replace originals with _h265 copies (no encoding)
  h265ify --dry-run ~/Movies/      preview what would be encoded
  h265ify --preset fast video.mkv  faster encoding, slightly larger file
  h265ify --cpu video.mkv          force software encoding (libx265)
  h265ify --vmaf 95 video.mkv      evaluate and recommend optimal CRF using VMAF (no encode)
  h265ify --vmaf 93 ~/Videos/      evaluate all videos with custom VMAF target
  h265ify --vmaf 95 --sample 5     evaluate a random sample of 5 files
  h265ify --vmaf 95 --sample 25%   evaluate a random 25%% of files
  h265ify --vmaf 95 --vmaf-clips 5 --vmaf-clip-duration 10  customise VMAF sampling (5 clips, 10s each)
""",
    )

    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="video files or directories to process",
    )

    # --- Encoding options ---
    encoding = parser.add_argument_group("encoding options")
    encoding.add_argument(
        "--crf",
        type=int,
        default=None,
        metavar="N",
        help="quality: 0–51, lower = better (default: 23). "
        "For hardware encoders, mapped to the closest equivalent. "
        "Mutually exclusive with --vmaf.",
    )
    encoding.add_argument(
        "--vmaf",
        nargs="?",
        const=95.0,
        type=float,
        default=None,
        metavar="VMAF",
        help="evaluate and recommend optimal CRF using VMAF perceptual quality "
        "metric (no encoding). Probes each file at several CRF values, measures "
        "VMAF, and reports the CRF that achieves the target score (default: 95). "
        "Mutually exclusive with --crf and other encoding options.",
    )
    encoding.add_argument(
        "--sample",
        type=str,
        metavar="N|N%",
        help="randomly sample N files or N%% for --vmaf evaluation",
    )
    encoding.add_argument(
        "--vmaf-clips",
        type=lambda v: positive_int_ge(v, 1),
        default=3,
        metavar="N",
        help="number of sample clips for VMAF evaluation (default: 3)",
    )
    encoding.add_argument(
        "--vmaf-clip-duration",
        type=lambda v: positive_float_gt(v, 0.0),
        default=8.0,
        metavar="SECS",
        help="duration of each VMAF sample clip in seconds (default: 8)",
    )
    encoding.add_argument(
        "--resize",
        "-r",
        metavar="SPEC",
        help="resize output: '720p', '1080p', '4k', or '1280x720'",
    )
    encoding.add_argument(
        "--format",
        dest="output_format",
        choices=["mp4", "mkv", "mov"],
        help="force output container: mp4, mkv, or mov "
        "(default: preserve mp4/mkv/mov; convert everything else to mp4)",
    )

    # --- Output options ---
    output_grp = parser.add_argument_group("output options")
    output_grp.add_argument(
        "--yolo",
        "-y",
        action="store_true",
        help="replace original file immediately after encoding (risky!)",
    )
    output_grp.add_argument(
        "--replace",
        action="store_true",
        help="replace originals with existing '_h265' copies (no encoding)",
    )
    output_grp.add_argument(
        "--dry-run",
        "--noop",
        action="store_true",
        help="show what would happen without encoding or replacing anything",
    )
    output_grp.add_argument(
        "-P",
        "--perm",
        "--permanent",
        dest="permanent",
        action="store_true",
        help="permanently delete replaced originals instead of sending to trash",
    )

    # --- Advanced options ---
    advanced = parser.add_argument_group("advanced options")
    advanced.add_argument(
        "--preset",
        choices=[
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        ],
        default="medium",
        help="encoding speed/efficiency preset (default: medium). "
        "Mapped to hardware encoder equivalents where applicable.",
    )
    advanced.add_argument(
        "--cpu",
        action="store_true",
        help="use CPU encoding (libx265) instead of hardware acceleration",
    )
    advanced.add_argument(
        "--reencode-audio",
        action="store_true",
        help="re-encode audio (AAC for MP4, Opus for MKV) instead of stream-copy",
    )
    advanced.add_argument(
        "--no-upscale",
        action="store_true",
        help="with --resize: skip if input is already ≤ target resolution",
    )
    advanced.add_argument(
        "--halt-on-increase",
        "-H",
        action="store_true",
        help="stop the entire batch if any file comes out larger than the original",
    )
    advanced.add_argument(
        "--ignore-full-disk",
        action="store_true",
        help="skip the free-disk-space safety check before encoding",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"h265ify {version('h265ify')}",
        help="print version and exit",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="write a diagnostic report with recent logs to a file",
    )

    args = parser.parse_args()

    console = Console(highlight=False)

    # --- report mode (must run alone) ---
    if args.report:
        cmd_report(args, console)
        return

    if not args.paths:
        parser.print_usage()
        sys.exit(1)

    err_console = Console(stderr=True, highlight=False)

    # --- Install exception hook ---
    install_excepthook()

    # --- Generate session tag ---
    _tag = generate_tag()
    set_session_tag(_tag)

    # --- Log session start ---
    _ver = version("h265ify")
    logger.info(f"=== h265ify {_ver}  [{_tag}] ===")
    mode = (
        "vmaf-eval"
        if args.vmaf is not None
        else (
            "dry-run"
            if args.dry_run
            else ("replace" if args.replace else "encode")
        )
    )
    logger.info(
        f"mode={mode}  paths={', '.join(str(p) for p in args.paths)}"
    )
    logger.info(f"log: {LOG_FILE}")

    try:
        _run(args, console, err_console)
    except KeyboardInterrupt:
        console.print("\n  [yellow]interrupted[/]")
        sys.exit(130)


def _run(
    args: argparse.Namespace, console: Console, err_console: Console
) -> None:
    """Validate cross-cutting arg constraints and dispatch to subcommand."""
    # --- Mutual exclusivity ---
    if args.replace and args.yolo:
        err_console.print(
            "[red]error:[/] --replace and --yolo are mutually exclusive."
        )
        sys.exit(1)

    if args.vmaf is not None and args.replace:
        err_console.print(
            "[red]error:[/] --vmaf and --replace are mutually exclusive."
        )
        sys.exit(1)

    if args.vmaf is not None and args.yolo:
        err_console.print(
            "[red]error:[/] --vmaf and --yolo are mutually exclusive."
        )
        sys.exit(1)

    # --- --permanent requires --yolo or --replace ---
    if args.permanent and not args.replace and not args.yolo:
        err_console.print(
            "[red]error:[/] --permanent has no effect without"
            " --yolo or --replace"
        )
        sys.exit(1)

    # --- --permanent with --dry-run is nonsensical ---
    if args.permanent and args.dry_run:
        err_console.print(
            "[red]error:[/] --permanent has no effect with --dry-run"
            " (nothing is changed)"
        )
        sys.exit(1)

    # --- --sample without --vmaf is a no-op ---
    if args.sample is not None and args.vmaf is None:
        err_console.print(
            "[yellow]warning:[/] --sample has no effect without --vmaf"
        )

    # --- VMAF evaluation mode ---
    if args.vmaf is not None:
        cmd_vmaf(args, console)
        return

    # --- Replace mode ---
    if args.replace:
        cmd_replace(args, console)
        return

    # --- Encode mode (default) ---
    if args.crf is None:
        args.crf = 23

    if not (0 <= args.crf <= 51):
        err_console.print("[red]error:[/] --crf must be between 0 and 51")
        sys.exit(1)

    if args.resize and not valid_resize(args.resize):
        err_console.print(
            f"[red]error:[/] invalid --resize value '{args.resize}'"
            "  (valid: 720p, 1080p, 4k, or WxH e.g. 1280x720)"
        )
        sys.exit(1)

    # --- Warn about VMAF-only flags in encode mode ---
    if args.vmaf_clips != _NUM_CLIPS:
        err_console.print(
            "[yellow]note:[/] --vmaf-clips has no effect without --vmaf"
        )
    if args.vmaf_clip_duration != _CLIP_DURATION:
        err_console.print(
            "[yellow]note:[/] --vmaf-clip-duration has no effect"
            " without --vmaf"
        )

    cmd_encode(args, console)
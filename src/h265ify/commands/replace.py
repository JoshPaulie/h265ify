"""``--replace`` subcommand — swap originals with _h265 copies."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from ..encoder import format_size
from ..logger import LOG_FILE, get_session_tag
from ..pipeline import (
    compute_display_names,
    find_replace_pairs,
    run_replace,
)
from ..vmaf import _CLIP_DURATION, _NUM_CLIPS


def cmd_replace(args: argparse.Namespace, console: Console) -> None:
    """Replace originals with their _h265 copies."""
    # --- Warn about encoding-only flags that have no effect ---
    ignored: list[str] = []
    if args.crf is not None:
        ignored.append("--crf")
    if args.resize:
        ignored.append("--resize")
    if args.preset != "medium":
        ignored.append("--preset")
    if args.reencode_audio:
        ignored.append("--reencode-audio")
    if args.output_format:
        ignored.append("--format")
    if args.sample is not None:
        ignored.append("--sample")
    if args.vmaf_clips != _NUM_CLIPS:
        ignored.append("--vmaf-clips")
    if args.vmaf_clip_duration != _CLIP_DURATION:
        ignored.append("--vmaf-clip-duration")
    if ignored:
        console.print(
            f"[yellow]note:[/] --replace does no encoding;"
            f" ignoring: {', '.join(ignored)}"
        )

    if args.permanent and not args.dry_run:
        console.print(
            "[bold red]\u26a0  --permanent:[/] originals will be"
            " [bold red]permanently deleted[/] \u2014 not moved to trash"
        )
        console.print(f"[dim]log: {LOG_FILE}[/]  tag: {get_session_tag()}")
        console.print()
        answer = (
            console.input("[red]type 'yes' to confirm permanent deletion:[/] ")
            .strip()
            .lower()
        )
        if answer not in ("y", "yes"):
            console.print("aborted.")
            sys.exit(0)
        console.print()
    else:
        console.print(
            "[bold]replace[/]  [dim]originals \u2192 trash"
            + ("  (dry-run)" if args.dry_run else "")
            + f"  log: {LOG_FILE}[/]  tag: {get_session_tag()}"
        )
        console.print()

    pairs = find_replace_pairs(args.paths, console)

    if not pairs:
        console.print("no _h265 files with matching originals found.")
        sys.exit(0)

    console.print(f"[bold]found {len(pairs)} replacement pairs[/]\n")

    # Compute sizes before renaming (h265 files won't exist after run_replace)
    total_original = sum(p.original_path.stat().st_size for p in pairs)
    total_h265 = sum(p.h265_path.stat().st_size for p in pairs)

    all_replace_paths = [p.original_path for p in pairs] + [p.h265_path for p in pairs]
    replace_display = compute_display_names(all_replace_paths)

    replaced, skipped = run_replace(
        pairs,
        dry_run=args.dry_run,
        permanent=args.permanent,
        console=console,
        display_names=replace_display,
    )

    console.print()
    if args.dry_run:
        console.print(f"  {replaced} would be replaced")
    else:
        console.print(f"  [bold]{replaced} replaced[/], {skipped} skipped")

    # Show space savings
    if total_original > 0 and total_h265 > 0:
        saved = total_original - total_h265
        pct = (1 - total_h265 / total_original) * 100
        saved_str = format_size(saved) if saved > 0 else "0 B"
        sign = "" if saved >= 0 else "+"
        console.print(
            f"  space saved: {saved_str} ({sign}{pct:.1f}%)"
            f"  {format_size(total_original)} → {format_size(total_h265)}"
        )
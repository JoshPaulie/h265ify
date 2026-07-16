"""``--report`` subcommand — diagnostic report generation."""

from __future__ import annotations

import argparse
import platform
import re
import sys
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from rich.console import Console

from ..logger import ERROR_LOG_FILE, FFMPEG_LOG_FILE, LOG_FILE


def _dedup_consecutive(raw: list[str]) -> list[str]:
    """Collapse consecutive identical lines into a single line with a repeat count.

    ["a", "a", "a", "b", "c", "c"] → ["a (3x)", "b", "c (2x)"]
    """
    if not raw:
        return []

    result: list[str] = []
    current = raw[0]
    count = 1

    for line in raw[1:]:
        if line == current:
            count += 1
        else:
            result.append(f"{current} ({count}x)" if count > 1 else current)
            current = line
            count = 1

    result.append(f"{current} ({count}x)" if count > 1 else current)
    return result


def _append_ffmpeg_log(path: Path, lines: list[str], tail: int = 100) -> None:
    """Append the last failed encode session + recent tail of the ffmpeg log.

    The ffmpeg log is a concatenation of per-encode sessions delimited by
    ``====...====`` headers.  Extracts the last failed session first, then
    appends a recent tail, and finishes with a compact rc-status summary.
    """
    sep72 = "=" * 72

    _SIGNAL_NAMES: dict[int, str] = {
        -1: "SIGHUP (hangup)",
        -2: "SIGINT (interrupt)",
        -3: "SIGQUIT (quit)",
        -6: "SIGABRT (abort)",
        -9: "SIGKILL (killed)",
        -11: "SIGSEGV (segmentation fault — ffmpeg crashed)",
        -15: "SIGTERM (terminated)",
    }

    def _signal_name(rc: int) -> str:
        return _SIGNAL_NAMES.get(rc, f"signal {rc}")

    if not path.exists():
        lines.append("(not found)\n")
        return

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        lines.append("(empty)\n")
        return

    all_lines = content.splitlines()
    total = len(all_lines)

    # Walk sessions: find every === header followed by an rc= line
    session_starts: list[int] = []
    session_rc: dict[int, int] = {}
    session_label: dict[int, str] = {}

    for i, line in enumerate(all_lines):
        if line == sep72 and i + 1 < total:
            rc_line = all_lines[i + 1]
            if "rc=" in rc_line:
                session_starts.append(i)
                m = re.search(r"rc=(-?\d+)", rc_line)
                if m:
                    rc = int(m.group(1))
                    session_rc[i] = rc
                    parts = rc_line.split(None, 2)
                    lbl = parts[2] if len(parts) > 2 else rc_line
                    session_label[i] = lbl

    # Identify the last failed session
    fail_idx: int | None = None
    for s in reversed(session_starts):
        if session_rc.get(s, 0) != 0:
            fail_idx = s
            break

    def _session_end(start: int) -> int:
        for s in session_starts:
            if s > start:
                return s
        return total

    # Section 1: Last failed encode
    if fail_idx is not None:
        rc = session_rc.get(fail_idx, 0)
        label = session_label.get(fail_idx, "")
        fail_end = _session_end(fail_idx)
        session_lines = _dedup_consecutive(all_lines[fail_idx:fail_end])

        sig = _signal_name(rc)
        lines.append(f"\n[Last failed encode — rc={rc}  {sig}]\n")
        lines.append(f"  File: {label}\n")
        for sl in session_lines:
            lines.append(sl + "\n")
        lines.append(f"\n[End — {len(session_lines)} unique line groups]\n")

    # Section 2: Recent log tail (excluding the failed session if inside window)
    tail_start = max(0, total - tail)
    if fail_idx is not None and fail_idx >= tail_start:
        fail_end = _session_end(fail_idx)
        tail_pre: list[str] = []
        tail_post: list[str] = []
        for j in range(tail_start, total):
            if j < fail_idx:
                tail_pre.append(all_lines[j])
            elif j >= fail_end:
                tail_post.append(all_lines[j])
        if tail_pre:
            deduped = _dedup_consecutive(tail_pre)
            lines.append(
                f"\n[Recent log tail before failed session"
                f" ({len(tail_pre)} raw → {len(deduped)} groups)]\n"
            )
            for tl in deduped:
                lines.append(tl + "\n")
        if tail_post:
            deduped = _dedup_consecutive(tail_post)
            lines.append(
                f"\n[Recent log tail after failed session"
                f" ({len(tail_post)} raw → {len(deduped)} groups)]\n"
            )
            for tl in deduped:
                lines.append(tl + "\n")
    else:
        raw_tail = all_lines[tail_start:]
        deduped = _dedup_consecutive(raw_tail)
        lines.append(
            f"\n[Recent log tail ({len(raw_tail)} raw → {len(deduped)} unique groups"
            f" of {total} total lines)]\n"
        )
        for tl in deduped:
            lines.append(tl + "\n")

    # Section 3: Quick rc summary of recent sessions
    recent = session_starts[-20:]
    if recent:
        lines.append(f"\n[Recent session status ({len(recent)} sessions)]\n")
        for s in reversed(recent):
            rc = session_rc.get(s, 0)
            ts = all_lines[s + 1].split("rc=")[0].strip()
            sig = _signal_name(rc) if rc < 0 else ""
            status = "OK" if rc == 0 else "FAIL"
            lines.append(f"  {ts}  rc={rc}  {status:4s}  {sig}\n")


def _append_log_file_content(
    lines: list[str], path: Path, heading: str, max_lines: int = 100
) -> None:
    """Append deduplicated log-file contents to *lines* with a heading."""
    lines.append(f"\n--- {heading} ---\n")
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            raw_lines = content.split("\n")
            file_lines = _dedup_consecutive(raw_lines)
            raw_count = len(raw_lines)
            deduped_count = len(file_lines)
            if deduped_count > max_lines:
                lines.append(
                    f"[showing last {max_lines} of {raw_count} lines"
                    f" (deduped to {deduped_count})]\n"
                )
                file_lines = file_lines[-max_lines:]
            elif raw_count != deduped_count:
                lines.append(f"[{raw_count} lines, deduped to {deduped_count}]\n")
            for line in file_lines:
                lines.append(line + "\n")
        else:
            lines.append("(empty)\n")
    else:
        lines.append("(not found)\n")


def cmd_report(args: argparse.Namespace, console: Console) -> None:
    """Write a diagnostic report to a timestamped file."""
    # --report is standalone: reject any other non-default flags
    flag_names = [
        ("--yolo", args.yolo),
        ("--replace", args.replace),
        ("--dry-run", args.dry_run),
        ("--permanent", args.permanent),
        ("--cpu", args.cpu),
        ("--reencode-audio", args.reencode_audio),
        ("--no-upscale", args.no_upscale),
        ("--halt-on-increase", args.halt_on_increase),
        ("--resize", bool(args.resize)),
        ("--format", bool(args.output_format)),
        ("--crf", args.crf is not None),
        ("--preset", args.preset != "medium"),
    ]
    extra = [name for name, present in flag_names if present]
    if args.paths:
        extra.append("paths")

    if extra:
        console.print(
            f"[red]error:[/] --report cannot be combined with other options: "
            f"{', '.join(extra)}"
        )
        sys.exit(1)

    console.print("[bold]h265ify diagnostic report[/]\n")

    # Build report in memory
    lines: list[str] = []
    sep = "=" * 72

    # Header
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("h265ify diagnostic report\n")
    lines.append(f"Generated: {timestamp}\n")
    lines.append(f"Version:   {version('h265ify')}\n")
    lines.append(f"Python:    {sys.version}\n")
    lines.append(f"Platform:  {platform.platform()}\n")
    lines.append(f"Log dir:   {LOG_FILE.parent}\n")

    # Last exception
    if ERROR_LOG_FILE.exists():
        content = ERROR_LOG_FILE.read_text(encoding="utf-8").strip()
        if content:
            blocks = [b.strip() for b in content.split(sep) if b.strip()]
            recent = blocks[-3:] if len(blocks) > 1 else blocks
            lines.append("\n--- Last exception(s) ---\n")
            for b in recent:
                lines.append(f"{sep}\n")
                lines.append(b + "\n")
                lines.append(f"{sep}\n\n")
    else:
        lines.append("\n--- Last exception(s) ---\n(not found)\n")

    # Application log
    _append_log_file_content(
        lines, LOG_FILE, "Application log (h265ify.log)", max_lines=100
    )

    # FFmpeg log — session-aware extraction
    lines.append("\n--- FFmpeg log (h265ify_ffmpeg.log) ---\n")
    _append_ffmpeg_log(FFMPEG_LOG_FILE, lines, tail=100)

    # Write to file
    report_path = (
        LOG_FILE.parent
        / f"h265ify_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    report_str = "".join(lines)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_str, encoding="utf-8")
    except OSError as e:
        console.print(f"[red]error:[/] could not write report: {e}")
        sys.exit(1)

    console.print(f"Report written to: [bold]{report_path}[/]")
    console.print(f"[dim]({len(report_str)} bytes)[/]")

    sys.exit(0)
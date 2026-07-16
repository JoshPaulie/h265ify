"""Shared argparse validators used across CLI subcommands."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def positive_int_ge(value: str, minimum: int = 1) -> int:
    """Argparse type helper: validate int >= *minimum*."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer") from exc
    if n < minimum:
        raise argparse.ArgumentTypeError(f"'{n}' must be at least {minimum}")
    return n


def positive_float_gt(value: str, minimum: float = 0.0) -> float:
    """Argparse type helper: validate float > *minimum*."""
    try:
        n = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid number") from exc
    if n <= minimum:
        raise argparse.ArgumentTypeError(f"'{n}' must be greater than {minimum}")
    return n


def valid_resize(spec: str) -> bool:
    """Return True if *spec* is a recognised resize value."""
    lowered = spec.lower().strip()
    if lowered in ("720p", "1080p", "4k"):
        return True
    if "x" in lowered:
        try:
            w, h = lowered.split("x", 1)
            int(w)
            int(h)
            return True
        except ValueError:
            return False
    try:
        int(lowered)
        return True
    except ValueError:
        return False


def validate_sample_arg(
    args: argparse.Namespace, console: "Console"
) -> None:
    """Parse and validate ``--sample`` format into a (type, value) tuple.

    Mutates *args.sample* in place: a string like ``"25%"`` becomes
    ``("pct", 0.25)``; ``"5"`` becomes ``("count", 5)``.
    """
    if args.sample is None:
        return

    raw = args.sample.strip()
    if raw.endswith("%"):
        pct_str = raw[:-1]
        if not pct_str:
            console.print(
                f"[red]error:[/] invalid --sample value {args.sample!r}"
            )
            sys.exit(1)
        try:
            pct = float(pct_str)
        except ValueError:
            console.print(
                f"[red]error:[/] invalid --sample value {args.sample!r}"
            )
            sys.exit(1)
        if not (0 < pct <= 100):
            console.print(
                "[red]error:[/] --sample percentage must be > 0 and ≤ 100"
            )
            sys.exit(1)
        args.sample = ("pct", pct / 100.0)
    else:
        try:
            n = int(raw)
        except ValueError:
            console.print(
                f"[red]error:[/] invalid --sample value {args.sample!r}"
            )
            sys.exit(1)
        if n <= 0:
            console.print(
                "[red]error:[/] --sample must be a positive integer"
            )
            sys.exit(1)
        args.sample = ("count", n)
from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import print_summary, EncodeResult
from h265ify.probe import ProbeResult


def test_summary_dry_run() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), True, 0, 1000, 1000),
        EncodeResult(Path("in2.mp4"), Path("out2.mp4"), True, 0, 2000, 2000),
    ]
    skipped = [
        ProbeResult(Path("skip1.mp4"), True, "hevc", 1920, 1080, 10.0, 1000),
        ProbeResult(Path("skip2.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    ]
    print_summary(results, skipped, dry_run=True, console=console)
    console.print.assert_any_call("  skipped 1 already-h265 file")
    console.print.assert_any_call("  skipped 1 file (output exists)")
    console.print.assert_any_call("  2 files would be encoded  (2.9 KB)")


def test_summary_no_encodes() -> None:
    console = MagicMock()
    print_summary([], [], dry_run=False, console=console)
    console.print.assert_any_call("  0 encoded")


def test_summary_encodes_success_shrink() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), True, 10.0, 1000, 500),
    ]
    with patch("h265ify.pipeline.logger"):
        print_summary(results, [], dry_run=False, console=console)
        # 1000 -> 500 is 50%
        console.print.assert_any_call(
            "  [bold]1 encoded[/], 1000.0 B → 500.0 B  [green]-50.0%[/]"
        )


def test_summary_encodes_success_grow() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), True, 10.0, 1000, 1500),
    ]
    with patch("h265ify.pipeline.logger"):
        print_summary(results, [], dry_run=False, console=console)
        # 1000 -> 1500 is +50%
        console.print.assert_any_call(
            "  [bold]1 encoded[/], 1000.0 B → 1.5 KB  [red]+50.0%[/]"
        )


def test_summary_encodes_success_same() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), True, 10.0, 1000, 1000),
    ]
    with patch("h265ify.pipeline.logger"):
        print_summary(results, [], dry_run=False, console=console)
        console.print.assert_any_call("  [bold]1 encoded[/], 1000.0 B → 1000.0 B  ~0%")


def test_summary_encodes_failure() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), False, 10.0, 1000, 0),
    ]
    with patch("h265ify.pipeline.logger"):
        print_summary(results, [], dry_run=False, console=console)
        console.print.assert_any_call("  [red]1 failed[/]")


def test_summary_encodes_mixed() -> None:
    console = MagicMock()
    results = [
        EncodeResult(Path("in1.mp4"), Path("out1.mp4"), True, 10.0, 1000, 0),
        EncodeResult(Path("in2.mp4"), Path("out2.mp4"), False, 5.0, 1000, 0),
    ]
    with patch("h265ify.pipeline.logger"):
        print_summary(results, [], dry_run=False, console=console)
        console.print.assert_any_call("  [bold]1 encoded[/]")
        console.print.assert_any_call("  [red]1 failed[/]")

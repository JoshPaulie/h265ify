"""Tests for _append_ffmpeg_log, the session-aware ffmpeg log extractor used by --report."""

from __future__ import annotations

from pathlib import Path

import pytest

from h265ify.__init__ import _append_ffmpeg_log, _dedup_consecutive


def _make_log(tmp_path: Path, sessions: list[dict]) -> Path:
    """Build a fake ffmpeg log from session dicts.

    Each session dict::

        {"rc": 0, "cmd": "ffmpeg ...", "lines": ["frame=...", ...]}

    Sessions are concatenated with the standard === / ---- delimiters.
    """
    log_path = tmp_path / "h265ify_ffmpeg.log"
    sep_eq = "=" * 72
    sep_dash = "-" * 72
    ts = "2026-07-04 12:00:00"

    lines: list[str] = []
    for session in sessions:
        rc = session["rc"]
        cmd = session.get("cmd", "/usr/bin/ffmpeg -i input.mp4 output.mp4")
        lines.append("")
        lines.append(sep_eq)
        lines.append(f"{ts}  rc={rc}  {session['label']}")
        lines.append(f"cmd: {cmd}")
        lines.append(sep_dash)
        lines.extend(session.get("lines", []))
        ts_m = ts.rsplit(":", 1)[0]
        m = int(ts_m.split()[-1].split(":")[0]) + 1
        ts = f"2026-07-04 12:{m:02d}:00"

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def test_shows_last_failed_session(tmp_path: Path) -> None:
    """A failed encode (rc=-11) is extracted and annotated with signal name."""
    log = _make_log(tmp_path, [
        {"rc": 0, "label": "Companion.mp4"},
        {"rc": 0, "label": "Compliance.mp4"},
        {
            "rc": -11,
            "label": "Twilight.Saga.2008...h265-tmp.mp4",
            "lines": [
                "frame=137595 fps= 62 ...",
                "Segmentation fault (core dumped)",
            ],
        },
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)

    text = "\n".join(out)

    # Failed session section present
    assert "[Last failed encode — rc=-11  SIGSEGV (segmentation fault" in text
    assert "Twilight.Saga.2008" in text
    assert "Segmentation fault (core dumped)" in text
    assert "cmd:" in text  # should include the ffmpeg command


def test_no_failed_sessions(tmp_path: Path) -> None:
    """When all encodes succeeded, no 'failed encode' section appears."""
    log = _make_log(tmp_path, [
        {"rc": 0, "label": "Companion.mp4"},
        {"rc": 0, "label": "Compliance.mp4"},
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)

    text = "\n".join(out)
    assert "[Last failed encode" not in text
    assert "Recent log tail" in text


def test_multiple_failures_shows_last_only(tmp_path: Path) -> None:
    """Only the *last* failed session is extracted."""
    log = _make_log(tmp_path, [
        {"rc": -6, "label": "First.crash.mkv", "lines": ["OOM killed"]},
        {"rc": 0, "label": "Good.mp4"},
        {
            "rc": -11,
            "label": "Second.crash.mkv",
            "lines": ["Segfault"],
        },
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)

    text = "\n".join(out)
    assert "First.crash" not in text or text.index("First.crash") > text.index(
        "Second.crash"
    )
    # The last (second) failure's info comes first
    assert text.index("Second.crash") < len(text) // 2


def test_failed_session_within_tail_no_duplication(tmp_path: Path) -> None:
    """When the failed session is within the tail window, lines aren't duplicated."""
    log = _make_log(tmp_path, [
        {"rc": 0, "label": "A.mp4"},
        {"rc": 0, "label": "B.mp4"},
        {
            "rc": -11,
            "label": "C.mp4",
            "lines": ["crash output"],
        },
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=200)

    text = "\n".join(out)
    # "crash output" should appear exactly once
    assert text.count("crash output") == 1


def test_signal_name_mapping(tmp_path: Path) -> None:
    """Each known negative return code gets a descriptive signal name."""
    signals = {-1: "SIGHUP", -2: "SIGINT", -6: "SIGABRT", -9: "SIGKILL", -11: "SIGSEGV", -15: "SIGTERM"}

    for rc, name in signals.items():
        log = _make_log(tmp_path, [
            {"rc": rc, "label": f"fail_{rc}.mp4", "lines": ["error"]},
        ])
        out: list[str] = []
        _append_ffmpeg_log(log, out, tail=10)
        assert name in "\n".join(out), f"missing {name} for rc={rc}"


def test_unknown_signal(tmp_path: Path) -> None:
    """An undocumented negative return code falls back to 'signal N'."""
    log = _make_log(tmp_path, [
        {"rc": -4, "label": "weird.mp4", "lines": ["hmm"]},
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)
    assert "signal -4" in "\n".join(out)


def test_empty_log(tmp_path: Path) -> None:
    """An empty log file is handled gracefully."""
    log = tmp_path / "h265ify_ffmpeg.log"
    log.write_text("", encoding="utf-8")
    out: list[str] = []
    _append_ffmpeg_log(log, out)
    assert "(empty)" in "\n".join(out)


def test_missing_log(tmp_path: Path) -> None:
    """A missing log file is handled gracefully."""
    log = tmp_path / "does_not_exist.log"
    out: list[str] = []
    _append_ffmpeg_log(log, out)
    assert "not found" in "\n".join(out)


def test_session_status_summary(tmp_path: Path) -> None:
    """The recent-session summary shows newest-first with OK/FAIL."""
    sessions = [
        {"rc": 0, "label": "A.mp4"},
        {"rc": -11, "label": "B.mp4", "lines": ["boom"]},
        {"rc": 0, "label": "C.mp4"},
    ]
    log = _make_log(tmp_path, sessions)
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)

    text = "\n".join(out)
    assert "[Recent session status (3 sessions)]" in text
    # Newest first: rc=0 OK, rc=-11 FAIL, rc=0 OK
    lines = [l for l in text.split("\n") if "rc=" in l and ("OK" in l or "FAIL" in l)]
    ok_lines = [l for l in lines if "OK" in l]
    fail_lines = [l for l in lines if "FAIL" in l]
    assert len(ok_lines) >= 2  # two OK sessions
    assert len(fail_lines) == 1
    assert "SIGSEGV" in fail_lines[0]


# ── _dedup_consecutive ──


def test_dedup_empty() -> None:
    assert _dedup_consecutive([]) == []


def test_dedup_no_repeats() -> None:
    assert _dedup_consecutive(["a", "b", "c"]) == ["a", "b", "c"]


def test_dedup_single_element() -> None:
    assert _dedup_consecutive(["a"]) == ["a"]


def test_dedup_all_same() -> None:
    assert _dedup_consecutive(["a", "a", "a", "a"]) == ["a (4x)"]


def test_dedup_mixed() -> None:
    assert _dedup_consecutive(["a", "a", "b", "c", "c", "c"]) == [
        "a (2x)",
        "b",
        "c (3x)",
    ]


def test_dedup_repeat_at_end() -> None:
    assert _dedup_consecutive(["x", "y", "y"]) == ["x", "y (2x)"]


# ── Dedup applied to ffmpeg log output ──


def test_ffmpeg_log_dedup_repeated_lines(tmp_path: Path) -> None:
    """Repeated consecutive stderr lines in a failed session are compressed."""
    log = _make_log(tmp_path, [
        {
            "rc": -11,
            "label": "crash.mp4",
            "lines": [
                "same error",
                "same error",
                "same error",
            ],
        },
    ])
    out: list[str] = []
    _append_ffmpeg_log(log, out, tail=10)
    text = "\n".join(out)

    assert "same error (3x)" in text
    assert text.count("same error") == 1  # only the dedup'd form appears
